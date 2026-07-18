"""Closed CandidateSubmission values and child delivery identity tests."""

from __future__ import annotations

import json

import pytest

from modi_harness.long_task import (
    ArtifactCandidate,
    CandidateSubmission,
    ChildCheckpointConflict,
    ChildRunError,
    DependencyRef,
    EvidenceClaim,
    InMemoryChildCheckpointStore,
    SubmissionDeliveryAck,
    SubmissionError,
    acknowledge_child_submission,
    initial_child_snapshot,
    persist_child_submission,
)

from .test_child_run import _binding, _manifest


def _submission(**changes: object) -> CandidateSubmission:
    values = {
        "submission_id": "submission-1",
        "submission_sequence": 1,
        "task_ref": DependencyRef("task", "build", 2),
        "attempt_id": "attempt-1",
        "child_run_id": "child-1",
        "lease_epoch": 1,
        "lease_token": "lease-1",
        "context_manifest_fingerprint": "sha256:manifest",
        "completion_contract_hash": "sha256:completion",
        "parent_execution_contract_fingerprint": "sha256:parent-contract",
        "outcome": "candidate_completed",
        "result": {"summary": "built", "structured_output": {"ok": True}},
        "artifact_candidates": (
            ArtifactCandidate(
                uri=f"blob://sha256/{'a' * 64}",
                content_hash="a" * 64,
                size_bytes=2,
                mime_type="application/json",
                artifact_type="result",
                schema_version="result-v1",
                visibility="task",
                producer_attempt_id="attempt-1",
                producer_child_run_id="child-1",
            ),
        ),
        "evidence_claims": (
            EvidenceClaim(
                claim_id="claim-1",
                statement="Build passed",
                source_candidate_uri=f"blob://sha256/{'a' * 64}",
                producer_attempt_id="attempt-1",
                producer_child_run_id="child-1",
            ),
        ),
    }
    values.update(changes)
    return CandidateSubmission(**values)  # type: ignore[arg-type]


def test_candidate_submission_is_closed_hashed_and_round_trips() -> None:
    submission = _submission()
    restored = CandidateSubmission.from_snapshot(
        json.loads(json.dumps(submission.snapshot()))
    )

    assert restored == submission
    assert submission.payload_hash
    assert _submission(lease_token="lease-2").payload_hash != submission.payload_hash
    assert _submission(result={"summary": "changed"}).payload_hash != submission.payload_hash


def test_evidence_claim_cannot_write_parent_verification_fields() -> None:
    raw = {
        "claim_id": "claim-1",
        "statement": "passed",
        "source_candidate_uri": "blob://sha256/value",
        "producer_attempt_id": "attempt-1",
        "producer_child_run_id": "child-1",
        "visibility": "task",
        "verification_status": "verified",
    }

    with pytest.raises(SubmissionError, match="unknown verification_status"):
        EvidenceClaim.from_snapshot(raw)


def test_candidate_artifact_visibility_is_closed() -> None:
    with pytest.raises(SubmissionError, match="unsupported artifact visibility"):
        _submission(
            artifact_candidates=(
                ArtifactCandidate(
                    uri="blob://sha256/value",
                    content_hash="a" * 64,
                    size_bytes=1,
                    mime_type=None,
                    artifact_type="result",
                    schema_version="v1",
                    visibility="public",  # type: ignore[arg-type]
                    producer_attempt_id="attempt-1",
                    producer_child_run_id="child-1",
                ),
            )
        )


def test_child_persists_submission_idempotently_before_delivery() -> None:
    binding = _binding()
    manifest = _manifest()
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial_child_snapshot(binding, manifest))
    submission = _submission(
        task_ref=DependencyRef("task", "task-1", 1),
        context_manifest_fingerprint=manifest.fingerprint,
        parent_execution_contract_fingerprint=(
            binding.parent_execution_contract_fingerprint
        ),
    )

    first = persist_child_submission(store, submission)
    second = persist_child_submission(store, submission)

    assert first == second
    assert first.submissions == (submission,)
    assert first.revision == 1


def test_child_submission_conflict_gap_and_repair_lease_rules() -> None:
    binding = _binding()
    manifest = _manifest()
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial_child_snapshot(binding, manifest))

    def candidate(sequence: int, submission_id: str, **changes: object) -> CandidateSubmission:
        values = {
            "submission_id": submission_id,
            "submission_sequence": sequence,
            "task_ref": DependencyRef("task", "task-1", 1),
            "attempt_id": binding.parent_attempt_id,
            "child_run_id": binding.child_run_id,
            "lease_epoch": binding.lease_epoch,
            "lease_token": binding.lease_token,
            "context_manifest_fingerprint": manifest.fingerprint,
            "completion_contract_hash": "sha256:completion",
            "parent_execution_contract_fingerprint": (
                binding.parent_execution_contract_fingerprint
            ),
        }
        values.update(changes)
        return _submission(**values)

    first = candidate(1, "submission-1")
    persist_child_submission(store, first)
    with pytest.raises(ChildCheckpointConflict, match="different content"):
        persist_child_submission(
            store,
            candidate(1, "submission-1", result={"summary": "changed"}),
        )
    with pytest.raises(ChildCheckpointConflict, match="sequence already belongs"):
        persist_child_submission(store, candidate(1, "submission-other"))
    with pytest.raises(ChildCheckpointConflict, match="sequence gap"):
        persist_child_submission(store, candidate(3, "submission-3"))
    with pytest.raises(ChildRunError, match="repairable parent decision"):
        persist_child_submission(store, candidate(2, "submission-2"))

    acknowledged = acknowledge_child_submission(
        store,
        binding.child_run_id,
        SubmissionDeliveryAck(
            submission_id=first.submission_id,
            payload_hash=first.payload_hash,
            decision="repairable",
            receipt_status="repairable",
            lease_epoch=2,
            lease_token="lease-2",
        ),
    )
    assert acknowledged.active_lease is not None
    assert acknowledged.active_lease.epoch == 2
    with pytest.raises(ChildRunError, match="stale child lease"):
        persist_child_submission(store, candidate(2, "submission-2"))
    repaired = candidate(
        2,
        "submission-2",
        lease_epoch=2,
        lease_token="lease-2",
    )
    persisted = persist_child_submission(store, repaired)
    assert persisted.submissions == (first, repaired)
