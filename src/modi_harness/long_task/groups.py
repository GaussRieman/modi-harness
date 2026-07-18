"""Pure Group join evaluation and atomic winner transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .._utils import compute_fingerprint, new_ulid
from .types import (
    CancellationRequest,
    DependencyRef,
    GroupRun,
    LongTaskState,
    TaskGraphRun,
    TaskRun,
)


@dataclass(frozen=True, slots=True)
class GroupDecision:
    status: str
    candidates: tuple[TaskRun, ...] = ()
    winner: TaskRun | None = None
    reason: str | None = None


def evaluate_group(
    group: GroupRun,
    graph: TaskGraphRun,
    *,
    rejected_task_refs: tuple[DependencyRef, ...] = (),
) -> GroupDecision:
    children = tuple(_task(graph, item.task_ref) for item in group.children)
    required = tuple(
        task for item, task in zip(group.children, children, strict=True) if item.required
    )
    completed = tuple(task for task in children if task.status == "completed")
    active = tuple(
        task for task in children if task.status in {"pending", "running", "waiting", "verifying"}
    )
    if group.join_policy == "all_required":
        failed = [task for task in required if task.status in {"failed", "cancelled"}]
        if failed:
            return GroupDecision(
                "failed",
                reason="required Group child failed: "
                + ", ".join(sorted(task.task_id for task in failed)),
            )
        if required and all(task.status == "completed" for task in required):
            return GroupDecision("verifying", candidates=completed)
        return GroupDecision(
            "running" if any(task.status != "pending" for task in children) else "pending"
        )
    viable_completed = tuple(task for task in completed if task.ref not in rejected_task_refs)
    if viable_completed:
        winner = min(viable_completed, key=lambda task: (-task.priority, task.task_id))
        return GroupDecision("verifying", candidates=(winner,), winner=winner)
    if not active:
        return GroupDecision("failed", reason="any_success Group has no viable child")
    return GroupDecision(
        "running" if any(task.status != "pending" for task in children) else "pending"
    )


def commit_any_success_winner(
    state: LongTaskState,
    group: GroupRun,
    winner: TaskRun,
    *,
    reason: str,
) -> LongTaskState:
    graph = _graph(state)
    child_refs = {item.task_ref for item in group.children}
    tasks: list[TaskRun] = []
    attempts = list(state.attempts)
    locks = list(state.resource_locks)
    cancellations = list(state.cancellation_requests)
    for task in graph.tasks:
        if (
            task.ref not in child_refs
            or task.ref == winner.ref
            or task.status
            in {
                "completed",
                "failed",
                "cancelled",
            }
        ):
            tasks.append(task)
            continue
        tasks.append(
            replace(
                task,
                status="cancelled",
                active_attempt_id=None,
                failure=f"cancelled after Group winner {winner.task_id!r}",
            )
        )
        if task.active_attempt_id is None:
            continue
        for index, attempt in enumerate(attempts):
            if attempt.attempt_id != task.active_attempt_id:
                continue
            retired = replace(
                attempt,
                status="cancelled",
                lease=replace(attempt.lease, retiring=True),
                failure=f"lost any_success Group {group.group_id!r}",
            )
            attempts[index] = retired
            locks = [
                replace(lock, retiring=True) if lock.attempt_id == attempt.attempt_id else lock
                for lock in locks
            ]
            cancellations.append(
                CancellationRequest(
                    cancellation_id=new_ulid(),
                    attempt_id=attempt.attempt_id,
                    reason=reason,
                    lease_epoch=attempt.lease.epoch,
                    lease_token=attempt.lease.token,
                )
            )
    completed = replace(
        group,
        status="completed",
        winner_task_ref=winner.ref,
        verification_record_ref=(
            group.verification_record_ref
            or "group-winner://"
            + compute_fingerprint({"group": group.ref.key, "winner": winner.ref.key})
        ),
    )
    groups = tuple(completed if item.ref == group.ref else item for item in graph.groups)
    return replace(
        state,
        graph=replace(graph, tasks=tuple(tasks), groups=groups),
        attempts=tuple(attempts),
        resource_locks=tuple(locks),
        cancellation_requests=tuple(cancellations),
    )


def replace_group(graph: TaskGraphRun, group: GroupRun) -> TaskGraphRun:
    return replace(
        graph,
        groups=tuple(group if item.ref == group.ref else item for item in graph.groups),
    )


def _graph(state: LongTaskState) -> TaskGraphRun:
    if state.graph is None:
        raise ValueError("Task Graph state has no graph")
    return state.graph


def _task(graph: TaskGraphRun, ref: object) -> TaskRun:
    for task in graph.tasks:
        if task.ref == ref:
            return task
    raise ValueError("Group references unknown child Task")


__all__ = [
    "GroupDecision",
    "commit_any_success_winner",
    "evaluate_group",
    "replace_group",
]
