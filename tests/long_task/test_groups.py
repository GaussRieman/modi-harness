"""Group all_required join state derivation."""

from __future__ import annotations

from dataclasses import replace

from modi_harness.long_task import CompletionContract, GroupChildRef, GroupRun
from modi_harness.long_task.graph import ready_tasks
from modi_harness.long_task.groups import evaluate_group

from .helpers import graph, task, with_status


def _group(*children, required: tuple[bool, ...]) -> GroupRun:
    return GroupRun(
        group_id="join",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        supports=("criterion-1",),
        required=True,
        depends_on=(),
        completion_contract=CompletionContract("group-v1", ("group-v1",)),
        children=tuple(
            GroupChildRef(child.ref, item) for child, item in zip(children, required, strict=True)
        ),
        join_policy="all_required",
        failure_behavior="fail_group",
    )


def test_all_required_verifies_exact_completed_child_set() -> None:
    first = with_status(task("first"), "completed")
    optional = with_status(task("optional"), "failed")
    group = _group(first, optional, required=(True, False))
    value = replace(
        graph(first, optional),
        groups=(group,),
        active_group_refs=(group.ref,),
    )

    decision = evaluate_group(group, value)

    assert decision.status == "verifying"
    assert decision.candidates == (first,)


def test_all_required_fails_when_required_child_fails() -> None:
    failed = with_status(task("failed"), "failed")
    group = _group(failed, required=(True,))
    value = replace(graph(failed), groups=(group,), active_group_refs=(group.ref,))

    assert evaluate_group(group, value).status == "failed"


def test_all_required_ignores_rejected_candidate_filter() -> None:
    first = with_status(task("first"), "completed")
    second = with_status(task("second"), "completed")
    group = _group(first, second, required=(True, True))
    value = replace(
        graph(first, second),
        groups=(group,),
        active_group_refs=(group.ref,),
    )

    decision = evaluate_group(
        group,
        value,
        rejected_task_refs=(first.ref, second.ref),
    )

    assert decision.status == "verifying"
    assert decision.candidates == (first, second)


def test_downstream_task_becomes_ready_only_after_exact_group_completion() -> None:
    child = with_status(task("child"), "completed")
    group = replace(_group(child, required=(True,)), status="running")
    downstream = task("downstream", depends_on=(group.ref,))
    value = replace(
        graph(child, downstream),
        groups=(group,),
        active_group_refs=(group.ref,),
    )

    assert downstream not in ready_tasks(value)
    completed = replace(
        value,
        groups=(replace(group, status="completed"),),
    )
    assert downstream in ready_tasks(completed)
