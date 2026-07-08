from __future__ import annotations

import pytest

from modi_harness.brain import BrainPlanningError, SlowModelBrain, StaticStructuredSlowPlanner
from modi_harness.loop import build_step_context, initialize_loop_state
from modi_harness.loop.types import StepDecision


def _context():
    loop = initialize_loop_state(
        run_id="run_1",
        agent_name="agent",
        intent_version=1,
        stage_id="clarify",
        max_auto_steps=5,
    )
    return build_step_context(
        step_id="step_1",
        loop=loop,
        event={"kind": "test"},
        intent={"current_stage": {"id": "clarify", "kind": "clarify"}},
        intent_clarity={"level": "clear"},
        autonomy_scope={"mode": "bounded"},
        agent_profile={
            "name": "agent",
            "description": "demo",
            "default_tools": ["lookup"],
            "default_skills": [],
            "permission_profile": None,
            "output_contract": None,
            "metadata": {},
        },
        recent_steps=[],
        available_capabilities={"tools": {"lookup": {"risk_level": "L0"}}},
    )


def test_slow_model_brain_returns_valid_slow_plan_decision() -> None:
    decision = SlowModelBrain().plan_step(_context())

    assert decision["id"] == "step_1"
    assert decision["step_kind"] == "plan"
    assert decision["reasoning_mode"] == "slow"
    assert decision["human_judgment"]["required"] is False
    assert decision["continuation_basis"]["source"] == "slow_plan"


def test_slow_model_brain_requires_step_id() -> None:
    context = _context()
    context.pop("step_id")

    with pytest.raises(BrainPlanningError):
        SlowModelBrain().plan_step(context)


def test_slow_model_brain_accepts_valid_structured_planner_decision() -> None:
    planner = StaticStructuredSlowPlanner(
        StepDecision(
            id="planner-id-is-overwritten",
            step_kind="verify",
            reasoning_mode="fast",
            reason="check the draft against the output contract",
            rule_ref="ignored",
            intent_patch=None,
            ask=None,
            operation=None,
            expected_state_change={"verified": True},
            postcheck={"conditions": ["draft matches schema"], "reason": "contract check"},
            continuation="continue",
            human_judgment={
                "required": False,
                "reason": "verification is inside the bounded autonomy scope",
                "trigger": "none",
            },
            continuation_basis={
                "source": "slow_plan",
                "reference": "structured-planner",
                "reason": "continue after structured slow verification",
            },
        )
    )

    decision = SlowModelBrain(planner=planner).plan_step(_context())

    assert decision["id"] == "step_1"
    assert decision["reasoning_mode"] == "slow"
    assert decision["rule_ref"] is None
    assert decision["step_kind"] == "verify"
    assert decision["continuation_basis"]["reference"] == "structured-planner"


def test_slow_model_brain_routes_invalid_structured_decision_to_judgment() -> None:
    planner = StaticStructuredSlowPlanner(
        {
            "id": "bad",
            "step_kind": "act",
            "reasoning_mode": "slow",
            "reason": "unsafe",
            "rule_ref": None,
            "intent_patch": None,
            "ask": None,
            "operation": {
                "kind": "tool",
                "summary": "call tool",
                "target": "send_email",
                "arguments": {},
                "expected_outcome": None,
            },
            "expected_state_change": None,
            "postcheck": None,
            "continuation": "wait",
            "human_judgment": {
                "required": True,
                "reason": "planner mixed judgment and operation",
                "trigger": "operation_risk",
            },
            "continuation_basis": None,
        }
    )

    decision = SlowModelBrain(planner=planner).plan_step(_context())

    assert decision["id"] == "step_1"
    assert decision["step_kind"] == "handoff"
    assert decision["reasoning_mode"] == "slow"
    assert decision["operation"] is None
    assert decision["ask"] is not None
    assert decision["human_judgment"]["required"] is True
    assert decision["human_judgment"]["trigger"] == "failure_recovery"
