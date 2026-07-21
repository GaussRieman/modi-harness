"""Authoritative legal state transitions for Task Graph values."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import replace
from typing import Any, TypeVar, cast

from .types import (
    AttemptStatus,
    GraphStatus,
    GroupRun,
    GroupStatus,
    TaskAttempt,
    TaskGraphRun,
    TaskRun,
    TaskStatus,
)


class TransitionError(ValueError):
    """A requested state transition is not legal."""


_StatusT = TypeVar("_StatusT", bound=str)
_ValueT = TypeVar("_ValueT")


_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    "pending": frozenset({"running", "waiting", "verifying", "completed", "cancelled"}),
    "running": frozenset({"pending", "waiting", "verifying", "failed", "cancelled"}),
    "waiting": frozenset({"running", "verifying", "cancelled"}),
    "verifying": frozenset({"pending", "waiting", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    "created": frozenset({"leased", "cancelled"}),
    "leased": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"waiting", "submitted", "failed", "cancelled"}),
    "waiting": frozenset({"running", "failed", "cancelled"}),
    "submitted": frozenset({"running", "waiting", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_GROUP_TRANSITIONS: dict[GroupStatus, frozenset[GroupStatus]] = {
    "pending": frozenset({"running", "verifying", "completed", "failed", "cancelled"}),
    "running": frozenset({"verifying", "completed", "failed", "cancelled"}),
    "verifying": frozenset({"running", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_GRAPH_TRANSITIONS: dict[GraphStatus, frozenset[GraphStatus]] = {
    "planning": frozenset({"active", "waiting", "failed", "cancelled"}),
    "active": frozenset({"waiting", "verifying", "failed", "cancelled"}),
    "waiting": frozenset({"active", "verifying", "failed", "cancelled"}),
    "verifying": frozenset({"active", "waiting", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def transition_task(task: TaskRun, target: TaskStatus, **changes: object) -> TaskRun:
    _check("Task", task.status, target, _TASK_TRANSITIONS)
    return _replace_dataclass(task, status=target, **changes)


def transition_attempt(
    attempt: TaskAttempt, target: AttemptStatus, **changes: object
) -> TaskAttempt:
    _check("Attempt", attempt.status, target, _ATTEMPT_TRANSITIONS)
    return _replace_dataclass(attempt, status=target, **changes)


def transition_group(group: GroupRun, target: GroupStatus, **changes: object) -> GroupRun:
    _check("Group", group.status, target, _GROUP_TRANSITIONS)
    return _replace_dataclass(group, status=target, **changes)


def transition_graph(
    graph: TaskGraphRun, target: GraphStatus, **changes: object
) -> TaskGraphRun:
    _check("Graph", graph.status, target, _GRAPH_TRANSITIONS)
    return _replace_dataclass(graph, status=target, **changes)


def _check(
    label: str,
    current: _StatusT,
    target: _StatusT,
    transitions: Mapping[_StatusT, Set[_StatusT]],
) -> None:
    if target not in transitions[current]:
        raise TransitionError(f"illegal {label} transition {current!r} -> {target!r}")


def _replace_dataclass(value: _ValueT, **changes: object) -> _ValueT:
    """Keep dynamic transition updates while preserving the concrete return type."""

    dynamic_replace = cast(Any, replace)
    return cast(_ValueT, dynamic_replace(value, **changes))


__all__ = [
    "TransitionError",
    "transition_attempt",
    "transition_graph",
    "transition_group",
    "transition_task",
]
