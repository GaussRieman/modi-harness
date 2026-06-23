"""Estimate how operational the current intent is (plan N2.1, spec D2).

**Model-first, deterministically floored.** The model is the primary estimator:
it reads the task to act, so it also judges clarity and emits a ``ClarityVerdict``
through a structured-output contract. This module does *not* pre-decide clarity
with a heuristic and hand the model a verdict.

Determinism enters only as a **floor and a guard**:

- ``clarity_ceiling`` — the highest level the input shape can justify, so a
  mis-estimating model cannot unlock more autonomy than the input warrants
  (e.g. no goal and no materials can never be reported as ``stable``);
- ``cold_start_clarity`` — a safe estimate when no verdict is available (cold
  start or model error), so a thin intent still proceeds.

If the model proves to mis-estimate in practice, tighten the floor — do not
replace the model with the heuristic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from modi_harness.intent.extractor import _GOAL_KEYS, _MATERIAL_KEYS
from modi_harness.intent.types import (
    HumanIntentContext,
    IntentClarity,
    IntentClarityLevel,
)

# Increasing order of how operational the intent is.
_LEVEL_ORDER: dict[IntentClarityLevel, int] = {
    "thin": 0,
    "partial": 1,
    "operational": 2,
    "stable": 3,
}
_ORDER_LEVEL: dict[int, IntentClarityLevel] = {v: k for k, v in _LEVEL_ORDER.items()}


class ClarityVerdict(TypedDict):
    """The model's structured estimate of intent clarity."""

    level: IntentClarityLevel
    unknowns: list[str]
    assumptions: list[str]
    confidence: float


# JSON Schema for the structured-output contract presented to the model.
CLARITY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "level": {
            "type": "string",
            "enum": ["thin", "partial", "operational", "stable"],
            "description": "How operational the human intent currently is.",
        },
        "unknowns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What the agent still does not know about the intent.",
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Assumptions the agent is making to proceed.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Confidence in the level estimate, 0..1.",
        },
    },
    "required": ["level", "unknowns", "assumptions", "confidence"],
}


def _has_real_goal(ctx: HumanIntentContext) -> bool:
    inputs = ctx["confirmed_inputs"]
    if any(inputs.get(key) for key in _GOAL_KEYS):
        return True
    goal = ctx["goal"].strip()
    # An empty payload yields ``str({})`` as the fallback goal; reject that and
    # other bare serialized-payload shapes as non-goals.
    return bool(goal) and not (goal.startswith("{") and goal.endswith("}"))


def _has_materials(ctx: HumanIntentContext) -> bool:
    inputs = ctx["confirmed_inputs"]
    return any(inputs.get(key) for key in _MATERIAL_KEYS)


def _has_criteria(ctx: HumanIntentContext) -> bool:
    # Reaching ``stable`` needs both an explicit success target and a declared
    # edge of the field — either alone caps the intent at ``operational``.
    return bool(ctx["success_criteria"]) and bool(ctx["boundaries"])


def clarity_ceiling(ctx: HumanIntentContext) -> IntentClarityLevel:
    """The highest clarity the input shape can justify (the deterministic floor).

    A model estimate above this ceiling is clamped down to it.
    """
    if not _has_real_goal(ctx):
        return "thin"
    if not _has_materials(ctx):
        return "partial"
    if not _has_criteria(ctx):
        return "operational"
    return "stable"


def _min_level(a: IntentClarityLevel, b: IntentClarityLevel) -> IntentClarityLevel:
    return _ORDER_LEVEL[min(_LEVEL_ORDER[a], _LEVEL_ORDER[b])]


def _missing_signal_unknowns(ctx: HumanIntentContext) -> list[str]:
    unknowns: list[str] = []
    if not _has_real_goal(ctx):
        unknowns.append("the user's goal is not yet clear")
    if not _has_materials(ctx):
        unknowns.append("no working materials have been provided yet")
    if not _has_criteria(ctx):
        unknowns.append("success criteria and boundaries are not established")
    return unknowns


def cold_start_clarity(ctx: HumanIntentContext) -> IntentClarity:
    """Safe clarity when no model verdict is available (cold start / model error)."""
    return IntentClarity(
        level=clarity_ceiling(ctx),
        unknowns=_missing_signal_unknowns(ctx) or ["intent not yet model-verified"],
        assumptions=[],
        confidence=0.3,
    )


def estimate_clarity(
    ctx: HumanIntentContext, verdict: ClarityVerdict | None
) -> IntentClarity:
    """Combine the model's verdict with the deterministic floor.

    ``verdict`` is the model's structured estimate (or ``None`` for cold start).
    The returned level is the model's level clamped to ``clarity_ceiling``; the
    model's unknowns/assumptions pass through, and confidence is clamped to
    ``[0, 1]``.
    """
    if verdict is None:
        return cold_start_clarity(ctx)

    ceiling = clarity_ceiling(ctx)
    level = _min_level(verdict["level"], ceiling)
    confidence = max(0.0, min(1.0, float(verdict.get("confidence", 0.0))))
    return IntentClarity(
        level=level,
        unknowns=list(verdict.get("unknowns", [])),
        assumptions=list(verdict.get("assumptions", [])),
        confidence=confidence,
    )


# Type for an injected estimator: builds a verdict from intent + task, or None.
ClarityEstimator = Any  # Callable[[HumanIntentContext, Mapping[str, Any]], ClarityVerdict | None]


def run_estimator(
    estimator: ClarityEstimator | None,
    ctx: HumanIntentContext,
    task: Mapping[str, Any],
) -> ClarityVerdict | None:
    """Invoke an injected clarity estimator, swallowing failures into cold start.

    A model-backed estimator may raise (gateway down, malformed output); the
    runtime must still proceed on a thin intent, so any error returns ``None``.
    """
    if estimator is None:
        return None
    try:
        verdict: ClarityVerdict | None = estimator(ctx, task)
        return verdict
    except Exception:
        return None


__all__ = [
    "CLARITY_OUTPUT_SCHEMA",
    "ClarityEstimator",
    "ClarityVerdict",
    "clarity_ceiling",
    "cold_start_clarity",
    "estimate_clarity",
    "run_estimator",
]
