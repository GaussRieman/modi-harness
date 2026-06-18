"""Pure canonical task-plan transitions for the native task protocol."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .types import TaskItem, TaskPlan


class TaskTransitionError(ValueError):
    """Raised when a proposed task-plan transition violates an invariant."""


def create_task_plan(
    tasks: list[dict[str, Any]],
    *,
    min_items: int = 1,
    max_items: int = 8,
    version: int = 1,
) -> TaskPlan:
    """Create a pending-only plan from model-proposed ids and titles."""
    if not min_items <= len(tasks) <= max_items:
        raise TaskTransitionError(f"task count must be between {min_items} and {max_items}")
    seen: set[str] = set()
    items: list[TaskItem] = []
    for raw in tasks:
        task_id = str(raw.get("id", "")).strip()
        title = str(raw.get("title", "")).strip()
        if not task_id or task_id in seen:
            raise TaskTransitionError("task ids must be non-empty and unique")
        if not title or len(title) > 120:
            raise TaskTransitionError("task titles must contain 1-120 characters")
        if raw.get("status", "pending") != "pending":
            raise TaskTransitionError("new task plans must contain only pending tasks")
        seen.add(task_id)
        items.append(TaskItem(id=task_id, title=title, status="pending", summary=None))
    return TaskPlan(
        version=version,
        items=items,
        current_task_id=None,
        current_action=None,
        last_activity=None,
    )


def revise_task_plan(
    plan: TaskPlan,
    tasks: list[dict[str, Any]],
    *,
    min_items: int = 1,
    max_items: int = 8,
) -> TaskPlan:
    """Replace an unstarted plan with a new pending version."""
    if any(item["status"] != "pending" for item in plan["items"]):
        raise TaskTransitionError("a task plan can only be revised before execution")
    return create_task_plan(
        tasks,
        min_items=min_items,
        max_items=max_items,
        version=plan["version"] + 1,
    )


def start_task(plan: TaskPlan, task_id: str, *, current_action: str) -> TaskPlan:
    """Start exactly one pending task."""
    if plan["current_task_id"] is not None:
        raise TaskTransitionError("another task is already in progress")
    action = current_action.strip()
    if not action:
        raise TaskTransitionError("current_action is required")
    updated = deepcopy(plan)
    item = _find_task(updated, task_id)
    if item["status"] != "pending":
        raise TaskTransitionError("only a pending task can be started")
    item["status"] = "in_progress"
    updated["current_task_id"] = task_id
    updated["current_action"] = action
    return updated


def resume_task(plan: TaskPlan, task_id: str, *, current_action: str) -> TaskPlan:
    """Resume one blocked task after its external blocker has changed."""
    if plan["current_task_id"] is not None:
        raise TaskTransitionError("another task is already in progress")
    action = current_action.strip()
    if not action:
        raise TaskTransitionError("current_action is required")
    updated = deepcopy(plan)
    item = _find_task(updated, task_id)
    if item["status"] != "blocked":
        raise TaskTransitionError("only a blocked task can be resumed")
    item["status"] = "in_progress"
    updated["current_task_id"] = task_id
    updated["current_action"] = action
    updated["last_activity"] = action
    return updated


def complete_task(
    plan: TaskPlan,
    task_id: str,
    *,
    summary: str,
    next_task_id: str | None = None,
    current_action: str | None = None,
) -> TaskPlan:
    """Complete the active task and optionally start one next task atomically."""
    if plan["current_task_id"] != task_id:
        raise TaskTransitionError("only the active task can be completed")
    result = summary.strip()
    if not result:
        raise TaskTransitionError("completion summary is required")
    updated = deepcopy(plan)
    item = _find_task(updated, task_id)
    if item["status"] != "in_progress":
        raise TaskTransitionError("only an in-progress task can be completed")
    item["status"] = "completed"
    item["summary"] = result
    updated["current_task_id"] = None
    updated["current_action"] = None
    updated["last_activity"] = result
    if next_task_id is not None:
        if not current_action or not current_action.strip():
            raise TaskTransitionError("current_action is required when starting the next task")
        next_item = _find_task(updated, next_task_id)
        if next_item["status"] != "pending":
            raise TaskTransitionError("next task must be pending")
        next_item["status"] = "in_progress"
        updated["current_task_id"] = next_task_id
        updated["current_action"] = current_action.strip()
    elif current_action is not None:
        raise TaskTransitionError("current_action requires next_task_id")
    return updated


def block_task(plan: TaskPlan, task_id: str, *, reason: str) -> TaskPlan:
    """Block the active task with an actionable reason."""
    if plan["current_task_id"] != task_id:
        raise TaskTransitionError("only the active task can be blocked")
    detail = reason.strip()
    if not detail:
        raise TaskTransitionError("block reason is required")
    updated = deepcopy(plan)
    item = _find_task(updated, task_id)
    item["status"] = "blocked"
    item["summary"] = detail
    updated["current_task_id"] = None
    updated["current_action"] = None
    updated["last_activity"] = detail
    return updated


def plan_is_complete(plan: TaskPlan | None) -> bool:
    return bool(plan and plan["items"] and all(i["status"] == "completed" for i in plan["items"]))


def _find_task(plan: TaskPlan, task_id: str) -> TaskItem:
    for item in plan["items"]:
        if item["id"] == task_id:
            return item
    raise TaskTransitionError(f"unknown task id: {task_id}")


__all__ = [
    "TASK_PROTOCOL_TOOL_NAMES",
    "TaskTransitionError",
    "block_task",
    "complete_task",
    "create_task_plan",
    "plan_is_complete",
    "resume_task",
    "revise_task_plan",
    "start_task",
]


TASK_PROTOCOL_TOOL_NAMES = (
    "create_task_plan",
    "revise_task_plan",
    "start_task",
    "complete_task",
    "block_task",
    "resume_task",
)
