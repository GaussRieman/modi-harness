from __future__ import annotations

import pytest

from modi_harness.brain import BrainPlanningError, SlowModelBrain
from modi_harness.loop import build_step_context, initialize_loop_state


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
