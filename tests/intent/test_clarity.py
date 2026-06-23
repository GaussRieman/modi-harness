"""Tests for IntentClarity estimation (plan N2.1).

Model-first, deterministically floored (spec D2). These tests drive the model
side with a fixed verdict and assert how the runtime *handles and enforces* it —
the floor clamps over-confident estimates, and a missing verdict still yields a
safe clarity. They do not test a heuristic estimator.
"""
from __future__ import annotations

from typing import Any

from modi_harness.intent.clarity import (
    ClarityVerdict,
    clarity_ceiling,
    estimate_clarity,
)
from modi_harness.intent.extractor import extract_intent


def _verdict(level: str, confidence: float = 0.9) -> ClarityVerdict:
    return ClarityVerdict(
        level=level,  # type: ignore[typeddict-item]
        unknowns=[],
        assumptions=["model assumed something"],
        confidence=confidence,
    )


def test_floor_clamps_overconfident_stable_on_empty_input() -> None:
    ctx = extract_intent({})
    # The model over-estimates; the deterministic floor must clamp it down.
    clarity = estimate_clarity(ctx, _verdict("stable"))
    assert clarity["level"] == "thin"


def test_cold_start_without_verdict_still_produces_clarity() -> None:
    ctx = extract_intent({"prompt": "look into something"})
    clarity = estimate_clarity(ctx, None)
    assert clarity["level"] in {"thin", "partial", "operational", "stable"}
    # No model verdict → conservative confidence, and unknowns are surfaced.
    assert clarity["confidence"] <= 0.5
    assert clarity["unknowns"]


def test_research_with_sources_and_question_is_at_least_operational() -> None:
    ctx = extract_intent(
        {
            "research_question": "state of solid-state batteries?",
            "source_urls": ["https://a", "https://b"],
        }
    )
    # Model agrees it's operational; ceiling permits it.
    clarity = estimate_clarity(ctx, _verdict("operational"))
    assert clarity["level"] == "operational"
    # Even if the model under-estimates, the verdict is honored below the ceiling.
    assert estimate_clarity(ctx, _verdict("thin"))["level"] == "thin"


def test_ceiling_caps_at_operational_without_criteria() -> None:
    ctx = extract_intent(
        {
            "research_question": "q",
            "source_urls": ["https://a"],
        }
    )
    # Goal + materials but no success criteria/boundaries → cannot be stable.
    assert clarity_ceiling(ctx) == "operational"
    assert estimate_clarity(ctx, _verdict("stable"))["level"] == "operational"


def test_verdict_confidence_is_clamped_to_unit_interval() -> None:
    ctx = extract_intent({"prompt": "x"})
    weird: dict[str, Any] = {
        "level": "partial",
        "unknowns": [],
        "assumptions": [],
        "confidence": 9.9,
    }
    clarity = estimate_clarity(ctx, weird)  # type: ignore[arg-type]
    assert 0.0 <= clarity["confidence"] <= 1.0


def test_full_intent_can_reach_stable() -> None:
    ctx = extract_intent(
        {
            "research_question": "q",
            "source_urls": ["https://a"],
            "success_criteria": ["every claim cited"],
        }
    )
    ctx["boundaries"] = [
        {
            "id": "b1",
            "kind": "data",
            "statement": "only provided sources",
            "severity": "hard",
            "escalation": "deny",
        }
    ]
    assert clarity_ceiling(ctx) == "stable"
    assert estimate_clarity(ctx, _verdict("stable"))["level"] == "stable"
