"""Pure native task-plan transition tests."""

from __future__ import annotations

import pytest

from modi_harness.tasks import (
    TaskTransitionError,
    block_task,
    complete_task,
    create_task_plan,
    plan_is_complete,
    resume_task,
    revise_task_plan,
    start_task,
)


def _tasks():
    return [
        {"id": "fetch", "title": "Fetch sources"},
        {"id": "analyze", "title": "Analyze evidence"},
        {"id": "write", "title": "Write briefing"},
    ]


def test_create_plan_normalizes_pending_tasks() -> None:
    plan = create_task_plan(_tasks(), min_items=2, max_items=4)

    assert plan["version"] == 1
    assert plan["current_task_id"] is None
    assert [item["status"] for item in plan["items"]] == ["pending"] * 3


@pytest.mark.parametrize(
    "tasks, message",
    [
        ([], "task count"),
        ([{"id": "x", "title": "A"}, {"id": "x", "title": "B"}], "unique"),
        ([{"id": "", "title": "A"}], "non-empty"),
        ([{"id": "x", "title": ""}], "titles"),
        ([{"id": "x", "title": "A", "status": "completed"}], "pending"),
    ],
)
def test_create_plan_rejects_invalid_shape(tasks, message: str) -> None:
    with pytest.raises(TaskTransitionError, match=message):
        create_task_plan(tasks)


def test_start_complete_and_start_next_are_atomic() -> None:
    plan = create_task_plan(_tasks())
    active = start_task(plan, "fetch", current_action="Downloading official docs")
    advanced = complete_task(
        active,
        "fetch",
        summary="Fetched one official source",
        next_task_id="analyze",
        current_action="Comparing pricing rows",
    )

    assert plan["items"][0]["status"] == "pending"
    assert advanced["items"][0]["status"] == "completed"
    assert advanced["items"][0]["summary"] == "Fetched one official source"
    assert advanced["items"][1]["status"] == "in_progress"
    assert advanced["current_task_id"] == "analyze"
    assert advanced["current_action"] == "Comparing pricing rows"


def test_completed_task_cannot_regress_or_complete_twice() -> None:
    active = start_task(create_task_plan(_tasks()), "fetch", current_action="Fetch")
    completed = complete_task(active, "fetch", summary="Done")

    with pytest.raises(TaskTransitionError, match="pending"):
        start_task(completed, "fetch", current_action="Again")
    with pytest.raises(TaskTransitionError, match="active"):
        complete_task(completed, "fetch", summary="Again")


def test_cannot_skip_pending_task_to_completed() -> None:
    plan = create_task_plan(_tasks())

    with pytest.raises(TaskTransitionError, match="active"):
        complete_task(plan, "fetch", summary="Pretend done")


def test_only_one_task_can_be_active() -> None:
    active = start_task(create_task_plan(_tasks()), "fetch", current_action="Fetch")

    with pytest.raises(TaskTransitionError, match="already"):
        start_task(active, "analyze", current_action="Analyze")


def test_block_active_task_requires_reason() -> None:
    active = start_task(create_task_plan(_tasks()), "fetch", current_action="Fetch")

    blocked = block_task(active, "fetch", reason="Source unavailable")

    assert blocked["items"][0]["status"] == "blocked"
    assert blocked["current_task_id"] is None
    with pytest.raises(TaskTransitionError, match="reason"):
        block_task(active, "fetch", reason="")


def test_blocked_task_can_resume_after_external_condition_changes() -> None:
    active = start_task(create_task_plan(_tasks()), "fetch", current_action="Fetch")
    blocked = block_task(active, "fetch", reason="Source unavailable")

    resumed = resume_task(blocked, "fetch", current_action="Reading replacement source")

    assert resumed["items"][0]["status"] == "in_progress"
    assert resumed["items"][0]["summary"] == "Source unavailable"
    assert resumed["current_task_id"] == "fetch"
    assert resumed["current_action"] == "Reading replacement source"


def test_only_blocked_task_can_resume() -> None:
    plan = create_task_plan(_tasks())

    with pytest.raises(TaskTransitionError, match="blocked"):
        resume_task(plan, "fetch", current_action="Work")


def test_revision_increments_version_before_execution() -> None:
    plan = create_task_plan(_tasks())
    revised = revise_task_plan(plan, [{"id": "one", "title": "One task"}])

    assert revised["version"] == 2
    assert [item["id"] for item in revised["items"]] == ["one"]


def test_revision_rejected_after_execution_starts() -> None:
    active = start_task(create_task_plan(_tasks()), "fetch", current_action="Fetch")

    with pytest.raises(TaskTransitionError, match="before execution"):
        revise_task_plan(active, _tasks())


def test_plan_is_complete_only_when_every_task_completed() -> None:
    plan = create_task_plan([{"id": "one", "title": "Only task"}])
    assert plan_is_complete(plan) is False
    active = start_task(plan, "one", current_action="Work")
    completed = complete_task(active, "one", summary="Done")
    assert plan_is_complete(completed) is True
