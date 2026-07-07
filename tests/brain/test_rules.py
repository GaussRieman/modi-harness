from __future__ import annotations

from modi_harness.brain import MISSING_INPUT_RULE_ID, RuleBrain, SlowModelBrain
from modi_harness.loop import build_step_context, initialize_loop_state


def _context(
    *,
    stage_kind: str = "clarify",
    unknowns: list[str] | None = None,
    brain_spec: dict | None = None,
    confirmed_inputs: dict | None = None,
):
    loop = initialize_loop_state(
        run_id="run_1",
        agent_name="agent",
        intent_version=1,
        stage_id=stage_kind,
        max_auto_steps=5,
    )
    return build_step_context(
        step_id="step_1",
        loop=loop,
        event={"kind": "test"},
        intent={
            "confirmed_inputs": confirmed_inputs or {},
            "current_stage": {
                "id": stage_kind,
                "kind": stage_kind,
                "goal": "test",
                "exit_criteria": [],
                "judgment_required_before_exit": False,
            }
        },
        intent_clarity={
            "level": "partial",
            "unknowns": unknowns or [],
            "assumptions": [],
            "confidence": 0.3,
        },
        autonomy_scope={"mode": "guided"},
        agent_profile={
            "name": "agent",
            "description": "demo",
            "default_tools": [],
            "default_skills": [],
            "permission_profile": None,
            "output_contract": None,
            "metadata": {},
        },
        recent_steps=[],
        available_capabilities={"tools": {}},
        brain_spec=brain_spec,
    )


def test_rule_brain_asks_when_explicit_required_input_is_missing() -> None:
    decision = RuleBrain(fallback=SlowModelBrain()).plan_step(
        _context(
            brain_spec={"fast_rules": {"required_inputs": ["deadline", "desired_format"]}},
            confirmed_inputs={"desired_format": "markdown"},
        )
    )

    assert decision["reasoning_mode"] == "fast"
    assert decision["step_kind"] == "clarify"
    assert decision["rule_ref"] == MISSING_INPUT_RULE_ID
    assert decision["ask"]["prompt"] == "Please provide: deadline"
    assert decision["continuation"] == "wait"
    assert decision["human_judgment"]["trigger"] == "missing_input"
    assert decision["operation"] is None


def test_rule_brain_does_not_treat_general_unknowns_as_required_input() -> None:
    decision = RuleBrain(fallback=SlowModelBrain()).plan_step(
        _context(unknowns=["success criteria and boundaries are not established"])
    )

    assert decision["reasoning_mode"] == "slow"
    assert decision["continuation_basis"]["source"] == "slow_plan"


def test_rule_brain_falls_back_to_slow_when_no_rule_matches() -> None:
    decision = RuleBrain(fallback=SlowModelBrain()).plan_step(
        _context(stage_kind="plan", unknowns=["not enough detail"])
    )

    assert decision["reasoning_mode"] == "slow"
    assert decision["continuation_basis"]["source"] == "slow_plan"
