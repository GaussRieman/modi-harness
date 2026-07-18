"""Checkpoint-driven execution of one exact pinned child Workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast

from .._utils import new_ulid
from ..api._session_helpers import agent_to_profile
from ..brain import DefaultBrain
from ..brain.model import ModelStructuredPlanner
from ..workflow.contract import CompletionValidatorRegistry, OperationAdapterRegistry
from ..workflow.runtime import (
    InMemoryWorkflowStore,
    InvocationRecord,
    PendingOperation,
    TransitionRecord,
    WorkflowRuntime,
    WorkflowState,
)
from ..workspace import TaskArtifactStore, WorkspaceManager
from .child import (
    ChildAuditEvent,
    ChildCheckpointStore,
    ChildRunBinding,
    ChildRunSnapshot,
    persist_child_submission,
    prepare_child_run,
)
from .context import ContextManifest
from .submission import CandidateSubmission
from .templates import PinnedChildTemplateRegistry, ResolvedChildTemplate
from .types import LongTaskState, TaskAttempt, TaskRun


class ChildRuntimeError(RuntimeError):
    """A pinned child Workflow cannot be created, restored, or advanced."""


class SessionChildRuntime:
    """Pull-based child driver; each call commits at most one child semantic step."""

    def __init__(
        self,
        *,
        checkpoints: ChildCheckpointStore,
        templates: PinnedChildTemplateRegistry,
        template_snapshots: Mapping[str, Mapping[str, Any]],
        workspace: WorkspaceManager,
        artifacts: TaskArtifactStore,
        adapters: OperationAdapterRegistry,
        dispatcher_factory: Callable[
            [ResolvedChildTemplate, ChildRunBinding, ContextManifest],
            Any,
        ],
        model: Any,
        tool_catalog: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self._checkpoints = checkpoints
        self._templates = templates
        self._template_snapshots = dict(template_snapshots)
        self._workspace = workspace
        self._artifacts = artifacts
        self._adapters = adapters
        self._dispatcher_factory = dispatcher_factory
        self._model = model
        self._tool_catalog = dict(tool_catalog)

    def prepare_child(
        self,
        *,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
    ) -> tuple[int, str]:
        del state, task
        binding, manifest = self._binding_and_manifest(attempt)
        checkpoint, _partition = prepare_child_run(
            checkpoints=self._checkpoints,
            workspace=self._workspace,
            binding=binding,
            manifest=manifest,
        )
        if checkpoint.workflow_state:
            return checkpoint.revision, checkpoint.status
        executable = self._resolve_executable(binding)
        dispatcher = self._dispatcher_factory(executable, binding, manifest)
        runtime = self._runtime(executable, dispatcher)
        child = runtime.start(
            workflow=executable.workflow,
            contract=executable.execution_contract,
            workflow_input={"context_manifest": manifest.snapshot()},
            run_id=binding.child_run_id,
        )
        started = self._commit_child(
            checkpoint,
            status="running",
            workflow_state=self._runtime_snapshot(runtime, child, dispatcher),
            event_type="child_workflow_started",
        )
        return started.revision, started.status

    def advance_child(self, attempt: TaskAttempt) -> CandidateSubmission | None:
        binding, manifest = self._binding_and_manifest(attempt)
        checkpoint = self._checkpoints.load(binding.checkpoint_ns)
        if checkpoint is None:
            raise ChildRuntimeError("child checkpoint disappeared after parent acknowledgement")
        self._validate_checkpoint_binding(checkpoint, binding)
        repair_ack = self._latest_repair_ack(checkpoint)
        if checkpoint.submissions and repair_ack is None:
            return checkpoint.submissions[-1]
        if checkpoint.status in {"failed", "cancelled", "orphaned", "reconciliation_required"}:
            raise ChildRuntimeError(f"child Workflow is terminal with status {checkpoint.status!r}")
        executable = self._resolve_executable(binding)
        dispatcher = self._dispatcher_factory(executable, binding, manifest)
        if repair_ack is not None and checkpoint.status == "completed":
            runtime = self._runtime(executable, dispatcher)
            child = runtime.start(
                workflow=executable.workflow,
                contract=executable.execution_contract,
                workflow_input={
                    "context_manifest": manifest.snapshot(),
                    "repair_feedback": {
                        "prior_submission_id": repair_ack.submission_id,
                        "reason": repair_ack.reason,
                        "lease_epoch": binding.lease_epoch,
                    },
                },
                run_id=binding.child_run_id,
            )
            self._commit_child(
                checkpoint,
                status="running",
                workflow_state=self._runtime_snapshot(runtime, child, dispatcher),
                event_type="child_workflow_repair_started",
            )
            return None
        runtime, child = self._restore_runtime(executable, checkpoint, dispatcher)
        if child.status == "running":
            child = runtime.advance(
                child.run_id,
                workflow=executable.workflow,
                contract=executable.execution_contract,
            )
        if child.status == "waiting":
            self._commit_child(
                checkpoint,
                status="waiting",
                workflow_state=self._runtime_snapshot(runtime, child, dispatcher),
                event_type="child_workflow_waiting",
            )
            return None
        if child.status == "running":
            self._commit_child(
                checkpoint,
                status="running",
                workflow_state=self._runtime_snapshot(runtime, child, dispatcher),
                event_type="child_workflow_progressed",
            )
            return None
        if child.status != "completed" or not isinstance(child.output, Mapping):
            self._commit_child(
                checkpoint,
                status="failed",
                workflow_state=self._runtime_snapshot(runtime, child, dispatcher),
                event_type="child_workflow_failed",
            )
            raise ChildRuntimeError(child.failure or "child Workflow failed")
        completed = checkpoint
        if checkpoint.status != "completed":
            completed = self._commit_child(
                checkpoint,
                status="completed",
                workflow_state=self._runtime_snapshot(runtime, child, dispatcher),
                event_type="child_workflow_completed",
            )
        submission = CandidateSubmission(
            submission_id=new_ulid(),
            submission_sequence=len(completed.submissions) + 1,
            task_ref=attempt.task_ref,
            attempt_id=attempt.attempt_id,
            child_run_id=cast(str, attempt.child_run_id),
            lease_epoch=attempt.lease.epoch,
            lease_token=attempt.lease.token,
            context_manifest_fingerprint=cast(str, attempt.context_manifest_fingerprint),
            completion_contract_hash=attempt.completion_contract_hash,
            parent_execution_contract_fingerprint=(
                attempt.parent_execution_contract_fingerprint
            ),
            outcome="candidate_completed",
            result=dict(child.output),
        )
        persist_child_submission(self._checkpoints, submission)
        return submission

    @staticmethod
    def _validate_checkpoint_binding(
        checkpoint: ChildRunSnapshot,
        binding: ChildRunBinding,
    ) -> None:
        ignored = {"fingerprint", "lease_epoch", "lease_token"}
        stored = {
            key: value
            for key, value in checkpoint.binding.snapshot().items()
            if key not in ignored
        }
        current = {
            key: value
            for key, value in binding.snapshot().items()
            if key not in ignored
        }
        if stored != current:
            raise ChildRuntimeError("child checkpoint binding changed during recovery")
        lease = checkpoint.active_lease
        if lease is None or (lease.epoch, lease.token) != (
            binding.lease_epoch,
            binding.lease_token,
        ):
            raise ChildRuntimeError("child checkpoint active lease is stale")

    @staticmethod
    def _latest_repair_ack(checkpoint: ChildRunSnapshot) -> Any | None:
        if not checkpoint.submissions:
            return None
        submission_id = checkpoint.submissions[-1].submission_id
        acknowledgement = next(
            (
                item
                for item in checkpoint.delivery_acks
                if item.submission_id == submission_id
            ),
            None,
        )
        if acknowledgement is None or acknowledgement.decision != "repairable":
            return None
        return acknowledgement

    def cancel_child(
        self,
        attempt: TaskAttempt,
        *,
        reason: str,
    ) -> CandidateSubmission | None:
        binding, _manifest = self._binding_and_manifest(attempt)
        checkpoint = self._checkpoints.load(binding.checkpoint_ns)
        if checkpoint is None:
            return None
        if checkpoint.submissions:
            return checkpoint.submissions[-1]
        if checkpoint.status in {
            "completed",
            "failed",
            "cancelled",
            "orphaned",
        }:
            return None
        self._commit_child(
            checkpoint,
            status="cancelled",
            workflow_state={**checkpoint.workflow_state, "cancellation_reason": reason},
            event_type="child_workflow_cancelled",
        )
        return None

    def _binding_and_manifest(
        self,
        attempt: TaskAttempt,
    ) -> tuple[ChildRunBinding, ContextManifest]:
        required = {
            "child_run_id": attempt.child_run_id,
            "child_checkpoint_ns": attempt.child_checkpoint_ns,
            "child_workflow_fingerprint": attempt.child_workflow_fingerprint,
            "child_execution_contract_fingerprint": (
                attempt.child_execution_contract_fingerprint
            ),
            "context_manifest_fingerprint": attempt.context_manifest_fingerprint,
            "child_template_fingerprint": attempt.child_template_fingerprint,
            "parent_node_id": attempt.parent_node_id,
            "parent_node_attempt": attempt.parent_node_attempt,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ChildRuntimeError(
                f"child Attempt is missing durable binding fields: {', '.join(missing)}"
            )
        manifest = ContextManifest.from_snapshot(
            cast(Mapping[str, Any], self._read_json(attempt.context_manifest_ref))
        )
        binding = ChildRunBinding(
            root_run_id=attempt.lease.owner_id,
            parent_run_id=attempt.lease.owner_id,
            parent_node_id=cast(str, attempt.parent_node_id),
            parent_node_attempt=cast(int, attempt.parent_node_attempt),
            parent_attempt_id=attempt.attempt_id,
            child_run_id=cast(str, attempt.child_run_id),
            template_id=attempt.executor_binding.id,
            template_fingerprint=cast(str, attempt.child_template_fingerprint),
            dispatch_key=attempt.dispatch_key,
            context_manifest_ref=attempt.context_manifest_ref,
            context_manifest_fingerprint=cast(str, attempt.context_manifest_fingerprint),
            parent_execution_contract_fingerprint=(
                attempt.parent_execution_contract_fingerprint
            ),
            child_workflow_fingerprint=cast(str, attempt.child_workflow_fingerprint),
            child_execution_contract_fingerprint=cast(
                str, attempt.child_execution_contract_fingerprint
            ),
            lease_epoch=attempt.lease.epoch,
            lease_token=attempt.lease.token,
            checkpoint_ns=cast(str, attempt.child_checkpoint_ns),
            workspace_partition=(
                f"runs/{attempt.lease.owner_id}/sub/{attempt.child_run_id}"
            ),
        )
        if manifest.fingerprint != binding.context_manifest_fingerprint:
            raise ChildRuntimeError("parent Attempt ContextManifest fingerprint is stale")
        return binding, manifest

    def _resolve_executable(self, binding: ChildRunBinding) -> ResolvedChildTemplate:
        try:
            snapshot = self._template_snapshots[binding.template_id]
        except KeyError as exc:
            raise ChildRuntimeError(
                f"parent contract has no child template {binding.template_id!r}"
            ) from exc
        executable = self._templates.resolve_executable(snapshot)
        if executable.pinned.fingerprint != binding.template_fingerprint:
            raise ChildRuntimeError("pinned child executable changed during recovery")
        return executable

    def _runtime(self, executable: ResolvedChildTemplate, dispatcher: Any) -> WorkflowRuntime:
        validators = CompletionValidatorRegistry()
        for validator in executable.agent.completion_validators:
            validators.register(validator)
        profile = agent_to_profile(executable.agent)
        planner = ModelStructuredPlanner(
            model=self._model,
            instruction=executable.agent.instruction,
            tool_catalog=self._tool_catalog,
            skill_instructions=[
                skill.profile["instruction"] for skill in executable.agent.skills
            ],
        )
        return WorkflowRuntime(
            adapters=self._adapters,
            validators=validators,
            dispatcher=dispatcher,
            store=InMemoryWorkflowStore(),
            brain=DefaultBrain(planner),
            agent_profile=profile,
        )

    def _restore_runtime(
        self,
        executable: ResolvedChildTemplate,
        checkpoint: ChildRunSnapshot,
        dispatcher: Any,
    ) -> tuple[WorkflowRuntime, WorkflowState]:
        runtime = self._runtime(executable, dispatcher)
        raw = checkpoint.workflow_state
        state_raw = raw.get("state") if isinstance(raw.get("state"), Mapping) else raw
        child = self._state_from_snapshot(cast(Mapping[str, Any], state_raw))
        runtime.store.create(child)
        for item in raw.get("invocations") or ():
            invocation = cast(Mapping[str, Any], item)
            runtime.store.restore_invocation(
                InvocationRecord(
                    id=str(invocation["id"]),
                    run_id=str(invocation["run_id"]),
                    node_id=str(invocation["node_id"]),
                    node_attempt=int(invocation["node_attempt"]),
                    adapter_id=str(invocation["adapter_id"]),
                    arguments=MappingProxyType(dict(invocation.get("arguments") or {})),
                    workflow_revision=int(invocation["workflow_revision"]),
                    status=cast(Any, invocation["status"]),
                    output=invocation.get("output"),
                    error=cast(str | None, invocation.get("error")),
                )
            )
        if hasattr(dispatcher, "records"):
            dispatcher.records.extend(dict(item) for item in raw.get("operation_records") or ())
        if hasattr(dispatcher, "denied_actions"):
            dispatcher.denied_actions.extend(
                dict(item) for item in raw.get("denied_actions") or ()
            )
        return runtime, child

    @classmethod
    def _runtime_snapshot(
        cls,
        runtime: WorkflowRuntime,
        state: WorkflowState,
        dispatcher: Any,
    ) -> dict[str, Any]:
        return {
            "state": cls._state_snapshot(state),
            "invocations": [
                {
                    "id": item.id,
                    "run_id": item.run_id,
                    "node_id": item.node_id,
                    "node_attempt": item.node_attempt,
                    "adapter_id": item.adapter_id,
                    "arguments": _plain(item.arguments),
                    "workflow_revision": item.workflow_revision,
                    "status": item.status,
                    "output": _plain(item.output),
                    "error": item.error,
                }
                for item in runtime.store.invocations(state.run_id)
            ],
            "operation_records": _plain(getattr(dispatcher, "records", ())),
            "denied_actions": _plain(getattr(dispatcher, "denied_actions", ())),
        }

    def _commit_child(
        self,
        checkpoint: ChildRunSnapshot,
        *,
        status: str,
        workflow_state: Mapping[str, Any],
        event_type: str,
    ) -> ChildRunSnapshot:
        revision = checkpoint.revision + 1
        return self._checkpoints.compare_and_swap(
            checkpoint.binding.checkpoint_ns,
            expected_revision=checkpoint.revision,
            snapshot=replace(
                checkpoint,
                revision=revision,
                status=cast(Any, status),
                workflow_state=workflow_state,
            ),
            event=ChildAuditEvent(new_ulid(), event_type, revision, {}),
        )

    def _read_json(self, uri: str) -> Any:
        import json

        return json.loads(self._artifacts.read_uri_verified(uri))

    @staticmethod
    def _state_snapshot(state: WorkflowState) -> dict[str, Any]:
        return {
            "run_id": state.run_id,
            "workflow_id": state.workflow_id,
            "definition_fingerprint": state.definition_fingerprint,
            "execution_contract_fingerprint": state.execution_contract_fingerprint,
            "workflow_input": _plain(state.workflow_input),
            "status": state.status,
            "current_node_id": state.current_node_id,
            "node_attempt": state.node_attempt,
            "revision": state.revision,
            "transition_count": state.transition_count,
            "node_outputs": _plain(state.node_outputs),
            "transitions": [_plain(item) for item in state.transitions],
            "output": _plain(state.output),
            "failure": state.failure,
            "cancellation_requested": state.cancellation_requested,
            "loop_state": _plain(state.loop_state),
            "step_records": _plain(state.step_records),
            "task_plan": _plain(state.task_plan),
            "pending_operation": _plain(state.pending_operation),
            "human_inputs": _plain(state.human_inputs),
        }

    @staticmethod
    def _state_from_snapshot(raw: Mapping[str, Any]) -> WorkflowState:
        pending = raw.get("pending_operation")
        return WorkflowState(
            run_id=str(raw["run_id"]),
            workflow_id=str(raw["workflow_id"]),
            definition_fingerprint=str(raw["definition_fingerprint"]),
            execution_contract_fingerprint=str(raw["execution_contract_fingerprint"]),
            workflow_input=MappingProxyType(dict(cast(Mapping[str, Any], raw["workflow_input"]))),
            status=cast(Any, raw["status"]),
            current_node_id=str(raw["current_node_id"]),
            node_attempt=int(raw["node_attempt"]),
            revision=int(raw["revision"]),
            transition_count=int(raw["transition_count"]),
            node_outputs=MappingProxyType(dict(cast(Mapping[str, Any], raw["node_outputs"]))),
            transitions=tuple(
                TransitionRecord(**item)
                for item in cast(list[dict[str, Any]], raw.get("transitions", []))
            ),
            output=raw.get("output"),
            failure=cast(str | None, raw.get("failure")),
            cancellation_requested=bool(raw.get("cancellation_requested", False)),
            loop_state=cast(Any, raw.get("loop_state")),
            step_records=tuple(cast(Any, raw.get("step_records", ()))),
            task_plan=cast(Any, raw.get("task_plan")),
            pending_operation=(
                PendingOperation(**pending) if isinstance(pending, Mapping) else None
            ),
            human_inputs=MappingProxyType(dict(cast(Mapping[str, Any], raw.get("human_inputs", {})))),
        )


def _plain(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


__all__ = ["ChildRuntimeError", "SessionChildRuntime"]
