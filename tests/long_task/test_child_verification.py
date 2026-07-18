"""Parent receipt and verification lifecycle for persisted child submissions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import (
    ArtifactCandidate,
    CancellationRequest,
    CandidateSubmission,
    CompletionContract,
    EvidenceClaim,
    ExecutorBinding,
    ExecutorPolicy,
    GroupChildRef,
    GroupRun,
    IntentCriterion,
    IntentVersion,
    LeaseRecord,
    LongTaskState,
    ResourceLock,
    TaskAttempt,
)
from modi_harness.long_task.runtime import OperationTaskGraphRuntime, TaskGraphRuntimeError
from modi_harness.long_task.scheduler import (
    SchedulerPolicy,
    attempt_occupies_slot,
    schedule_ready_tasks,
)
from modi_harness.workflow import (
    ExecutionContract,
    OperationAdapterRegistry,
    PinnedComponent,
    PinnedComponentRegistry,
    TaskGraphLimits,
    TaskGraphNodeConfig,
)
from modi_harness.workspace import TaskArtifactStore

from .helpers import graph, task


class _UnusedBridge:
    pass


def _fixture(tmp_path, *, task_outcome: str = "passed"):
    selected = ExecutorBinding("child_agent", "worker", "sha256:template")
    base_task = replace(
        task("task-1", status="running"),
        completion_contract=CompletionContract("result-v1", ("task-v1",)),
        executor_policy=ExecutorPolicy((selected,), selected),
        active_attempt_id="attempt-1",
    )
    components = PinnedComponentRegistry()
    attempt = TaskAttempt(
        attempt_id="attempt-1",
        task_ref=base_task.ref,
        status="running",
        executor_binding=selected,
        context_manifest_ref="blob://sha256/context",
        completion_contract_hash=compute_fingerprint(
            {
                "output_schema_id": base_task.completion_contract.output_schema_id,
                "validator_ids": list(base_task.completion_contract.validator_ids),
                "required_artifact_types": list(
                    base_task.completion_contract.required_artifact_types
                ),
                "required_evidence": list(base_task.completion_contract.required_evidence),
            }
        ),
        dispatch_key="dispatch-1",
        lease=LeaseRecord(
            "root-1",
            1,
            "lease-1",
            "2026-07-18T00:00:00Z",
            resource_keys=("/workspace/result",),
        ),
        parent_execution_contract_fingerprint="sha256:parent-contract",
        child_run_id="child-1",
        context_manifest_fingerprint="sha256:manifest",
        child_template_fingerprint="sha256:template",
    )
    state = LongTaskState(
        root_run_id="root-1",
        revision=1,
        intents=(
            IntentVersion(
                "intent-1",
                1,
                "confirmed",
                "Build it",
                "Built",
                (IntentCriterion("criterion-1", "works", True, "validator"),),
            ),
        ),
        graph=graph(base_task),
        attempts=(attempt,),
        resource_locks=(
            ResourceLock("/workspace/result", attempt.attempt_id, attempt.lease.token),
        ),
    )
    config = TaskGraphNodeConfig(
        planner="planner",
        graph_policy="policy",
        context_builder="context",
        task_validators=("task-v1",),
        group_validators=(),
        criterion_validators=(),
        goal_verifier="goal",
        operation_adapters=(),
        parent_inline_components=(),
        human_task_contracts=(),
        child_templates=("worker",),
        limits=TaskGraphLimits(4, 2, 1, 1, 1),
    )
    calls: list[str] = []

    def verifier(_inputs, *, idempotency_key):
        calls.append(idempotency_key)
        return {"outcome": task_outcome, "reason": "verifier decision"}

    task_component = PinnedComponent(
            id="task-v1",
            version="1",
            kind="task_verifier",
            implementation_digest="sha256:task-v1",
            protocol_version="v1",
            input_schema_id="task-input-v1",
            output_schema_id="task-output-v1",
            supported_outcomes=("passed", "repairable", "terminal"),
            configuration={},
            implementation=verifier,
        )
    components.register(task_component)
    contract = ExecutionContract(
        snapshot={
            "task_graph": {
                "nodes": [
                    {
                        "node_id": "execute",
                        "bindings": {"task_validators": [task_component.snapshot()]},
                    }
                ]
            }
        },
        fingerprint="sha256:parent-contract",
    )
    artifact_store = TaskArtifactStore(tmp_path / "artifacts")
    runtime = OperationTaskGraphRuntime(
        root_run_id="root-1",
        node_id="execute",
        config=config,
        contract=contract,
        components=components,
        adapters=OperationAdapterRegistry(),
        dispatcher=_UnusedBridge(),  # type: ignore[arg-type]
        artifacts=artifact_store,
        state=state,
    )
    return runtime, state, base_task, attempt, calls, artifact_store


def _submission(task_value, attempt, **changes: object) -> CandidateSubmission:
    values = {
        "submission_id": "submission-1",
        "submission_sequence": 1,
        "task_ref": task_value.ref,
        "attempt_id": attempt.attempt_id,
        "child_run_id": attempt.child_run_id,
        "lease_epoch": attempt.lease.epoch,
        "lease_token": attempt.lease.token,
        "context_manifest_fingerprint": attempt.context_manifest_fingerprint,
        "completion_contract_hash": attempt.completion_contract_hash,
        "parent_execution_contract_fingerprint": (
            attempt.parent_execution_contract_fingerprint
        ),
        "outcome": "candidate_completed",
        "result": {"summary": "built"},
    }
    values.update(changes)
    return CandidateSubmission(**values)  # type: ignore[arg-type]


def test_parent_receipt_is_one_atomic_transition_and_duplicate_is_idempotent(tmp_path) -> None:
    runtime, state, task_value, attempt, _calls, _artifacts = _fixture(tmp_path)
    submission = _submission(task_value, attempt)

    receipt = runtime.receive_child_submission(submission, root_revision=2)

    committed = runtime.current_state
    assert committed is not None and committed.revision == 2
    assert receipt.status == "received"
    assert committed.receipts == (receipt,)
    assert committed.attempts[0].status == "submitted"
    assert committed.attempts[0].lease.retiring is True
    assert committed.graph is not None
    assert committed.graph.tasks[0].status == "verifying"
    assert committed.artifacts == ()
    assert committed.evidence_records == ()

    duplicate = runtime.receive_child_submission(submission, root_revision=3)
    assert duplicate == receipt
    assert runtime.current_state == committed
    assert state.revision == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"attempt_id": "other"}, "unknown Attempt"),
        ({"child_run_id": "other"}, "stale"),
        ({"lease_token": "stale"}, "stale"),
        ({"lease_epoch": 2}, "stale"),
        ({"context_manifest_fingerprint": "changed"}, "stale"),
        ({"completion_contract_hash": "changed"}, "stale"),
        ({"parent_execution_contract_fingerprint": "changed"}, "stale"),
    ],
)
def test_parent_rejects_stale_child_submission_without_receipt(
    tmp_path,
    changes: dict[str, object],
    message: str,
) -> None:
    runtime, state, task_value, attempt, _calls, _artifacts = _fixture(tmp_path)

    with pytest.raises(TaskGraphRuntimeError, match=message):
        runtime.receive_child_submission(
            _submission(task_value, attempt, **changes),
            root_revision=2,
        )

    assert runtime.current_state == state


def test_child_verifier_persists_each_semantic_boundary_and_accepts(tmp_path) -> None:
    runtime, _state, task_value, attempt, calls, artifacts = _fixture(tmp_path)
    staged = artifacts.stage(
        attempt.attempt_id,
        b'{"summary":"built"}',
        mime_type="application/json",
    )
    sealed = artifacts.seal(staged)
    submission = _submission(
        task_value,
        attempt,
        artifact_candidates=(
            ArtifactCandidate(
                uri=sealed.uri,
                content_hash=sealed.content_hash,
                size_bytes=sealed.size_bytes,
                mime_type=sealed.mime_type,
                artifact_type="result",
                schema_version="result-v1",
                visibility="task",
                producer_attempt_id=attempt.attempt_id,
                producer_child_run_id="child-1",
            ),
        ),
    )
    runtime.receive_child_submission(submission, root_revision=2)

    prepared = runtime.advance(inputs={}, root_revision=3)
    executed = runtime.advance(inputs={}, root_revision=4)
    accepted = runtime.advance(inputs={}, root_revision=5)

    assert (prepared.outcome, prepared.error) == ("running", None)
    assert (executed.outcome, executed.error) == ("running", None)
    assert (accepted.outcome, accepted.error) == ("running", None)
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert len(calls) == 1
    assert state.component_invocations[0].status == "completed"
    assert state.verification_records[0].outcome == "passed"
    assert state.receipts[0].status == "accepted"
    assert state.attempts[0].status == "completed"
    assert state.attempts[0].lease.retiring is False
    assert attempt_occupies_slot(state.attempts[0]) is False
    assert state.graph.tasks[0].status == "completed"
    assert len(state.artifacts) == 1
    assert state.resource_locks == ()
    successor = replace(task("successor"), resource_keys=("/workspace/result",))
    scheduled = schedule_ready_tasks(
        graph(successor),
        state.attempts,
        SchedulerPolicy(1),
        resource_paths_by_task={successor.ref: successor.resource_keys},
    )
    assert scheduled.selected == (successor,)


def test_repairable_verifier_resumes_same_attempt_with_new_fence(tmp_path) -> None:
    runtime, _state, task_value, attempt, calls, _artifacts = _fixture(
        tmp_path, task_outcome="repairable"
    )
    submission = _submission(task_value, attempt)
    runtime.receive_child_submission(submission, root_revision=2)
    prepared = runtime.advance(inputs={}, root_revision=3)
    verified = runtime.advance(inputs={}, root_revision=4)
    repaired_step = runtime.advance(inputs={}, root_revision=5)
    assert prepared.error is None
    assert verified.error is None
    assert repaired_step.error is None

    state = runtime.current_state
    assert state is not None and state.graph is not None
    repaired = state.attempts[0]
    assert len(calls) == 1
    assert state.receipts[0].status == "repairable"
    assert repaired.attempt_id == attempt.attempt_id
    assert repaired.child_run_id == attempt.child_run_id
    assert repaired.context_manifest_fingerprint == attempt.context_manifest_fingerprint
    assert repaired.status == "running"
    assert repaired.lease.epoch == 2
    assert repaired.lease.token != attempt.lease.token
    assert state.resource_locks[0].fencing_token == repaired.lease.token
    assert state.resource_locks[0].retiring is False
    assert state.graph.tasks[0].status == "running"
    with pytest.raises(TaskGraphRuntimeError, match="stale"):
        runtime.receive_child_submission(
            _submission(
                task_value,
                attempt,
                submission_id="submission-2",
                submission_sequence=2,
            ),
            root_revision=6,
        )
    repaired_submission = _submission(
        task_value,
        repaired,
        submission_id="submission-2",
        submission_sequence=2,
    )
    receipt = runtime.receive_child_submission(repaired_submission, root_revision=6)
    assert receipt.submission_sequence == 2


@pytest.mark.parametrize("failure", ["missing", "bad_hash", "bad_size"])
def test_candidate_blob_integrity_failure_never_runs_verifier(tmp_path, failure: str) -> None:
    runtime, _state, task_value, attempt, calls, artifacts = _fixture(tmp_path)
    if failure == "missing":
        candidate = ArtifactCandidate(
            uri=f"blob://sha256/{'a' * 64}",
            content_hash="a" * 64,
            size_bytes=1,
            mime_type=None,
            artifact_type="result",
            schema_version="v1",
            visibility="task",
            producer_attempt_id=attempt.attempt_id,
            producer_child_run_id="child-1",
        )
    else:
        sealed = artifacts.seal(artifacts.stage(attempt.attempt_id, b"{}"))
        candidate = ArtifactCandidate(
            uri=sealed.uri,
            content_hash=("b" * 64 if failure == "bad_hash" else sealed.content_hash),
            size_bytes=(sealed.size_bytes + 1 if failure == "bad_size" else sealed.size_bytes),
            mime_type=None,
            artifact_type="result",
            schema_version="v1",
            visibility="task",
            producer_attempt_id=attempt.attempt_id,
            producer_child_run_id="child-1",
        )
    runtime.receive_child_submission(
        _submission(task_value, attempt, artifact_candidates=(candidate,)),
        root_revision=2,
    )

    failed = runtime.advance(inputs={}, root_revision=3)

    assert failed.outcome == "failed"
    assert "artifact integrity failed" in str(failed.error)
    assert calls == []
    state = runtime.current_state
    assert state is not None
    assert state.artifacts == ()
    assert state.evidence_records == ()


def test_candidate_provenance_failure_creates_no_receipt(tmp_path) -> None:
    runtime, state, task_value, attempt, calls, _artifacts = _fixture(tmp_path)
    claim = EvidenceClaim(
        claim_id="claim-1",
        statement="passed",
        source_candidate_uri="blob://sha256/value",
        producer_attempt_id="other-attempt",
        producer_child_run_id="child-1",
    )

    with pytest.raises(TaskGraphRuntimeError, match="provenance mismatch"):
        runtime.receive_child_submission(
            _submission(task_value, attempt, evidence_claims=(claim,)),
            root_revision=2,
        )

    assert runtime.current_state == state
    assert calls == []


def test_late_any_success_loser_submission_is_durably_stale(tmp_path) -> None:
    runtime, state, task_value, attempt, calls, _artifacts = _fixture(tmp_path)
    winner = replace(task("winner"), status="completed", output_refs=("winner://result",))
    loser = replace(
        task_value,
        status="cancelled",
        active_attempt_id=None,
        failure="another candidate won",
    )
    group = GroupRun(
        group_id="options",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        supports=("criterion-1",),
        required=True,
        depends_on=(),
        completion_contract=CompletionContract("group-v1", ("group-v1",)),
        children=(GroupChildRef(winner.ref, True), GroupChildRef(loser.ref, True)),
        join_policy="any_success",
        failure_behavior="cancel_unneeded",
        status="completed",
        winner_task_ref=winner.ref,
        verification_record_ref="verification://winner",
    )
    retired = replace(
        attempt,
        status="cancelled",
        lease=replace(attempt.lease, retiring=True),
        failure="lost any_success Group",
    )
    graph_value = replace(
        graph(loser, winner),
        groups=(group,),
        active_group_refs=(group.ref,),
    )
    cancellation = CancellationRequest(
        cancellation_id="cancel-1",
        attempt_id=retired.attempt_id,
        reason="winner verified",
        lease_epoch=retired.lease.epoch,
        lease_token=retired.lease.token,
    )
    restored = replace(
        state,
        graph=graph_value,
        attempts=(retired,),
        cancellation_requests=(cancellation,),
    )
    runtime.current_state = restored

    receipt = runtime.receive_child_submission(
        _submission(task_value, retired),
        root_revision=2,
    )

    committed = runtime.current_state
    assert committed is not None
    assert receipt.status == receipt.decision == "stale"
    assert committed.graph == graph_value
    assert committed.attempts == (retired,)
    assert committed.verification_records == ()
    assert calls == []


def test_restore_after_verifier_record_accepts_without_reinvoking(tmp_path) -> None:
    runtime, _state, task_value, attempt, calls, artifacts = _fixture(tmp_path)
    sealed = artifacts.seal(artifacts.stage(attempt.attempt_id, b"{}"))
    submission = _submission(
        task_value,
        attempt,
        artifact_candidates=(
            ArtifactCandidate(
                sealed.uri,
                sealed.content_hash,
                sealed.size_bytes,
                None,
                "result",
                "v1",
                "task",
                attempt.attempt_id,
                "child-1",
            ),
        ),
    )
    runtime.receive_child_submission(submission, root_revision=2)
    runtime.advance(inputs={}, root_revision=3)
    runtime.advance(inputs={}, root_revision=4)
    verified_state = runtime.current_state
    assert verified_state is not None and len(calls) == 1

    restored, *_rest = _fixture(tmp_path)
    restored.current_state = verified_state
    restored._artifacts = artifacts
    accepted = restored.advance(inputs={}, root_revision=5)

    assert accepted.error is None
    assert len(calls) == 1
    state = restored.current_state
    assert state is not None
    assert state.receipts[0].status == "accepted"
    assert len(state.artifacts) == 1
