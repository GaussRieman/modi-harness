"""Crash-boundary recovery for child launch, submission, and parent acceptance."""

from __future__ import annotations

from dataclasses import replace

import pytest

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import (
    ChildAuditEvent,
    ChildDispatcher,
    ChildDispatchError,
    ChildLaunchCoordinator,
    DispatchObservation,
    InMemoryChildCheckpointStore,
    InMemoryChildExecutorBackend,
    initial_child_snapshot,
)

from .test_child_run import _binding, _manifest
from .test_child_verification import _fixture, _submission


def test_restore_after_child_creation_dispatches_once() -> None:
    binding = _binding()
    checkpoints = InMemoryChildCheckpointStore()
    checkpoints.create_or_load(initial_child_snapshot(binding, _manifest()))
    backend = InMemoryChildExecutorBackend(lambda _binding: "handle-1")

    first = ChildLaunchCoordinator(
        checkpoints=checkpoints,
        dispatcher=ChildDispatcher(backend),
    )
    started = first.ensure_started(binding)
    restored = ChildLaunchCoordinator(
        checkpoints=checkpoints,
        dispatcher=ChildDispatcher(backend),
    )

    assert restored.ensure_started(binding) == started
    assert backend.start_count == 1


def test_restore_after_lost_parent_ack_adopts_existing_dispatch() -> None:
    binding = _binding()
    checkpoints = InMemoryChildCheckpointStore()
    checkpoints.create_or_load(initial_child_snapshot(binding, _manifest()))
    observation = DispatchObservation(
        dispatch_key=binding.dispatch_key,
        binding_fingerprint=binding.fingerprint,
        child_run_id=binding.child_run_id,
        checkpoint_ns=binding.checkpoint_ns,
        handle_id="handle-1",
        status="running",
    )

    class Backend:
        start_count = 0

        def inspect(self, dispatch_key):
            assert dispatch_key == binding.dispatch_key
            return observation

        def start(self, _binding_value):
            self.start_count += 1
            return observation

        def cancel(self, dispatch_key, *, lease_token):
            del dispatch_key, lease_token
            return replace(observation, status="cancelled")

    backend = Backend()
    restored = ChildLaunchCoordinator(
        checkpoints=checkpoints,
        dispatcher=ChildDispatcher(backend),
    )

    assert restored.ensure_started(binding) == observation
    assert backend.start_count == 0
    checkpoint = checkpoints.load(binding.checkpoint_ns)
    assert checkpoint is not None and checkpoint.launch_handle_id == "handle-1"


def test_active_child_without_recoverable_observation_fails_closed() -> None:
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    checkpoints = InMemoryChildCheckpointStore()
    checkpoints.create_or_load(initial)
    checkpoints.compare_and_swap(
        binding.checkpoint_ns,
        expected_revision=0,
        snapshot=replace(
            initial,
            revision=1,
            status="running",
            launch_handle_id="lost-handle",
        ),
        event=ChildAuditEvent("started", "child_started", 1, {}),
    )
    restored = ChildLaunchCoordinator(
        checkpoints=checkpoints,
        dispatcher=ChildDispatcher(InMemoryChildExecutorBackend()),
    )

    with pytest.raises(ChildDispatchError, match="no reconcilable executor"):
        restored.ensure_started(binding)


def test_restore_after_persisted_submission_creates_one_parent_receipt(tmp_path) -> None:
    runtime, _state, task, attempt, calls, _artifacts = _fixture(tmp_path)
    submission = _submission(task, attempt)

    first = runtime.receive_child_submission(submission, root_revision=2)
    duplicate = runtime.receive_child_submission(submission, root_revision=3)

    assert duplicate == first
    assert calls == []
    assert runtime.current_state is not None
    assert len(runtime.current_state.receipts) == 1


def test_restore_after_verification_record_accepts_without_reinvocation(tmp_path) -> None:
    runtime, _state, task, attempt, calls, _artifacts = _fixture(tmp_path)
    submission = _submission(task, attempt)
    runtime.receive_child_submission(submission, root_revision=2)
    runtime.advance(inputs={}, root_revision=3)
    runtime.advance(inputs={}, root_revision=4)
    durable = runtime.current_state
    assert durable is not None and len(durable.verification_records) == 1
    assert calls

    restored, *_rest = _fixture(tmp_path / "restored")
    restored.current_state = durable
    calls_before = len(calls)
    restored.advance(inputs={}, root_revision=5)

    assert len(calls) == calls_before
    assert restored.current_state is not None
    assert restored.current_state.receipts[0].status == "accepted"


def test_orphan_child_cannot_be_adopted_by_another_attempt() -> None:
    first = _binding()
    manifest = _manifest()
    checkpoints = InMemoryChildCheckpointStore()
    checkpoints.create_or_load(initial_child_snapshot(first, manifest))
    backend = InMemoryChildExecutorBackend(lambda _binding_value: "handle-1")
    coordinator = ChildLaunchCoordinator(
        checkpoints=checkpoints,
        dispatcher=ChildDispatcher(backend),
    )
    coordinator.ensure_started(first)
    coordinator.mark_orphaned(first, reason="parent Attempt is absent")
    other = replace(
        first,
        parent_attempt_id="attempt-2",
        child_run_id="child-2",
        dispatch_key="dispatch-2",
        context_manifest_ref="context/attempt-2",
        context_manifest_fingerprint=compute_fingerprint("other"),
        checkpoint_ns=(
            "roots/root-1/nodes/execute%2Fgoal/2/attempts/attempt-2/"
            "children/child-2/workflow"
        ),
        workspace_partition="runs/root-1/sub/child-2",
        fingerprint="",
    )

    orphan = checkpoints.load(first.checkpoint_ns)
    assert orphan is not None and orphan.status == "orphaned"
    assert checkpoints.load(other.checkpoint_ns) is None
