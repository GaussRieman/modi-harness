from __future__ import annotations

from modi_harness.brain import (
    HARD_BOUNDARY_RULE_ID,
    MISSING_INPUT_RULE_ID,
    STAGE_EXIT_RULE_ID,
    RuleBrain,
    SlowModelBrain,
    StaticStructuredSlowPlanner,
)
from modi_harness.loop import build_step_context, initialize_loop_state
from modi_harness.loop.types import StepDecision


def _context(
    *,
    stage_kind: str = "clarify",
    unknowns: list[str] | None = None,
    brain_spec: dict | None = None,
    confirmed_inputs: dict | None = None,
    event: dict | None = None,
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
        event=event or {"kind": "test"},
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


def _slow_brain() -> SlowModelBrain:
    return SlowModelBrain(
        planner=StaticStructuredSlowPlanner(
            StepDecision(
                id="step_1",
                step_kind="plan",
                reasoning_mode="slow",
                reason="structured slow fallback",
                rule_ref=None,
                intent_patch=None,
                ask=None,
                operation=None,
                expected_state_change=None,
                postcheck=None,
                continuation="continue",
                human_judgment={
                    "required": False,
                    "reason": "slow planner remains inside autonomy scope",
                    "trigger": "none",
                },
                continuation_basis={
                    "source": "slow_plan",
                    "reference": "test",
                    "reason": "continue after structured slow plan",
                },
            )
        )
    )


def test_rule_brain_asks_when_explicit_required_input_is_missing() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(
            brain_spec={"fast_rules": {"required_inputs": ["deadline", "desired_format"]}},
            confirmed_inputs={"desired_format": "markdown"},
        )
    )

    assert decision["reasoning_mode"] == "fast"
    assert decision["step_kind"] == "clarify"
    assert decision["rule_ref"] == MISSING_INPUT_RULE_ID
    assert decision["ask"]["prompt"] == "Please provide: deadline"
    assert decision["ask"]["field"] == "deadline"
    assert decision["ask"]["input_type"] == "text"
    assert decision["ask"]["required"] is True
    assert decision["continuation"] == "wait"
    assert decision["human_judgment"]["trigger"] == "missing_input"
    assert decision["operation"] is None


def test_rule_brain_asks_for_url_list_when_url_input_is_missing() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(
            brain_spec={"fast_rules": {"required_inputs": ["source_urls"]}},
            confirmed_inputs={},
        )
    )

    assert decision["reasoning_mode"] == "fast"
    assert decision["ask"]["field"] == "source_urls"
    assert decision["ask"]["input_type"] == "url_list"
    assert decision["continuation"] == "wait"


def test_rule_brain_does_not_treat_general_unknowns_as_required_input() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(unknowns=["success criteria and boundaries are not established"])
    )

    assert decision["reasoning_mode"] == "slow"
    assert decision["continuation_basis"]["source"] == "slow_plan"


def test_rule_brain_falls_back_to_slow_when_no_rule_matches() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(stage_kind="plan", unknowns=["not enough detail"])
    )

    assert decision["reasoning_mode"] == "slow"
    assert decision["continuation_basis"]["source"] == "slow_plan"


def test_rule_brain_falls_back_to_slow_when_fast_rule_errors() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(
            brain_spec={
                "fast_rules": {
                    "stage_exit_transitions": "invalid",
                    "required_inputs": ["source_urls"],
                }
            },
            confirmed_inputs={"source_urls": ["https://example.test"]},
            event={"kind": "test", "stage_exit_criteria_satisfied": True},
        )
    )

    assert decision["reasoning_mode"] == "slow"
    assert decision["continuation_basis"]["source"] == "slow_plan"


def test_rule_brain_proposes_configured_stage_transition_on_explicit_exit() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(
            stage_kind="plan",
            event={"kind": "test", "stage_exit_criteria_satisfied": True},
            brain_spec={
                "fast_rules": {
                    "stage_exit_transitions": [
                        {
                            "from": "plan",
                            "to": "execute",
                            "when": "exit_criteria_satisfied",
                        }
                    ]
                }
            },
        )
    )

    assert decision["reasoning_mode"] == "fast"
    assert decision["rule_ref"] == STAGE_EXIT_RULE_ID
    assert decision["step_kind"] == "act"
    assert decision["operation"]["kind"] == "stage_transition"
    assert decision["operation"]["target"] == "transition_stage"
    assert decision["operation"]["arguments"]["to"] == "execute"
    assert decision["continuation"] == "continue"
    assert decision["continuation_basis"]["source"] == "stage_exit_criteria"


def test_rule_brain_does_not_transition_without_explicit_exit_event() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(
            stage_kind="plan",
            brain_spec={
                "fast_rules": {
                    "stage_exit_transitions": [
                        {"from": "plan", "to": "execute"}
                    ]
                }
            },
        )
    )

    assert decision["reasoning_mode"] == "slow"
    assert decision["continuation_basis"]["source"] == "slow_plan"


def test_rule_brain_waits_on_explicit_hard_boundary_event() -> None:
    decision = RuleBrain(fallback=_slow_brain()).plan_step(
        _context(
            event={
                "kind": "test",
                "hard_boundary_triggered": {
                    "id": "b-hard",
                    "reason": "external commitment would cross a hard boundary",
                },
            }
        )
    )

    assert decision["reasoning_mode"] == "fast"
    assert decision["rule_ref"] == HARD_BOUNDARY_RULE_ID
    assert decision["step_kind"] == "handoff"
    assert decision["operation"] is None
    assert decision["continuation"] == "wait"
    assert decision["human_judgment"]["required"] is True
    assert decision["human_judgment"]["trigger"] == "boundary"
    assert decision["continuation_basis"]["reference"] == "b-hard"
