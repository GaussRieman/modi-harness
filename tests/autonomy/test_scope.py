"""Tests for autonomy scope derivation (plan N2.2).

Autonomy is derived from intent clarity and active boundaries — never selected.
"""
from __future__ import annotations

from modi_harness.autonomy import derive_autonomy_scope, mode_for_clarity
from modi_harness.intent.types import HumanIntentContext, IntentBoundary, IntentClarity


def _clarity(level: str, confidence: float = 0.8) -> IntentClarity:
    return IntentClarity(
        level=level,  # type: ignore[typeddict-item]
        unknowns=[],
        assumptions=[],
        confidence=confidence,
    )


def _ctx(boundaries: list[IntentBoundary] | None = None) -> HumanIntentContext:
    return HumanIntentContext(
        version=1,
        goal="g",
        desired_outcome=None,
        boundaries=boundaries or [],
        non_goals=[],
        success_criteria=[],
        current_stage={
            "id": "s1",
            "kind": "explore",
            "goal": "",
            "exit_criteria": [],
            "judgment_required_before_exit": False,
        },
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


def test_clarity_to_mode_mapping() -> None:
    assert mode_for_clarity("thin") == "guided"
    assert mode_for_clarity("partial") == "guided"
    assert mode_for_clarity("operational") == "bounded"
    assert mode_for_clarity("stable") == "delegated"


def test_scope_mode_follows_clarity() -> None:
    assert derive_autonomy_scope(_clarity("thin"), _ctx())["mode"] == "guided"
    assert derive_autonomy_scope(_clarity("operational"), _ctx())["mode"] == "bounded"
    assert derive_autonomy_scope(_clarity("stable"), _ctx())["mode"] == "delegated"


def test_hard_deny_boundary_forces_constrained() -> None:
    red_line: IntentBoundary = {
        "id": "b1",
        "kind": "risk",
        "statement": "never touch production",
        "severity": "hard",
        "escalation": "deny",
    }
    scope = derive_autonomy_scope(_clarity("stable"), _ctx([red_line]))
    assert scope["mode"] == "constrained"
    assert scope["max_tool_risk_without_judgment"] == "L0"


def test_hard_ask_boundary_does_not_force_constrained() -> None:
    # An agent's safety constraint is hard but escalates with 'ask' — it raises
    # judgment only when hit, so the agent still runs in its clarity-derived mode.
    safety: IntentBoundary = {
        "id": "b2",
        "kind": "risk",
        "statement": "do not invent facts outside provided sources",
        "severity": "hard",
        "escalation": "ask",
    }
    scope = derive_autonomy_scope(_clarity("operational"), _ctx([safety]))
    assert scope["mode"] == "bounded"


def test_source_collection_allowed_under_guided() -> None:
    # Thin intent → guided. Fetching sources is a low-risk (L1) tool_call and
    # must be allowed without a judgment so the agent can gather context.
    scope = derive_autonomy_scope(_clarity("thin"), _ctx())
    assert scope["mode"] == "guided"
    assert "tool_call" in scope["allowed_action_kinds"]
    assert scope["max_tool_risk_without_judgment"] == "L1"


def test_scope_embeds_clarity() -> None:
    clarity = _clarity("operational", confidence=0.66)
    scope = derive_autonomy_scope(clarity, _ctx())
    assert scope["intent_clarity"]["confidence"] == 0.66
