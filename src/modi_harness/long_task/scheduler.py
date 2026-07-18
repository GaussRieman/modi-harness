"""Pure scheduling, concurrency, and lease decisions for Task Graph runs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Literal

from .graph import ready_tasks
from .resources import (
    canonical_resource_paths,
    exclusive_path_conflicts,
    resource_sets_conflict,
)
from .types import DependencyRef, TaskAttempt, TaskGraphRun, TaskRun

ScheduleBlockReason = Literal["global_limit", "template_limit", "resource_conflict"]
LeaseState = Literal["valid", "suspect", "inactive"]
LeaseAction = Literal["none", "reconcile"]
ExecutorObservation = Literal[
    "live",
    "durably_resumable",
    "definitely_absent",
    "uncertain",
    "side_effecting",
]
ReconcileAction = Literal[
    "not_required",
    "renew_same_attempt",
    "resume_same_attempt",
    "replace_and_release",
    "release_retiring",
    "retain_and_reconcile",
]


class SchedulerPolicyError(ValueError):
    """Scheduler limits are invalid."""


class LeaseTimeError(ValueError):
    """A lease timestamp cannot be interpreted safely."""


class LeaseRenewalError(ValueError):
    """A lease renewal lacks one of its required durable proofs."""


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    """Logical concurrency limits used to derive a scheduling batch."""

    max_concurrency: int
    per_template_limits: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_concurrency, int)
            or isinstance(self.max_concurrency, bool)
            or self.max_concurrency < 1
        ):
            raise SchedulerPolicyError("max_concurrency must be a positive integer")
        limits = dict(self.per_template_limits)
        for template_id, limit in limits.items():
            if not isinstance(template_id, str) or not template_id.strip():
                raise SchedulerPolicyError("template IDs must be non-empty strings")
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                raise SchedulerPolicyError(
                    f"per-template limit for {template_id!r} must be a positive integer"
                )
        object.__setattr__(
            self,
            "per_template_limits",
            MappingProxyType(dict(sorted(limits.items()))),
        )


@dataclass(frozen=True, slots=True)
class BlockedTask:
    task_ref: DependencyRef
    reason: ScheduleBlockReason
    conflicting_attempt_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduleBatch:
    """A deterministic first-fit scheduling result derived from one snapshot."""

    selected: tuple[TaskRun, ...]
    blocked: tuple[BlockedTask, ...]
    occupied_slots: int


@dataclass(frozen=True, slots=True)
class LeaseAssessment:
    state: LeaseState
    action: LeaseAction
    reason: str


def attempt_occupies_slot(attempt: TaskAttempt) -> bool:
    """Count physical active and retiring executors against capacity."""

    return attempt.lease.retiring or attempt.status in {
        "created",
        "leased",
        "running",
        "waiting",
        "submitted",
    }


def occupied_slot_count(attempts: Iterable[TaskAttempt]) -> int:
    return sum(attempt_occupies_slot(attempt) for attempt in attempts)


def schedule_ready_tasks(
    graph: TaskGraphRun,
    attempts: Iterable[TaskAttempt],
    policy: SchedulerPolicy,
    *,
    resource_paths_by_task: Mapping[DependencyRef, Iterable[str]] | None = None,
) -> ScheduleBatch:
    """Select a bounded, conflict-free batch in deterministic ready order."""

    attempt_snapshot = tuple(attempts)
    occupied = occupied_slot_count(attempt_snapshot)
    selected: list[TaskRun] = []
    blocked: list[BlockedTask] = []
    template_counts = Counter(
        attempt.executor_binding.id
        for attempt in attempt_snapshot
        if attempt_occupies_slot(attempt)
    )
    selected_resources: list[tuple[str, ...]] = []
    resources = resource_paths_by_task or {}
    global_limit = min(policy.max_concurrency, graph.limits.max_concurrency)

    for task in deterministic_ready_tasks(graph):
        if occupied + len(selected) >= global_limit:
            blocked.append(BlockedTask(task.ref, "global_limit"))
            continue

        template_id = task.executor_policy.preferred_binding.id
        template_limit = policy.per_template_limits.get(template_id)
        if template_limit is not None and template_counts[template_id] >= template_limit:
            blocked.append(BlockedTask(task.ref, "template_limit"))
            continue

        requested = canonical_resource_paths(resources.get(task.ref, ()))
        conflicts = exclusive_path_conflicts(requested, attempt_snapshot)
        selected_conflict = any(
            resource_sets_conflict(requested, claimed) for claimed in selected_resources
        )
        if conflicts or selected_conflict:
            blocked.append(
                BlockedTask(
                    task.ref,
                    "resource_conflict",
                    tuple(sorted({item.holder_attempt_id for item in conflicts})),
                )
            )
            continue

        selected.append(task)
        selected_resources.append(requested)
        template_counts[template_id] += 1

    return ScheduleBatch(tuple(selected), tuple(blocked), occupied)


def deterministic_ready_tasks(graph: TaskGraphRun) -> tuple[TaskRun, ...]:
    """Expose the authoritative stable ready order to scheduler consumers."""

    return tuple(
        sorted(
            ready_tasks(graph),
            key=lambda task: (
                -task.priority,
                not task.required,
                task.task_id,
                task.task_revision,
            ),
        )
    )


def assess_attempt_lease(attempt: TaskAttempt, *, now: datetime) -> LeaseAssessment:
    """Classify expiry as suspect; it never authorizes replacement directly."""

    current = _aware_utc(now, label="now")
    if not attempt_occupies_slot(attempt):
        return LeaseAssessment("inactive", "none", "Attempt no longer owns a physical slot")
    if attempt.lease.retiring:
        return LeaseAssessment(
            "suspect",
            "reconcile",
            "retiring executor retains its slot and locks until definite absence",
        )
    if _lease_expiry(attempt) <= current:
        return LeaseAssessment(
            "suspect",
            "reconcile",
            "expired lease requires executor reconciliation",
        )
    return LeaseAssessment("valid", "none", "lease has not expired")


def reconciliation_action(
    attempt: TaskAttempt,
    observation: ExecutorObservation,
    *,
    now: datetime,
) -> ReconcileAction:
    """Map a durable executor observation to the only safe parent action."""

    assessment = assess_attempt_lease(attempt, now=now)
    if assessment.action == "none":
        return "not_required"
    if attempt.lease.retiring:
        return (
            "release_retiring"
            if observation == "definitely_absent"
            else "retain_and_reconcile"
        )
    if observation == "live":
        return "renew_same_attempt"
    if observation == "durably_resumable":
        return "resume_same_attempt"
    if observation == "definitely_absent":
        return "replace_and_release"
    return "retain_and_reconcile"


def renew_attempt_lease(
    attempt: TaskAttempt,
    *,
    now: datetime,
    ttl: timedelta,
    observed_dispatch_key: str,
    verified_liveness: bool,
    executor_checkpoint_active: bool,
    graph_terminal: bool,
    held_resource_paths: Iterable[str],
) -> TaskAttempt:
    """Return the same fenced Attempt with a parent-authorized later expiry."""

    current = _aware_utc(now, label="now")
    if ttl <= timedelta(0):
        raise LeaseRenewalError("lease ttl must be positive")
    if not attempt_occupies_slot(attempt) or attempt.lease.retiring:
        raise LeaseRenewalError("only a non-retiring active Attempt can renew")
    if observed_dispatch_key != attempt.dispatch_key:
        raise LeaseRenewalError("dispatch binding does not match the active Attempt")
    if not verified_liveness:
        raise LeaseRenewalError("verified executor liveness is required")
    if not executor_checkpoint_active:
        raise LeaseRenewalError("an active executor checkpoint is required")
    if graph_terminal:
        raise LeaseRenewalError("a terminal graph cannot renew leases")
    required = canonical_resource_paths(attempt.lease.resource_keys)
    held = canonical_resource_paths(held_resource_paths)
    if required != held:
        raise LeaseRenewalError("held resource locks do not match the active lease")
    renewed_expiry = current + ttl
    if renewed_expiry <= _lease_expiry(attempt):
        raise LeaseRenewalError("renewal must advance the lease expiry")
    return replace(
        attempt,
        lease=replace(attempt.lease, expires_at=renewed_expiry.isoformat()),
    )


def _lease_expiry(attempt: TaskAttempt) -> datetime:
    try:
        value = datetime.fromisoformat(attempt.lease.expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LeaseTimeError("lease expires_at must be an ISO-8601 timestamp") from exc
    return _aware_utc(value, label="lease expires_at")


def _aware_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LeaseTimeError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "BlockedTask",
    "ExecutorObservation",
    "LeaseAssessment",
    "LeaseRenewalError",
    "LeaseTimeError",
    "ReconcileAction",
    "ScheduleBatch",
    "ScheduleBlockReason",
    "SchedulerPolicy",
    "SchedulerPolicyError",
    "assess_attempt_lease",
    "attempt_occupies_slot",
    "deterministic_ready_tasks",
    "occupied_slot_count",
    "reconciliation_action",
    "renew_attempt_lease",
    "schedule_ready_tasks",
]
