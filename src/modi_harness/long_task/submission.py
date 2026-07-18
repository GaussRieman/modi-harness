"""Immutable child candidate values and idempotent submission identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from .._utils import compute_fingerprint
from .types import DependencyRef

SubmissionOutcome = Literal["candidate_completed", "needs_followup", "blocked", "failed"]
CandidateVisibility = Literal["task", "graph", "workflow"]
DeliveryDecision = Literal["pending", "received", "accepted", "repairable", "rejected", "stale"]


class SubmissionError(ValueError):
    """A candidate value or sequence violates the durable submission contract."""


@dataclass(frozen=True, slots=True)
class ArtifactCandidate:
    uri: str
    content_hash: str
    size_bytes: int
    mime_type: str | None
    artifact_type: str
    schema_version: str
    visibility: CandidateVisibility
    producer_attempt_id: str
    producer_child_run_id: str

    def __post_init__(self) -> None:
        for name in (
            "uri",
            "content_hash",
            "artifact_type",
            "schema_version",
            "producer_attempt_id",
            "producer_child_run_id",
        ):
            _nonempty(getattr(self, name), name)
        if self.visibility not in {"task", "graph", "workflow"}:
            raise SubmissionError(f"unsupported artifact visibility {self.visibility!r}")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise SubmissionError("artifact size_bytes must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    claim_id: str
    statement: str
    source_candidate_uri: str
    producer_attempt_id: str
    producer_child_run_id: str
    visibility: CandidateVisibility = "task"

    def __post_init__(self) -> None:
        for name in (
            "claim_id",
            "statement",
            "source_candidate_uri",
            "producer_attempt_id",
            "producer_child_run_id",
        ):
            _nonempty(getattr(self, name), name)
        if self.visibility not in {"task", "graph", "workflow"}:
            raise SubmissionError(f"unsupported evidence visibility {self.visibility!r}")

    @classmethod
    def from_snapshot(cls, raw: Mapping[str, Any]) -> EvidenceClaim:
        _exact_fields(
            raw,
            "EvidenceClaim",
            {
                "claim_id",
                "statement",
                "source_candidate_uri",
                "producer_attempt_id",
                "producer_child_run_id",
                "visibility",
            },
        )
        return cls(
            claim_id=_string(raw, "claim_id"),
            statement=_string(raw, "statement"),
            source_candidate_uri=_string(raw, "source_candidate_uri"),
            producer_attempt_id=_string(raw, "producer_attempt_id"),
            producer_child_run_id=_string(raw, "producer_child_run_id"),
            visibility=cast(CandidateVisibility, _string(raw, "visibility")),
        )


@dataclass(frozen=True, slots=True)
class CandidateSubmission:
    submission_id: str
    submission_sequence: int
    task_ref: DependencyRef
    attempt_id: str
    child_run_id: str
    lease_epoch: int
    lease_token: str
    context_manifest_fingerprint: str
    completion_contract_hash: str
    parent_execution_contract_fingerprint: str
    outcome: SubmissionOutcome
    result: Mapping[str, Any]
    artifact_candidates: tuple[ArtifactCandidate, ...] = ()
    evidence_claims: tuple[EvidenceClaim, ...] = ()
    discovered_work: tuple[Mapping[str, Any], ...] = ()
    failure: str | None = None
    payload_hash: str = ""
    schema_version: str = "candidate-submission-v1"

    def __post_init__(self) -> None:
        for name in (
            "submission_id",
            "attempt_id",
            "child_run_id",
            "lease_token",
            "context_manifest_fingerprint",
            "completion_contract_hash",
            "parent_execution_contract_fingerprint",
            "schema_version",
        ):
            _nonempty(getattr(self, name), name)
        for name in ("submission_sequence", "lease_epoch"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise SubmissionError(f"{name} must be a positive integer")
        if self.outcome not in {"candidate_completed", "needs_followup", "blocked", "failed"}:
            raise SubmissionError(f"unsupported submission outcome {self.outcome!r}")
        object.__setattr__(self, "result", _freeze_mapping(self.result))
        object.__setattr__(
            self,
            "artifact_candidates",
            tuple(sorted(self.artifact_candidates, key=lambda item: item.uri)),
        )
        object.__setattr__(
            self,
            "evidence_claims",
            tuple(sorted(self.evidence_claims, key=lambda item: item.claim_id)),
        )
        object.__setattr__(
            self,
            "discovered_work",
            tuple(_freeze_mapping(item) for item in self.discovered_work),
        )
        expected = compute_fingerprint(self._payload())
        if self.payload_hash and self.payload_hash != expected:
            raise SubmissionError("CandidateSubmission payload_hash does not match content")
        object.__setattr__(self, "payload_hash", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "submission_id": self.submission_id,
            "submission_sequence": self.submission_sequence,
            "task_ref": _plain(self.task_ref),
            "attempt_id": self.attempt_id,
            "child_run_id": self.child_run_id,
            "lease_epoch": self.lease_epoch,
            "lease_token": self.lease_token,
            "context_manifest_fingerprint": self.context_manifest_fingerprint,
            "completion_contract_hash": self.completion_contract_hash,
            "parent_execution_contract_fingerprint": self.parent_execution_contract_fingerprint,
            "outcome": self.outcome,
            "result": _plain(self.result),
            "artifact_candidates": [_plain(item) for item in self.artifact_candidates],
            "evidence_claims": [_plain(item) for item in self.evidence_claims],
            "discovered_work": [_plain(item) for item in self.discovered_work],
            "failure": self.failure,
        }

    def snapshot(self) -> dict[str, Any]:
        return {**self._payload(), "payload_hash": self.payload_hash}

    @classmethod
    def from_snapshot(cls, raw: Mapping[str, Any]) -> CandidateSubmission:
        _exact_fields(
            raw,
            "CandidateSubmission",
            {
                "schema_version",
                "submission_id",
                "submission_sequence",
                "task_ref",
                "attempt_id",
                "child_run_id",
                "lease_epoch",
                "lease_token",
                "context_manifest_fingerprint",
                "completion_contract_hash",
                "parent_execution_contract_fingerprint",
                "outcome",
                "result",
                "artifact_candidates",
                "evidence_claims",
                "discovered_work",
                "failure",
                "payload_hash",
            },
        )
        ref = _mapping(raw.get("task_ref"), "task_ref")
        return cls(
            submission_id=_string(raw, "submission_id"),
            submission_sequence=_integer(raw, "submission_sequence"),
            task_ref=DependencyRef(
                kind=cast(Any, _string(ref, "kind")),
                id=_string(ref, "id"),
                revision=_integer(ref, "revision"),
            ),
            attempt_id=_string(raw, "attempt_id"),
            child_run_id=_string(raw, "child_run_id"),
            lease_epoch=_integer(raw, "lease_epoch"),
            lease_token=_string(raw, "lease_token"),
            context_manifest_fingerprint=_string(raw, "context_manifest_fingerprint"),
            completion_contract_hash=_string(raw, "completion_contract_hash"),
            parent_execution_contract_fingerprint=_string(
                raw, "parent_execution_contract_fingerprint"
            ),
            outcome=cast(SubmissionOutcome, _string(raw, "outcome")),
            result=_mapping(raw.get("result"), "result"),
            artifact_candidates=tuple(
                ArtifactCandidate(**item) for item in _items(raw, "artifact_candidates")
            ),
            evidence_claims=tuple(
                EvidenceClaim.from_snapshot(item) for item in _items(raw, "evidence_claims")
            ),
            discovered_work=tuple(_items(raw, "discovered_work")),
            failure=cast(str | None, raw.get("failure")),
            payload_hash=_string(raw, "payload_hash"),
            schema_version=_string(raw, "schema_version"),
        )


@dataclass(frozen=True, slots=True)
class SubmissionDeliveryAck:
    submission_id: str
    payload_hash: str
    decision: DeliveryDecision
    receipt_status: str
    lease_epoch: int | None = None
    lease_token: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.submission_id, "submission_id")
        _nonempty(self.payload_hash, "payload_hash")
        if self.decision not in {
            "pending",
            "received",
            "accepted",
            "repairable",
            "rejected",
            "stale",
        }:
            raise SubmissionError(f"unsupported delivery decision {self.decision!r}")


def _nonempty(value: Any, source: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SubmissionError(f"{source} must be a non-empty string")


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, tuple | list):
        return tuple(_freeze(item) for item in value)
    return value


def _exact_fields(raw: Mapping[str, Any], source: str, expected: set[str]) -> None:
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise SubmissionError(f"{source} has invalid fields: {'; '.join(details)}")


def _mapping(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SubmissionError(f"{source} must be a mapping")
    return cast(Mapping[str, Any], value)


def _items(raw: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    value = raw.get(key, ())
    if not isinstance(value, tuple | list):
        raise SubmissionError(f"{key} must be an array")
    return tuple(_mapping(item, key) for item in value)


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    _nonempty(value, key)
    return cast(str, value)


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SubmissionError(f"{key} must be an integer")
    return value


__all__ = [
    "ArtifactCandidate",
    "CandidateSubmission",
    "CandidateVisibility",
    "DeliveryDecision",
    "EvidenceClaim",
    "SubmissionDeliveryAck",
    "SubmissionError",
    "SubmissionOutcome",
]
