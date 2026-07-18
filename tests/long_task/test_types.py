"""Long Task persisted value round-trip tests."""

from __future__ import annotations

import json

from modi_harness.long_task import (
    ArtifactRecord,
    AuditEvent,
    CandidateReceipt,
    CriterionCoverage,
    DurableComponentInvocation,
    EvidenceRecord,
    IntentCriterion,
    IntentVersion,
    LeaseRecord,
    LongTaskState,
    TaskAttempt,
    VerificationRecord,
    long_task_state_from_snapshot,
)

from .helpers import binding, graph, task


def test_long_task_state_json_round_trip() -> None:
    first_task = task("first")
    attempt = TaskAttempt(
        attempt_id="attempt-1",
        task_ref=first_task.ref,
        status="running",
        executor_binding=binding(),
        context_manifest_ref="context://attempt-1",
        completion_contract_hash="sha256:contract",
        dispatch_key="dispatch-1",
        lease=LeaseRecord("scheduler-1", 1, "token-1", "2026-07-17T10:00:00Z"),
        parent_execution_contract_fingerprint="sha256:root-contract",
    )
    state = LongTaskState(
        root_run_id="root-1",
        revision=3,
        intents=(
            IntentVersion(
                intent_id="intent-1",
                version=1,
                status="confirmed",
                goal="Build it",
                desired_outcome="Working result",
                success_criteria=(
                    IntentCriterion("criterion-1", "It works", True, "validator", "goal-v1"),
                ),
            ),
        ),
        graph=graph(first_task),
        attempts=(attempt,),
        receipts=(CandidateReceipt("submission-1", "attempt-1", 1, "sha256:p", "received"),),
        artifacts=(
            ArtifactRecord(
                "artifact-1",
                "candidate_output",
                "blob://sha256/abc",
                "abc",
                2,
                "application/json",
                "trusted",
                "attempt-1",
            ),
        ),
        component_invocations=(
            DurableComponentInvocation(
                "invoke-1",
                "task_verifier",
                "task-v1",
                "sha256:component",
                "verify/task-1",
                "sha256:input",
                "completed",
                output_hash="sha256:output",
            ),
        ),
        verification_records=(
            VerificationRecord(
                "verify-1",
                "task",
                "task:first:1",
                "sha256:component",
                "sha256:input",
                "passed",
            ),
        ),
        evidence_records=(
            EvidenceRecord(
                "evidence-1",
                "criterion-1",
                "It works",
                "blob://sha256/source",
                "attempt-1",
                "task_verifier",
                "verified",
                "task-v1",
                "2026-07-18T00:00:00Z",
                child_run_id="child-1",
            ),
        ),
        criterion_coverage=(CriterionCoverage("criterion-1", "satisfied"),),
        events=(AuditEvent("event-1", "task_started", 3, {"task_id": "first"}),),
    )

    encoded = json.dumps(state.snapshot(), sort_keys=True)
    restored = long_task_state_from_snapshot(json.loads(encoded))

    assert restored == state
    assert restored.events[0].payload["task_id"] == "first"
