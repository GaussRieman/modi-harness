"""Intent-aligned runtime: the human intent field and its derivation.

This package holds the new center of Modi Harness — the durable human intent
field (`HumanIntentContext`), its clarity estimate, boundaries, and stages —
that governance now supports rather than leads.
"""

from __future__ import annotations

from modi_harness.intent.types import (
    BoundaryEscalation,
    BoundarySeverity,
    EscalationPreference,
    HumanIntentContext,
    HumanJudgment,
    HumanJudgmentKind,
    IntentBoundary,
    IntentBoundaryKind,
    IntentClarity,
    IntentClarityLevel,
    IntentCorrection,
    IntentPatch,
    IntentStage,
    IntentStageKind,
    ResponsibilityContext,
)

__all__ = [
    "BoundaryEscalation",
    "BoundarySeverity",
    "EscalationPreference",
    "HumanIntentContext",
    "HumanJudgment",
    "HumanJudgmentKind",
    "IntentBoundary",
    "IntentBoundaryKind",
    "IntentClarity",
    "IntentClarityLevel",
    "IntentCorrection",
    "IntentPatch",
    "IntentStage",
    "IntentStageKind",
    "ResponsibilityContext",
]
