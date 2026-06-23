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
    from modi_harness.intent import HumanIntentContext  # noqa: F401
    from typing import get_type_hints

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
