"""Pure parent executor contract and pending-decision state tests."""

from __future__ import annotations

import json

import pytest

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import (
    ExecutorContractError,
    LongTaskState,
    PendingDecisionConflict,
    PendingDecisionError,
    PendingDecisionStale,
    PendingGoalDecision,
    PendingTaskDecision,
    consume_pending_goal_decision,
    consume_pending_task_decision,
    long_task_state_from_snapshot,
    parse_human_task_contract,
    validate_human_prompt,
    validate_human_response,
)
from modi_harness.long_task.verification import json_value
from modi_harness.workflow import PinnedComponent

from .helpers import graph, task


def _task_pending() -> PendingTaskDecision:
    return PendingTaskDecision(
        request_id="task-decision-1",
        task_ref=task("review").ref,
        attempt_id="attempt-1",
        graph_revision=3,
        contract_id="human-review-v1",
        contract_fingerprint="sha256:human-contract",
        input_hash="sha256:task-input",
        expected_root_revision=7,
        decision_class="judgment",
        allowed_decisions=("approve", "reject"),
        prompt={"title": "Review result", "items": ["artifact://one"]},
    )


def _goal_pending() -> PendingGoalDecision:
    return PendingGoalDecision(
        request_id="goal-decision-1",
        graph_revision=3,
        goal_verification_record_id="goal-verification-1",
        input_hash="sha256:goal-input",
        expected_root_revision=7,
        allowed_decisions=("repair", "reject"),
        criterion_gaps=(
            {"criterion_id": "criterion-1", "reason": "evidence is ambiguous"},
        ),
        options=(
            {"id": "repair", "label": "Collect more evidence"},
            {"id": "reject", "label": "Stop"},
        ),
        prompt={"summary": "Choose how to resolve the Goal ambiguity"},
    )


def _human_component() -> PinnedComponent:
    return PinnedComponent(
        id="human-review-v1",
        version="1",
        kind="human_contract",
        implementation_digest="sha256:human-review",
        protocol_version="human-task-v1",
        input_schema_id="human-prompt-v1",
        output_schema_id="human-response-v1",
        supported_outcomes=("passed",),
        configuration={
            "prompt_schema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            "response_schema": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["decision", "comment"],
                "additionalProperties": False,
            },
            "decision_class": "judgment",
            "allowed_decisions": ["approve", "reject"],
            "authority_requirement": {"role": "reviewer"},
            "timeout_behavior": "keep_waiting",
            "resume_policy": "exactly_once",
        },
        implementation=None,
    )


def test_pending_decisions_have_frozen_json_roundtrip() -> None:
    task_pending = _task_pending()
    goal_pending = _goal_pending()
    state = LongTaskState(
        root_run_id="root-1",
        revision=7,
        intents=(),
        graph=graph(task("review"), revision=3),
        pending_task_decisions=(task_pending,),
        pending_goal_decisions=(goal_pending,),
    )

    snapshot = state.snapshot()
    encoded = json.dumps(snapshot, sort_keys=True)
    restored = long_task_state_from_snapshot(json.loads(encoded))

    assert restored == state
    with pytest.raises(TypeError):
        task_pending.prompt["title"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        goal_pending.criterion_gaps[0]["reason"] = "changed"  # type: ignore[index]


def test_task_decision_consumes_once_and_replays_same_exact_response() -> None:
    pending = _task_pending()
    response = {"decision": "approve", "response": {"comment": "looks good"}}

    first = consume_pending_task_decision(
        pending,
        response=response,
        observed_root_revision=7,
        observed_graph_revision=3,
        commit_root_revision=8,
    )
    replay = consume_pending_task_decision(
        first.decision,
        response=response,
        observed_root_revision=99,
        observed_graph_revision=99,
        commit_root_revision=100,
    )

    assert first.replayed is False
    assert first.decision.status == "consumed"
    assert first.decision.response_hash == compute_fingerprint(response)
    assert first.decision.consumed_root_revision == 8
    assert replay.replayed is True
    assert replay.decision is first.decision
    with pytest.raises(PendingDecisionConflict, match="different response"):
        consume_pending_task_decision(
            first.decision,
            response={"decision": "reject", "response": {"comment": "no"}},
            observed_root_revision=8,
            observed_graph_revision=3,
            commit_root_revision=9,
        )


@pytest.mark.parametrize(
    ("root_revision", "graph_revision", "commit_revision", "message"),
    [
        (6, 3, 8, "expects root revision"),
        (7, 4, 8, "expects graph revision"),
        (7, 3, 7, "advance monotonically"),
    ],
)
def test_pending_decision_rejects_stale_revisions(
    root_revision: int,
    graph_revision: int,
    commit_revision: int,
    message: str,
) -> None:
    with pytest.raises(PendingDecisionStale, match=message):
        consume_pending_task_decision(
            _task_pending(),
            response={"decision": "approve"},
            observed_root_revision=root_revision,
            observed_graph_revision=graph_revision,
            commit_root_revision=commit_revision,
        )


def test_goal_decision_uses_same_idempotency_and_allowed_decision_rules() -> None:
    pending = _goal_pending()
    consumed = consume_pending_goal_decision(
        pending,
        response={"kind": "repair", "direction": "collect evidence"},
        observed_root_revision=7,
        observed_graph_revision=3,
        commit_root_revision=8,
    )

    assert consumed.decision.status == "consumed"
    assert consumed.decision.response["kind"] == "repair"
    with pytest.raises(PendingDecisionError, match="not allowed"):
        consume_pending_goal_decision(
            pending,
            response={"decision": "approve"},
            observed_root_revision=7,
            observed_graph_revision=3,
            commit_root_revision=8,
        )


def test_pinned_human_contract_validates_and_freezes_prompt_and_response() -> None:
    contract = parse_human_task_contract(_human_component())

    prompt = validate_human_prompt(contract, {"title": "Review this result"})
    response = validate_human_response(
        contract,
        {"decision": "approve", "comment": "verified"},
    )

    assert contract.decision_class == "judgment"
    assert contract.allowed_decisions == ("approve", "reject")
    assert contract.authority_requirement["role"] == "reviewer"
    assert response.decision == "approve"
    assert response.response_hash == compute_fingerprint(json_value(response.envelope))
    with pytest.raises(TypeError):
        prompt["title"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        response.response["comment"] = "changed"  # type: ignore[index]


def test_human_response_rejects_schema_decision_and_contract_mismatch() -> None:
    contract = parse_human_task_contract(_human_component())

    with pytest.raises(ExecutorContractError, match="schema validation"):
        validate_human_response(
            contract,
            {"decision": "approve"},
        )
    with pytest.raises(ExecutorContractError, match="not allowed"):
        validate_human_response(
            contract,
            {"decision": "redirect", "comment": "different direction"},
        )
    with pytest.raises(ExecutorContractError, match="conflicts"):
        validate_human_response(
            contract,
            {"decision": "approve", "comment": "verified"},
            decision="reject",
        )

    wrong = PinnedComponent(
        id="not-human",
        version="1",
        kind="parent_inline",
        implementation_digest="sha256:inline",
        protocol_version="parent-inline-v1",
        input_schema_id="inline-input-v1",
        output_schema_id="inline-output-v1",
        supported_outcomes=("passed",),
        configuration={},
        implementation=lambda _inputs, *, idempotency_key: idempotency_key,
    )
    with pytest.raises(ExecutorContractError, match="human_contract"):
        parse_human_task_contract(wrong)
