"""Atomic any_success winner, loser fencing, and cancellation."""

from __future__ import annotations

from dataclasses import replace

from modi_harness.long_task import (
    CompletionContract,
    GroupChildRef,
    GroupRun,
    LeaseRecord,
    LongTaskState,
    ResourceLock,
    TaskAttempt,
)
from modi_harness.long_task.groups import commit_any_success_winner, evaluate_group

from .helpers import binding, graph, task, with_status


def test_any_success_selects_deterministic_winner_and_retires_running_loser() -> None:
    winner = with_status(task("winner", priority=90), "completed")
    loser = replace(task("loser"), status="running", active_attempt_id="attempt-loser")
    group = GroupRun(
        group_id="options",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        supports=("criterion-1",),
        required=True,
        depends_on=(),
        completion_contract=CompletionContract("group-v1", ("group-v1",)),
        children=(GroupChildRef(winner.ref, True), GroupChildRef(loser.ref, True)),
        join_policy="any_success",
        failure_behavior="cancel_unneeded",
        status="running",
    )
    attempt = TaskAttempt(
        attempt_id="attempt-loser",
        task_ref=loser.ref,
        status="running",
        executor_binding=binding(),
        context_manifest_ref="context://loser",
        completion_contract_hash="sha256:contract",
        dispatch_key="dispatch-loser",
        lease=LeaseRecord(
            "root-1",
            2,
            "token-2",
            "2026-07-18T12:00:00+00:00",
            resource_keys=("workspace://result",),
        ),
        parent_execution_contract_fingerprint="sha256:root",
    )
    graph_value = replace(
        graph(winner, loser),
        groups=(group,),
        active_group_refs=(group.ref,),
    )
    state = LongTaskState(
        root_run_id="root-1",
        revision=1,
        intents=(),
        graph=graph_value,
        attempts=(attempt,),
        resource_locks=(
            ResourceLock("workspace://result", attempt.attempt_id, attempt.lease.token),
        ),
    )
    decision = evaluate_group(group, graph_value)
    assert decision.winner == winner

    committed = commit_any_success_winner(
        state,
        group,
        winner,
        reason="winner verified",
    )

    assert committed.graph is not None
    joined = committed.graph.groups[0]
    assert joined.status == "completed"
    assert joined.winner_task_ref == winner.ref
    assert (
        next(item for item in committed.graph.tasks if item.ref == loser.ref).status == "cancelled"
    )
    assert committed.attempts[0].status == "cancelled"
    assert committed.attempts[0].lease.retiring is True
    assert committed.resource_locks[0].retiring is True
    assert committed.cancellation_requests[0].lease_token == "token-2"


def test_any_success_selects_next_deterministic_candidate_after_rejection() -> None:
    highest_priority = with_status(task("highest", priority=90), "completed")
    alpha = with_status(task("alpha", priority=50), "completed")
    beta = with_status(task("beta", priority=50), "completed")
    group = GroupRun(
        group_id="options",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        supports=("criterion-1",),
        required=True,
        depends_on=(),
        completion_contract=CompletionContract("group-v1", ("group-v1",)),
        children=(
            GroupChildRef(beta.ref, True),
            GroupChildRef(highest_priority.ref, True),
            GroupChildRef(alpha.ref, True),
        ),
        join_policy="any_success",
        failure_behavior="cancel_unneeded",
        status="running",
    )
    graph_value = replace(
        graph(beta, highest_priority, alpha),
        groups=(group,),
        active_group_refs=(group.ref,),
    )

    decision = evaluate_group(
        group,
        graph_value,
        rejected_task_refs=(highest_priority.ref,),
    )

    assert decision.status == "verifying"
    assert decision.candidates == (alpha,)
    assert decision.winner == alpha


def test_any_success_fails_after_all_completed_candidates_are_rejected() -> None:
    first = with_status(task("first"), "completed")
    second = with_status(task("second"), "failed")
    group = GroupRun(
        group_id="options",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        supports=("criterion-1",),
        required=True,
        depends_on=(),
        completion_contract=CompletionContract("group-v1", ("group-v1",)),
        children=(GroupChildRef(first.ref, True), GroupChildRef(second.ref, True)),
        join_policy="any_success",
        failure_behavior="cancel_unneeded",
        status="running",
    )
    graph_value = replace(
        graph(first, second),
        groups=(group,),
        active_group_refs=(group.ref,),
    )

    decision = evaluate_group(
        group,
        graph_value,
        rejected_task_refs=(first.ref,),
    )

    assert decision.status == "failed"
    assert decision.winner is None
    assert decision.reason == "any_success Group has no viable child"
