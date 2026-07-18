"""Equivalent timing produces the same scheduler and winner decisions."""

from __future__ import annotations

from dataclasses import replace

from modi_harness.long_task import CompletionContract, GroupChildRef, GroupRun
from modi_harness.long_task.groups import evaluate_group
from modi_harness.long_task.scheduler import SchedulerPolicy, schedule_ready_tasks

from .helpers import graph, task, with_status


def test_ready_batch_is_independent_of_graph_storage_order() -> None:
    first = task("a", priority=80)
    second = task("b", priority=80)
    third = task("c", priority=10)

    left = schedule_ready_tasks(
        graph(third, second, first),
        (),
        SchedulerPolicy(2),
    )
    right = schedule_ready_tasks(
        graph(first, third, second),
        (),
        SchedulerPolicy(2),
    )

    assert [item.task_id for item in left.selected] == ["a", "b"]
    assert left == right


def test_simultaneous_any_success_candidates_choose_stable_task_id() -> None:
    alpha = with_status(task("alpha", priority=50), "completed")
    beta = with_status(task("beta", priority=50), "completed")
    group = GroupRun(
        group_id="winner",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        supports=("criterion-1",),
        required=True,
        depends_on=(),
        completion_contract=CompletionContract("group-v1", ("group-v1",)),
        children=(GroupChildRef(beta.ref, True), GroupChildRef(alpha.ref, True)),
        join_policy="any_success",
        failure_behavior="cancel_unneeded",
    )
    graph_value = replace(
        graph(beta, alpha),
        groups=(group,),
        active_group_refs=(group.ref,),
    )

    assert evaluate_group(group, graph_value).winner == alpha
