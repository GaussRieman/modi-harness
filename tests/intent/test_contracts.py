"""Architecture marker tests for the intent-aligned runtime redesign.

These intentionally fail until the new intent center exists (plan N1). They
name the new concepts so the redesign cannot silently regress to a
governance-first center.
"""
from __future__ import annotations


def test_human_intent_context_shape_exists() -> None:
    """HumanIntentContext is the primary human-facing runtime field.

    It must carry the intent field — goal, boundaries, stage, success
    criteria — not just interaction history like the old HumanContext.
    """
    from typing import get_type_hints

    from modi_harness.intent import HumanIntentContext

    hints = get_type_hints(HumanIntentContext)
    required = {
        "version",
        "goal",
        "boundaries",
        "non_goals",
        "success_criteria",
        "current_stage",
        "decisions",
        "corrections",
    }
    assert required <= set(hints), f"missing intent fields: {required - set(hints)}"


def _stage() -> dict:
    from modi_harness.intent import IntentStage

    return IntentStage(
        id="stg-1",
        kind="clarify",
        goal="figure out what the user wants",
        exit_criteria=[],
        judgment_required_before_exit=False,
    )


def test_construct_minimal_intent_context() -> None:
    """A thin intent is a valid, fully-typed context — not a failure state."""
    from modi_harness.intent import (
        EscalationPreference,
        HumanIntentContext,
        ResponsibilityContext,
    )

    ctx = HumanIntentContext(
        version=1,
        goal="research something",
        desired_outcome=None,
        boundaries=[],
        non_goals=[],
        success_criteria=[],
        current_stage=_stage(),
        responsibility=ResponsibilityContext(
            owner=None,
            on_behalf_of=None,
            irreversible_requires_judgment=True,
            notes=None,
        ),
        escalation=EscalationPreference(
            default_action="ask", escalate_on=[], quiet=False
        ),
        tradeoffs={},
        confirmed_inputs={},
        decisions=[],
        corrections=[],
    )
    assert ctx["version"] == 1
    assert ctx["current_stage"]["kind"] == "clarify"


def test_construct_full_intent_context_with_boundaries_and_judgment() -> None:
    from modi_harness.intent import (
        EscalationPreference,
        HumanIntentContext,
        HumanJudgment,
        IntentBoundary,
        IntentCorrection,
        ResponsibilityContext,
    )

    boundary = IntentBoundary(
        id="b-1",
        kind="data",
        statement="do not invent facts outside provided sources",
        severity="hard",
        escalation="deny",
    )
    judgment = HumanJudgment(
        id="j-1",
        kind="constrain",
        target_action_id=None,
        target_stage_id=None,
        rationale="keep it grounded",
        intent_updates={"add_boundaries": [boundary]},
        created_at="2026-06-23T00:00:00.000Z",
    )
    ctx = HumanIntentContext(
        version=2,
        goal="produce a grounded briefing",
        desired_outcome="a cited research briefing",
        boundaries=[boundary],
        non_goals=["do not give legal advice"],
        success_criteria=["every claim cites a provided source"],
        current_stage=_stage(),
        responsibility=ResponsibilityContext(
            owner="analyst",
            on_behalf_of="customer",
            irreversible_requires_judgment=True,
            notes=None,
        ),
        escalation=EscalationPreference(
            default_action="ask", escalate_on=["external_commitment"], quiet=False
        ),
        tradeoffs={"speed_vs_coverage": "favor coverage"},
        confirmed_inputs={"source_urls": ["https://example.com"]},
        decisions=[judgment],
        corrections=[
            IntentCorrection(
                id="c-1",
                created_at="2026-06-23T00:00:00.000Z",
                summary="narrowed scope to provided sources",
                detail=None,
            )
        ],
    )
    assert ctx["boundaries"][0]["severity"] == "hard"
    assert ctx["decisions"][0]["intent_updates"]["add_boundaries"][0]["id"] == "b-1"
