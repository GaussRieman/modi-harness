"""Concurrent claim and retiring-resource recovery decisions."""

from __future__ import annotations

from modi_harness.long_task.scheduler import SchedulerPolicy, schedule_ready_tasks

from .helpers import graph, task
from .test_scheduler import _attempt


def test_restart_reconstructs_capacity_from_active_and_retiring_attempts() -> None:
    attempts = (
        _attempt("active"),
        _attempt("retiring", status="cancelled", retiring=True),
    )
    graph_value = graph(task("first", priority=90), task("second", priority=80))

    before = schedule_ready_tasks(
        graph_value,
        attempts,
        SchedulerPolicy(max_concurrency=3),
    )
    after = schedule_ready_tasks(
        graph_value,
        tuple(attempts),
        SchedulerPolicy(max_concurrency=3),
    )

    assert before == after
    assert [item.task_id for item in before.selected] == ["first"]
    assert before.blocked[0].reason == "global_limit"


def test_restart_keeps_retiring_resource_locked_until_stop_ack() -> None:
    retiring = _attempt(
        "retiring",
        status="cancelled",
        retiring=True,
        resources=("/workspace/output",),
    )
    target = task("target")

    blocked = schedule_ready_tasks(
        graph(target),
        (retiring,),
        SchedulerPolicy(max_concurrency=2),
        resource_paths_by_task={target.ref: ("/workspace/output/result",)},
    )
    released = schedule_ready_tasks(
        graph(target),
        (),
        SchedulerPolicy(max_concurrency=2),
        resource_paths_by_task={target.ref: ("/workspace/output/result",)},
    )

    assert blocked.selected == ()
    assert blocked.blocked[0].reason == "resource_conflict"
    assert released.selected == (target,)
