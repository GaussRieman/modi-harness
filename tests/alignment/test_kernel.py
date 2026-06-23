"""AlignmentKernel: model-first intent-fit, deterministic hard floor (plan N4.3).

The kernel asks the model "is this action inside the intent field?" first, then
applies a narrow deterministic floor that can only *raise* severity — it never
lowers the model below what a structural risk demands, and never replaces the
model's reasoning when the model is available.
"""
from __future__ import annotations

from typing import Any

from modi_harness.actions import from_tool_call
from modi_harness.autonomy.scope import derive_autonomy_scope
from modi_harness.intent.types import (
    HumanIntentContext,
    IntentBoundary,
    IntentClarity,
    IntentStage,
)

# --- builders ----------------------------------------------------------------


def _spec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "fetch_url",
        "description": "",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": "L1",
        "side_effect": False,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": False,
        "dry_run_supported": False,
        "tags": [],
        "kind": "regular",
        "subagent_target": None,
    }
    base.update(over)
    return base


def _tc(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_call_id": "tc-1",
        "tool_name": name,
        "arguments": args,
        "malformed": False,
        "parse_error": None,
    }


def _stage(kind: str = "explore") -> IntentStage:
    return IntentStage(
        id=f"stage-{kind}",
        kind=kind,  # type: ignore[typeddict-item]
        goal="g",
        exit_criteria=[],
        judgment_required_before_exit=False,
    )


def _intent(*, boundaries: list[IntentBoundary] | None = None) -> HumanIntentContext:
    return HumanIntentContext(
        version=2,
        goal="research X",
        desired_outcome=None,
        boundaries=boundaries or [],
        non_goals=[],
        success_criteria=[],
        current_stage=_stage(),
        responsibility={
            "owner": None,
            "on_behalf_of": None,
            "irreversible_requires_judgment": True,
            "notes": None,
        },
        escalation={"default_action": "ask", "escalate_on": [], "quiet": False},
        tradeoffs={},
        confirmed_inputs={},
        decisions=[],
        corrections=[],
    )


def _clarity(level: str) -> IntentClarity:
    return IntentClarity(level=level, unknowns=[], assumptions=[], confidence=0.9)  # type: ignore[typeddict-item]


def _hard_deny_boundary() -> IntentBoundary:
    return IntentBoundary(
        id="b-hard",
        kind="external_commitment",
        statement="never contact competitors",
        severity="hard",
        escalation="deny",
    )


def _proposal(spec: dict[str, Any], args: dict[str, Any]):
    return from_tool_call(
        _tc(spec["name"], args), spec=spec, intent_version=2, stage_id="stage-explore"  # type: ignore[arg-type]
    )


# --- tests -------------------------------------------------------------------


def test_model_aligned_allows_when_no_floor_blocks() -> None:
    from modi_harness.alignment.kernel import align_action

    intent = _intent()
    clarity = _clarity("stable")  # delegated, generous risk budget
    scope = derive_autonomy_scope(clarity, intent)
    proposal = _proposal(_spec(risk_level="L1"), {"url": "file:///tmp/x.json"})

    def judge(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"verdict": "allow", "matched_boundary_ids": [], "drift": False, "reason": "ok"}

    d = align_action(proposal=proposal, intent=intent, scope=scope, judge=judge)
    assert d["decision"] == "allow"
    assert d["model_judged"] is True
    assert d["intent_version"] == 2
    assert d["stage_id"] == "stage-explore"


def test_hard_deny_boundary_denies_even_if_model_aligned() -> None:
    from modi_harness.alignment.kernel import align_action

    intent = _intent(boundaries=[_hard_deny_boundary()])
    clarity = _clarity("stable")
    scope = derive_autonomy_scope(clarity, intent)  # hard/deny -> constrained
    proposal = _proposal(_spec(risk_level="L1"), {"url": "https://competitor.com"})

    # Model thinks it's fine and even reports the boundary as matched.
    def judge(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "verdict": "allow",
            "matched_boundary_ids": ["b-hard"],
            "drift": False,
            "reason": "looks ok to me",
        }

    d = align_action(proposal=proposal, intent=intent, scope=scope, judge=judge)
    assert d["decision"] == "deny"
    assert any(h["boundary_id"] == "b-hard" for h in d["boundary_hits"])


def test_thin_clarity_external_side_effect_asks_judgment() -> None:
    from modi_harness.alignment.kernel import align_action

    intent = _intent()
    clarity = _clarity("thin")  # guided
    scope = derive_autonomy_scope(clarity, intent)
    proposal = _proposal(_spec(risk_level="L1"), {"url": "https://api.example.com"})

    # Model says allow, but external_commitment is a guided judgment trigger.
    def judge(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"verdict": "allow", "matched_boundary_ids": [], "drift": False, "reason": "ok"}

    d = align_action(proposal=proposal, intent=intent, scope=scope, judge=judge)
    assert d["decision"] == "ask_judgment"


def test_soft_boundary_follows_model_verdict() -> None:
    from modi_harness.alignment.kernel import align_action

    soft = IntentBoundary(
        id="b-soft",
        kind="scope",
        statement="prefer primary sources",
        severity="soft",
        escalation="ask",
    )
    intent = _intent(boundaries=[soft])
    clarity = _clarity("stable")
    scope = derive_autonomy_scope(clarity, intent)
    proposal = _proposal(_spec(risk_level="L1"), {"url": "file:///tmp/x"})

    def judge_redirect(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "verdict": "redirect",
            "matched_boundary_ids": ["b-soft"],
            "drift": True,
            "reason": "steer to primary sources",
        }

    d = align_action(proposal=proposal, intent=intent, scope=scope, judge=judge_redirect)
    assert d["decision"] == "redirect"
    assert any(h["boundary_id"] == "b-soft" for h in d["boundary_hits"])


def test_cold_start_no_model_still_decides_safely() -> None:
    from modi_harness.alignment.kernel import align_action

    intent = _intent()
    clarity = _clarity("thin")
    scope = derive_autonomy_scope(clarity, intent)
    # external commitment under guided -> floor asks judgment even with no model
    proposal = _proposal(_spec(risk_level="L1"), {"url": "https://api.example.com"})

    d = align_action(proposal=proposal, intent=intent, scope=scope, judge=None)
    assert d["decision"] == "ask_judgment"
    assert d["model_judged"] is False


def test_disallowed_action_kind_denied() -> None:
    from modi_harness.alignment.kernel import align_action

    # constrained scope (hard/deny boundary) disallows tool_call entirely.
    intent = _intent(boundaries=[_hard_deny_boundary()])
    clarity = _clarity("stable")
    scope = derive_autonomy_scope(clarity, intent)
    proposal = _proposal(_spec(name="calc", risk_level="L0"), {"a": 1})

    def judge(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"verdict": "allow", "matched_boundary_ids": [], "drift": False, "reason": "ok"}

    d = align_action(proposal=proposal, intent=intent, scope=scope, judge=judge)
    assert d["decision"] == "deny"


def test_judge_exception_falls_back_to_floor() -> None:
    from modi_harness.alignment.kernel import align_action

    intent = _intent()
    clarity = _clarity("stable")
    scope = derive_autonomy_scope(clarity, intent)
    proposal = _proposal(_spec(risk_level="L1"), {"url": "file:///tmp/x"})

    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("gateway down")

    d = align_action(proposal=proposal, intent=intent, scope=scope, judge=boom)
    # No usable model verdict -> floor-only, but nothing structural blocks -> allow.
    assert d["decision"] == "allow"
    assert d["model_judged"] is False
