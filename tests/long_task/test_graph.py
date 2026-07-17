"""Pure Task Graph validation, readiness, and patch tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from modi_harness.long_task import (
    CompletionContract,
    GraphPatch,
    GraphPatchOperation,
    GraphValidationError,
    GroupChildRef,
    GroupRun,
    apply_graph_patch,
    ready_tasks,
    validate_graph,
)

from .helpers import graph, task, with_status


def test_ready_tasks_respect_dependencies_priority_and_requiredness() -> None:
    done = with_status(task("done"), "completed")
    high = task("high", depends_on=(done.ref,), priority=90)
    low = task("low", priority=10)
    blocked = task("blocked", depends_on=(task("missing").ref,))
    value = graph(done, high, low, blocked)
    value = replace(value, tasks=(*value.tasks, task("missing")), active_task_refs=(*value.active_task_refs, task("missing").ref))

    assert [item.task_id for item in ready_tasks(value)] == ["high", "missing", "low"]


def test_graph_rejects_cycle_and_missing_criterion_coverage() -> None:
    first = task("first")
    second = task("second", depends_on=(first.ref,))
    first = replace(first, depends_on=(second.ref,))
    with pytest.raises(GraphValidationError, match="cycle"):
        validate_graph(graph(first, second))

    uncovered = replace(graph(task("only", supports=())), required_criteria=("criterion-1",))
    with pytest.raises(GraphValidationError, match=r"supports no criterion|coverage missing"):
        validate_graph(uncovered)


def test_seed_patch_adds_ready_tasks_and_rejects_stale_revision() -> None:
    empty = graph(revision=0)
    patch = GraphPatch(
        base_revision=0,
        trigger="seed",
        reason="initial plan",
        operations=(GraphPatchOperation("add_task", task=task("first")),),
    )
    updated = apply_graph_patch(empty, patch)

    assert updated.revision == 1
    assert [item.task_id for item in ready_tasks(updated)] == ["first"]
    with pytest.raises(GraphValidationError, match="stale graph revision"):
        apply_graph_patch(updated, patch)


def test_replace_dependencies_creates_new_revision_and_preserves_history() -> None:
    first = with_status(task("first"), "completed")
    second = task("second")
    value = graph(first, second)
    patch = GraphPatch(
        base_revision=1,
        trigger="dependency",
        reason="bind exact dependency",
        operations=(
            GraphPatchOperation(
                "replace_dependencies",
                task_id="second",
                expected_revision=1,
                dependencies=(first.ref,),
            ),
        ),
    )
    updated = apply_graph_patch(value, patch)

    assert len([item for item in updated.tasks if item.task_id == "second"]) == 2
    active = next(ref for ref in updated.active_task_refs if ref.id == "second")
    assert active.revision == 2


def test_expand_task_creates_group_children_and_rewrites_pending_dependents() -> None:
    expandable = task("phase", kind="expandable")
    dependent = task("after", depends_on=(expandable.ref,))
    child = task("child")
    group = GroupRun(
        group_id="phase-group",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
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
    value = graph(expandable, dependent)
    patch = GraphPatch(
        base_revision=1,
        trigger="expand",
        reason="detail near-term work",
        operations=(
            GraphPatchOperation(
                "expand_task",
                task_id="phase",
                expected_revision=1,
                group=group,
                child_tasks=(child,),
            ),
        ),
    )
    updated = apply_graph_patch(value, patch)

    assert group.ref in updated.active_group_refs
    assert expandable.ref not in updated.active_task_refs
    active_dependent = next(
        item
        for item in updated.tasks
        if item.task_id == "after" and item.ref in updated.active_task_refs
    )
    assert active_dependent.depends_on == (group.ref,)
