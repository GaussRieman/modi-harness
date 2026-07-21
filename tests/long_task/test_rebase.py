"""Pure atomic IntentRebase planning tests."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task.intent import (
    IntentConfirmation,
    IntentPatch,
    IntentPatchChange,
    IntentRebaseError,
    RebaseReuseProof,
    intent_fingerprint,
    plan_intent_rebase,
)
from modi_harness.long_task.types import (
    CompletionContract,
    GroupChildRef,
    GroupRun,
    IntentCriterion,
    IntentVersion,
    LeaseRecord,
    LongTaskState,
    TaskAttempt,
    TaskRun,
)

from .helpers import binding, graph, task


def _intent(version: int, *, goal: str) -> IntentVersion:
    return IntentVersion(
        intent_id="intent-1",
        version=version,
        status="confirmed",
        goal=goal,
        desired_outcome="A verified result",
        success_criteria=(
            IntentCriterion(
                "criterion-1",
                "The result works",
                True,
                "validator",
                "goal-v1",
            ),
        ),
        authority_hash="sha256:authority",
    )


def _state(*tasks: TaskRun, attempts: tuple[TaskAttempt, ...] = ()) -> LongTaskState:
    return LongTaskState(
        root_run_id="root-1",
        revision=7,
        intents=(_intent(1, goal="Build V1"),),
        graph=graph(*tasks, revision=4),
        attempts=attempts,
    )


def _patch() -> IntentPatch:
    return IntentPatch(
        1,
        "The desired goal changed",
        (IntentPatchChange("set_goal", "goal", "Build V2"),),
    )


def _new_intent() -> IntentVersion:
    return _intent(2, goal="Build V2")


def _confirmation() -> IntentConfirmation:
    intent = _new_intent()
    return IntentConfirmation(intent.intent_id, intent.version, intent_fingerprint(intent))


def _contract_hash(value: TaskRun | GroupRun) -> str:
    contract = value.completion_contract
    return compute_fingerprint(
        {
            "output_schema_id": contract.output_schema_id,
            "validator_ids": list(contract.validator_ids),
            "required_artifact_types": list(contract.required_artifact_types),
            "required_evidence": list(contract.required_evidence),
        }
    )


def _proof(
    value: TaskRun | GroupRun,
    *,
    reusable: bool = True,
    new_intent_version: int = 2,
) -> RebaseReuseProof:
    dependencies = value.depends_on
    if isinstance(value, GroupRun):
        dependencies += tuple(child.task_ref for child in value.children)
    return RebaseReuseProof(
        record_id=f"proof-{value.ref.id}",
        target_ref=value.ref,
        prior_intent_version=value.intent_version,
        new_intent_version=new_intent_version,
        intent_binding_hash=value.intent_binding_hash,
        dependency_refs=dependencies,
        completion_contract_hash=_contract_hash(value),
        reusable=reusable,
        validator_fingerprint="sha256:rebase-validator",
        new_intent_fingerprint=intent_fingerprint(_new_intent()),
    )


def _plan(
    state: LongTaskState,
    *proofs: RebaseReuseProof,
):
    return plan_intent_rebase(
        state,
        new_intent=_new_intent(),
        patch=_patch(),
        confirmation=_confirmation(),
        reuse_proofs=proofs,
    )


def test_pending_task_always_gets_new_current_revision() -> None:
    pending = task("pending")

    plan = _plan(_state(pending))

    replacement = plan.append_tasks[0]
    assert replacement.task_revision == 2
    assert replacement.intent_version == 2
    assert replacement.intent_binding_state == "current"
    assert plan.active_task_refs == (replacement.ref,)
    assert plan.binding_decisions[0].decision == "invalidated"


def test_completed_task_without_proof_is_invalidated_and_replaced() -> None:
    completed = replace(
        task("completed"),
        status="completed",
        output_refs=("artifact://old",),
    )

    plan = _plan(_state(completed))

    replacement = plan.append_tasks[0]
    assert replacement.status == "pending"
    assert replacement.output_refs == ()
    assert replacement.failure is None
    assert plan.binding_decisions[0].replacement_ref == replacement.ref


def test_completed_task_with_exact_passed_proof_is_retained() -> None:
    completed = replace(
        task("completed"),
        status="completed",
        output_refs=("artifact://verified",),
    )

    plan = _plan(_state(completed), _proof(completed))

    assert plan.append_tasks == ()
    assert plan.active_task_refs == (completed.ref,)
    assert plan.binding_decisions[0].decision == "retained"
    assert plan.binding_decisions[0].proof_record_id == "proof-completed"


def test_invalidated_active_task_produces_exact_cancellation_fence() -> None:
    running = replace(
        task("running"),
        status="running",
        active_attempt_id="attempt-1",
        resource_keys=("workspace://result",),
    )
    attempt = TaskAttempt(
        attempt_id="attempt-1",
        task_ref=running.ref,
        status="running",
        executor_binding=binding(),
        context_manifest_ref="context://attempt-1",
        completion_contract_hash="sha256:contract",
        dispatch_key="dispatch-1",
        lease=LeaseRecord(
            "scheduler-1",
            3,
            "lease-token-3",
            "2026-07-18T10:00:00Z",
            resource_keys=("lock://db",),
        ),
        parent_execution_contract_fingerprint="sha256:root-contract",
    )

    plan = _plan(_state(running, attempts=(attempt,)))

    replacement = plan.append_tasks[0]
    cancellation = plan.cancellations[0]
    assert replacement.status == "pending"
    assert replacement.active_attempt_id is None
    assert cancellation.attempt_id == "attempt-1"
    assert cancellation.lease_epoch == 3
    assert cancellation.lease_token == "lease-token-3"
    assert cancellation.resource_keys == ("lock://db", "workspace://result")


def test_false_or_stale_proof_cannot_retain_completed_task() -> None:
    completed = replace(task("completed"), status="completed")

    false_plan = _plan(_state(completed), _proof(completed, reusable=False))
    stale_plan = _plan(
        _state(completed),
        _proof(completed, new_intent_version=3),
    )
    wrong_intent_plan = _plan(
        _state(completed),
        replace(_proof(completed), new_intent_fingerprint="sha256:other"),
    )

    assert false_plan.binding_decisions[0].decision == "invalidated"
    assert stale_plan.binding_decisions[0].decision == "invalidated"
    assert wrong_intent_plan.binding_decisions[0].decision == "invalidated"


def test_rebase_rejects_state_that_already_drifted_to_another_confirmed_intent() -> None:
    pending = task("pending")
    state = replace(
        _state(pending),
        intents=(
            _intent(1, goal="Build V1"),
            _intent(2, goal="Already changed"),
        ),
    )

    with pytest.raises(IntentRebaseError, match="exactly one confirmed"):
        _plan(state)


def test_dependency_chain_is_transitively_rewritten_to_exact_revisions() -> None:
    first = task("first")
    second = task("second", depends_on=(first.ref,))
    third = task("third", depends_on=(second.ref,))

    plan = _plan(_state(first, second, third))

    replacements = {item.task_id: item for item in plan.append_tasks}
    assert replacements["second"].depends_on == (replacements["first"].ref,)
    assert replacements["third"].depends_on == (replacements["second"].ref,)
    assert all(item.task_revision == 2 for item in replacements.values())


def test_replacement_clears_attempt_outputs_and_failure_without_mutating_state() -> None:
    failed = replace(
        task("failed"),
        status="failed",
        output_refs=("artifact://partial",),
        failure="boom",
    )
    state = _state(failed)
    before = state.snapshot()

    plan = _plan(state)

    replacement = plan.append_tasks[0]
    assert replacement.status == "pending"
    assert replacement.active_attempt_id is None
    assert replacement.output_refs == ()
    assert replacement.failure is None
    assert state.snapshot() == before


def test_group_with_replaced_child_gets_new_revision_even_with_proof() -> None:
    child = task("child")
    group_value = GroupRun(
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
        children=(GroupChildRef(child.ref, True),),
        join_policy="all_required",
        failure_behavior="fail_group",
        status="completed",
        winner_task_ref=child.ref,
        verification_record_ref="verify-group",
    )
    state = replace(
        _state(child),
        graph=replace(
            graph(child, revision=4),
            groups=(group_value,),
            active_group_refs=(group_value.ref,),
        ),
    )

    plan = _plan(state, _proof(group_value))

    replacement_child = plan.append_tasks[0]
    replacement_group = plan.append_groups[0]
    assert replacement_group.children[0].task_ref == replacement_child.ref
    assert replacement_group.status == "pending"
    assert replacement_group.winner_task_ref is None
    assert replacement_group.verification_record_ref is None


def test_plan_is_deterministic_and_json_serializable() -> None:
    first = task("first")
    second = task("second", depends_on=(first.ref,))
    state = _state(first, second)

    first_plan = _plan(state)
    second_plan = _plan(state)

    assert first_plan == second_plan
    json.dumps(first_plan.snapshot(), sort_keys=True)
    assert first_plan.expected_root_revision == 7
    assert first_plan.expected_graph_revision == 4
    assert first_plan.next_graph_revision == 5
    assert first_plan.reset_criterion_ids == ("criterion-1",)
