"""Idempotent child executor dispatch and checkpoint acknowledgement."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from threading import RLock
from typing import Literal, Protocol

from .._utils import new_ulid
from .child import (
    ChildAuditEvent,
    ChildCheckpointStore,
    ChildRunBinding,
    ChildRunError,
    ChildRunSnapshot,
)
from .context import ContextManifest
from .templates import PinnedChildTemplateRegistry, ResolvedChildTemplate

DispatchStatus = Literal["absent", "running", "terminal", "cancelled", "uncertain"]


class ChildDispatchError(ChildRunError):
    """A dispatch key cannot be safely started, adopted, or cancelled."""


@dataclass(frozen=True, slots=True)
class DispatchObservation:
    dispatch_key: str
    binding_fingerprint: str
    child_run_id: str
    checkpoint_ns: str
    handle_id: str | None
    status: DispatchStatus
    detail: str | None = None


class ChildExecutorBackend(Protocol):
    def inspect(self, dispatch_key: str) -> DispatchObservation | None: ...

    def start(self, binding: ChildRunBinding) -> DispatchObservation: ...

    def cancel(self, dispatch_key: str, *, lease_token: str) -> DispatchObservation: ...


class InMemoryChildExecutorBackend:
    """Reference backend whose launcher receives one exact pinned binding."""

    def __init__(
        self,
        launcher: Callable[[ChildRunBinding], str] | None = None,
    ) -> None:
        self._launcher = launcher or (lambda _binding: new_ulid())
        self._observations: dict[str, DispatchObservation] = {}
        self._lease_tokens: dict[str, str] = {}
        self._lock = RLock()
        self.start_count = 0

    def inspect(self, dispatch_key: str) -> DispatchObservation | None:
        with self._lock:
            return self._observations.get(dispatch_key)

    def start(self, binding: ChildRunBinding) -> DispatchObservation:
        with self._lock:
            existing = self._observations.get(binding.dispatch_key)
            if existing is not None:
                _validate_observation(existing, binding)
                return existing
            handle_id = self._launcher(binding)
            if not isinstance(handle_id, str) or not handle_id.strip():
                raise ChildDispatchError("child launcher returned an invalid handle")
            observation = DispatchObservation(
                dispatch_key=binding.dispatch_key,
                binding_fingerprint=binding.fingerprint,
                child_run_id=binding.child_run_id,
                checkpoint_ns=binding.checkpoint_ns,
                handle_id=handle_id,
                status="running",
            )
            self._observations[binding.dispatch_key] = observation
            self._lease_tokens[binding.dispatch_key] = binding.lease_token
            self.start_count += 1
            return observation

    def cancel(self, dispatch_key: str, *, lease_token: str) -> DispatchObservation:
        with self._lock:
            existing = self._observations.get(dispatch_key)
            if existing is None:
                return DispatchObservation(
                    dispatch_key=dispatch_key,
                    binding_fingerprint="absent",
                    child_run_id="absent",
                    checkpoint_ns="absent",
                    handle_id=None,
                    status="absent",
                )
            if self._lease_tokens.get(dispatch_key) != lease_token:
                raise ChildDispatchError("stale lease token cannot cancel child executor")
            cancelled = replace(existing, status="cancelled")
            self._observations[dispatch_key] = cancelled
            return cancelled


class PinnedChildWorkflowBackend:
    """In-process backend that launches the exact parent-pinned Child Workflow."""

    def __init__(
        self,
        *,
        checkpoints: ChildCheckpointStore,
        templates: PinnedChildTemplateRegistry,
        template_snapshots: Mapping[str, Mapping[str, object]],
        runner: Callable[
            [ResolvedChildTemplate, ChildRunBinding, ContextManifest],
            str,
        ],
    ) -> None:
        self._checkpoints = checkpoints
        self._templates = templates
        self._template_snapshots = dict(template_snapshots)
        self._runner = runner
        self._backend = InMemoryChildExecutorBackend(self._launch)

    @property
    def start_count(self) -> int:
        return self._backend.start_count

    def inspect(self, dispatch_key: str) -> DispatchObservation | None:
        return self._backend.inspect(dispatch_key)

    def start(self, binding: ChildRunBinding) -> DispatchObservation:
        return self._backend.start(binding)

    def cancel(self, dispatch_key: str, *, lease_token: str) -> DispatchObservation:
        return self._backend.cancel(dispatch_key, lease_token=lease_token)

    def _launch(self, binding: ChildRunBinding) -> str:
        checkpoint = self._checkpoints.load(binding.checkpoint_ns)
        if checkpoint is None:
            raise ChildDispatchError("child checkpoint disappeared before launch")
        _validate_checkpoint(checkpoint, binding)
        try:
            template_snapshot = self._template_snapshots[binding.template_id]
        except KeyError as exc:
            raise ChildDispatchError(
                f"parent contract has no child template {binding.template_id!r}"
            ) from exc
        executable = self._templates.resolve_executable(template_snapshot)
        definition = executable.pinned.snapshot
        workflow = definition["child_workflow"]
        child_contract = definition["child_execution_contract"]
        if (
            executable.pinned.fingerprint != binding.template_fingerprint
            or workflow["fingerprint"] != binding.child_workflow_fingerprint
            or child_contract["fingerprint"] != binding.child_execution_contract_fingerprint
        ):
            raise ChildDispatchError("pinned child executable does not match dispatch binding")
        manifest = ContextManifest.from_snapshot(checkpoint.context_manifest)
        return self._runner(executable, binding, manifest)


class ChildDispatcher:
    def __init__(self, backend: ChildExecutorBackend) -> None:
        self._backend = backend

    def inspect(self, binding: ChildRunBinding) -> DispatchObservation | None:
        observation = self._backend.inspect(binding.dispatch_key)
        if observation is not None:
            _validate_observation(observation, binding)
        return observation

    def ensure_started(self, binding: ChildRunBinding) -> DispatchObservation:
        existing = self.inspect(binding)
        if existing is not None:
            if existing.status in {"running", "terminal"}:
                return existing
            if existing.status == "cancelled":
                raise ChildDispatchError("cancelled child executor cannot be restarted")
            raise ChildDispatchError("child executor liveness is uncertain")
        started = self._backend.start(binding)
        _validate_observation(started, binding)
        if started.status not in {"running", "terminal"}:
            raise ChildDispatchError(f"child executor start returned {started.status!r}")
        return started

    def ensure_cancelled(self, binding: ChildRunBinding) -> DispatchObservation:
        observation = self._backend.cancel(
            binding.dispatch_key,
            lease_token=binding.lease_token,
        )
        if observation.status != "absent":
            _validate_observation(observation, binding)
        return observation


class ChildLaunchCoordinator:
    """Reconcile child checkpoint creation, launch, and child-side acknowledgement."""

    def __init__(
        self,
        *,
        checkpoints: ChildCheckpointStore,
        dispatcher: ChildDispatcher,
    ) -> None:
        self._checkpoints = checkpoints
        self._dispatcher = dispatcher

    def ensure_started(self, binding: ChildRunBinding) -> DispatchObservation:
        checkpoint = self._checkpoints.load(binding.checkpoint_ns)
        if checkpoint is None:
            raise ChildDispatchError("child checkpoint must exist before dispatch")
        _validate_checkpoint(checkpoint, binding)
        if checkpoint.status in {
            "completed",
            "failed",
            "cancelled",
            "orphaned",
            "reconciliation_required",
        }:
            raise ChildDispatchError(
                f"child checkpoint in {checkpoint.status!r} cannot be dispatched"
            )
        if checkpoint.status in {"running", "waiting"}:
            observation = self._dispatcher.inspect(binding)
            if observation is None:
                raise ChildDispatchError(
                    "active child checkpoint has no reconcilable executor observation"
                )
            if observation.status != "running":
                raise ChildDispatchError(
                    f"active child executor observation is {observation.status!r}"
                )
            if checkpoint.launch_handle_id != observation.handle_id:
                raise ChildDispatchError("child launch handle changed after acknowledgement")
            return observation
        observation = self._dispatcher.ensure_started(binding)
        if observation.status == "terminal":
            raise ChildDispatchError(
                "terminal executor requires a terminal child checkpoint observation"
            )
        self._acknowledge_child_checkpoint(checkpoint, observation)
        return observation

    def mark_orphaned(self, binding: ChildRunBinding, *, reason: str) -> ChildRunSnapshot:
        checkpoint = self._checkpoints.load(binding.checkpoint_ns)
        if checkpoint is None:
            raise ChildDispatchError("cannot orphan a missing child checkpoint")
        _validate_checkpoint(checkpoint, binding)
        if checkpoint.status == "orphaned":
            return checkpoint
        observation = self._dispatcher.ensure_cancelled(binding)
        revision = checkpoint.revision + 1
        return self._checkpoints.compare_and_swap(
            binding.checkpoint_ns,
            expected_revision=checkpoint.revision,
            snapshot=replace(
                checkpoint,
                revision=revision,
                status="orphaned",
                launch_handle_id=observation.handle_id,
            ),
            event=ChildAuditEvent(
                event_id=new_ulid(),
                event_type="child_orphaned",
                child_revision=revision,
                payload={"reason": reason},
            ),
        )

    def _acknowledge_child_checkpoint(
        self,
        checkpoint: ChildRunSnapshot,
        observation: DispatchObservation,
    ) -> ChildRunSnapshot:
        if checkpoint.status == "running":
            if checkpoint.launch_handle_id != observation.handle_id:
                raise ChildDispatchError("child launch handle changed after acknowledgement")
            return checkpoint
        revision = checkpoint.revision + 1
        try:
            return self._checkpoints.compare_and_swap(
                checkpoint.binding.checkpoint_ns,
                expected_revision=checkpoint.revision,
                snapshot=replace(
                    checkpoint,
                    revision=revision,
                    status="running",
                    launch_handle_id=observation.handle_id,
                ),
                event=ChildAuditEvent(
                    event_id=new_ulid(),
                    event_type="child_started",
                    child_revision=revision,
                    payload={"handle_id": observation.handle_id},
                ),
            )
        except ChildRunError:
            current = self._checkpoints.load(checkpoint.binding.checkpoint_ns)
            if (
                current is not None
                and current.status == "running"
                and current.launch_handle_id == observation.handle_id
            ):
                return current
            raise


def _validate_checkpoint(
    checkpoint: ChildRunSnapshot,
    binding: ChildRunBinding,
) -> None:
    if checkpoint.binding.fingerprint != binding.fingerprint:
        raise ChildDispatchError("child checkpoint reciprocal binding mismatch")


def _validate_observation(
    observation: DispatchObservation,
    binding: ChildRunBinding,
) -> None:
    if observation.dispatch_key != binding.dispatch_key:
        raise ChildDispatchError("dispatcher returned another dispatch key")
    if observation.binding_fingerprint != binding.fingerprint:
        raise ChildDispatchError("dispatch key is bound to another child definition")
    if observation.child_run_id != binding.child_run_id:
        raise ChildDispatchError("dispatcher returned another child run")
    if observation.checkpoint_ns != binding.checkpoint_ns:
        raise ChildDispatchError("dispatcher returned another child namespace")


__all__ = [
    "ChildDispatchError",
    "ChildDispatcher",
    "ChildExecutorBackend",
    "ChildLaunchCoordinator",
    "DispatchObservation",
    "DispatchStatus",
    "InMemoryChildExecutorBackend",
    "PinnedChildWorkflowBackend",
]
