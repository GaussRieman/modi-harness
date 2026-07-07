from __future__ import annotations

import pytest

from modi_harness.loop import (
    begin_step_record,
    build_step_context,
    decide_loop_continuation,
    initialize_loop_state,
    slow_model_step_decision,
    validate_brain_intent_patch,
    validate_step_decision,
)
from modi_harness.loop.types import (
    BrainIntentPatchValidationError,
    StepValidationError,
)


def _loop():
    return initialize_loop_state(
        run_id="run_1",
        agent_name="agent",
        intent_version=1,
        stage_id="clarify",
        max_auto_steps=5,
    )


def test_slow_model_step_decision_is_valid() -> None:
    decision = slow_model_step_decision(step_id="step_1")
    validate_step_decision(decision)
    assert decision["reasoning_mode"] == "slow"
    assert decision["continuation_basis"]["source"] == "slow_plan"


def test_build_step_context_carries_brain_planning_inputs() -> None:
    loop = _loop()
    context = build_step_context(
        step_id="step_1",
        loop=loop,
        event={"kind": "user_message"},
        intent={
            "goal": "ship it",
            "current_stage": {"id": "plan", "kind": "plan"},
        },
        intent_clarity={"level": "clear"},
        autonomy_scope={"mode": "bounded"},
        agent_profile={
            "name": "agent",
            "description": "demo",
            "default_tools": ["lookup"],
            "default_skills": ["research"],
            "permission_profile": {"mode": "auto"},
            "output_contract": None,
            "metadata": {"brain": {"mode": "slow"}},
        },
        recent_steps=[],
        available_capabilities={"tools": {"lookup": {"risk_level": "L0"}}},
        brain_spec={"mode": "slow"},
    )

    assert context["step_id"] == "step_1"
    assert context["loop"] == loop
    assert context["intent"]["goal"] == "ship it"
    assert context["stage"]["id"] == "plan"
    assert context["intent_clarity"]["level"] == "clear"
    assert context["autonomy_scope"]["mode"] == "bounded"
    assert context["agent_state"]["default_tools"] == ["lookup"]
    assert context["available_capabilities"]["tools"]["lookup"]["risk_level"] == "L0"
    assert context["brain_spec"] == {"mode": "slow"}


def test_brain_intent_patch_rejects_stage_mutation() -> None:
    with pytest.raises(BrainIntentPatchValidationError):
        validate_brain_intent_patch({"set_stage": {"id": "execute"}})  # type: ignore[typeddict-unknown-key]


def test_brain_intent_patch_rejects_unknown_key() -> None:
    with pytest.raises(BrainIntentPatchValidationError):
        validate_brain_intent_patch({"surprise": True})  # type: ignore[typeddict-unknown-key]


def test_required_judgment_cannot_carry_operation() -> None:
    decision = slow_model_step_decision(step_id="step_1")
    decision["human_judgment"] = {
        "required": True,
        "reason": "needs human call",
        "trigger": "operation_risk",
    }
    decision["continuation"] = "wait"
    decision["operation"] = {
        "kind": "tool",
        "summary": "call tool",
        "target": "tool",
        "arguments": {},
        "expected_outcome": None,
    }

    with pytest.raises(StepValidationError):
        validate_step_decision(decision)


def test_continue_requires_continuation_basis() -> None:
    decision = slow_model_step_decision(step_id="step_1")
    decision["continuation_basis"] = None

    with pytest.raises(StepValidationError):
        validate_step_decision(decision)


def test_loop_continuation_waits_on_human_judgment() -> None:
    loop = _loop()
    decision = slow_model_step_decision(step_id="step_1")
    decision["human_judgment"] = {
        "required": True,
        "reason": "needs human call",
        "trigger": "boundary",
    }
    decision["continuation"] = "wait"
    decision["continuation_basis"] = None
    record = begin_step_record(loop=loop, decision=decision)
    record["status"] = "waiting"

    continuation = decide_loop_continuation(loop=loop, step=record)

    assert continuation["outcome"] == "wait_for_judgment"
    assert "human_judgment_required" in continuation["blockers"]
