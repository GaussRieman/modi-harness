"""Lazy expansion and local deadlock repair tests."""

from __future__ import annotations

from modi_harness.long_task.graph import ready_tasks
from modi_harness.long_task.planning import PlanningTrigger, validate_planner_patch
from modi_harness.long_task.types import (
    CompletionContract,
    GraphPatch,
    GraphPatchOperation,
    GroupChildRef,
    GroupRun,
)

from .helpers import graph, task, with_status


def test_expandable_task_is_expanded_only_when_planner_is_triggered() -> None:
    expandable = task("uncertain-phase", kind="expandable")
    dependent = task("finish", depends_on=(expandable.ref,))
    current = graph(expandable, dependent)
    child = task("near-term-child")
    group = GroupRun(
        group_id="uncertain-phase-group",
        group_revision=1,
        graph_id=current.graph_id,
        intent_version=current.intent_version,
        intent_binding_hash=expandable.intent_binding_hash,
        intent_binding_state="current",
        supports=expandable.supports,
        required=True,
        depends_on=expandable.depends_on,
        completion_contract=CompletionContract("group-result-v1", ("group-v1",)),
        children=(GroupChildRef(child.ref, True),),
        join_policy="all_required",
        failure_behavior="fail_group",
    )
    patch = GraphPatch(
        base_revision=current.revision,
        trigger="expandable_ready",
        reason="materialize only the near-term child work",
        operations=(
            GraphPatchOperation(
                "expand_task",
                task_id=expandable.task_id,
                expected_revision=expandable.task_revision,
                group=group,
                child_tasks=(child,),
            ),
        ),
    )

    assert ready_tasks(current) == ()
    updated = validate_planner_patch(
        current,
        PlanningTrigger("expandable_ready", target_ref="task:uncertain-phase:1"),
        patch,
    )

    assert [item.task_id for item in ready_tasks(updated)] == ["near-term-child"]
    assert expandable.ref not in updated.active_task_refs
    assert group.ref in updated.active_group_refs
    active_finish = next(
        item
        for item in updated.tasks
        if item.task_id == "finish" and item.ref in updated.active_task_refs
    )
    assert active_finish.depends_on == (group.ref,)


def test_deadlock_trigger_adds_local_repair_without_replacing_snapshot() -> None:
    failed = with_status(task("failed-input"), "failed")
    blocked = task("blocked", depends_on=(failed.ref,))
    current = graph(failed, blocked)
    repair = task("repair-input", priority=90)
    patch = GraphPatch(
        base_revision=current.revision,
        trigger="deadlock",
        reason="replace the failed input with a bounded repair Task",
        operations=(
            GraphPatchOperation("add_task", task=repair),
            GraphPatchOperation(
                "replace_dependencies",
                task_id=blocked.task_id,
                expected_revision=blocked.task_revision,
                dependencies=(repair.ref,),
            ),
        ),
    )

    updated = validate_planner_patch(current, PlanningTrigger("deadlock"), patch)

    assert updated.revision == current.revision + 1
    assert updated.replan_count == current.replan_count + 1
    assert [item.task_id for item in ready_tasks(updated)] == ["repair-input"]
    active_blocked = next(
        item
        for item in updated.tasks
        if item.task_id == "blocked" and item.ref in updated.active_task_refs
    )
    assert active_blocked.task_revision == 2
    assert active_blocked.depends_on == (repair.ref,)
