"""Intent clarification, confirmation, and patch classification tests."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from modi_harness.long_task.intent import (
    IntentAssumption,
    IntentConfirmation,
    IntentPatch,
    IntentPatchChange,
    IntentQuestion,
    IntentValidationError,
    assess_intent_clarification,
    classify_intent_patch,
    intent_fingerprint,
    validate_intent_confirmation,
)
from modi_harness.long_task.types import IntentCriterion, IntentVersion


def _intent(*, version: int = 1, authority_hash: str = "sha256:authority") -> IntentVersion:
    return IntentVersion(
        intent_id="intent-1",
        version=version,
        status="confirmed",
        goal="Build the service",
        desired_outcome="A verified service",
        success_criteria=(
            IntentCriterion(
                "criterion-1",
                "The service works",
                True,
                "validator",
                "goal-v1",
            ),
        ),
        constraints=("No external writes",),
        assumptions=("Dependencies are stable",),
        authority_hash=authority_hash,
    )


def _patch(*changes: IntentPatchChange, base_version: int = 1) -> IntentPatch:
    return IntentPatch(base_version, "User changed the goal", changes)


def test_high_impact_unresolved_question_blocks_confirmation_one_at_a_time() -> None:
    first = IntentQuestion("payment", "Process real payments?", "high")
    second = IntentQuestion("region", "Which deployment region?", "high")

    assessment = assess_intent_clarification((first, second), ())

    assert not assessment.can_confirm
    assert assessment.next_question == first
    assert assessment.blocking_question_ids == ("payment", "region")


def test_high_impact_unconfirmed_assumption_blocks_confirmation() -> None:
    assumption = IntentAssumption(
        "data-loss",
        "Destructive migration is acceptable",
        "high",
    )

    assessment = assess_intent_clarification((), (assumption,))

    assert not assessment.can_confirm
    assert assessment.blocking_assumption_ids == ("data-loss",)


def test_low_impact_unconfirmed_assumption_is_retained() -> None:
    assumption = IntentAssumption("language", "Chinese UI", "low")

    assessment = assess_intent_clarification((), (assumption,))

    assert assessment.can_confirm
    assert assessment.retained_assumptions == (assumption,)


def test_confirmation_is_bound_to_exact_intent_fingerprint() -> None:
    intent = _intent()
    confirmation = IntentConfirmation(
        intent.intent_id,
        intent.version,
        intent_fingerprint(intent),
    )

    validate_intent_confirmation(intent, confirmation)
    with pytest.raises(IntentValidationError, match="fingerprint"):
        validate_intent_confirmation(
            replace(intent, goal="Build something else"),
            confirmation,
        )


def test_patch_classifies_local_and_material_changes() -> None:
    current = _intent()
    local = classify_intent_patch(
        current,
        replace(current, version=2),
        _patch(
            IntentPatchChange(
                "retry_strategy",
                "task:backend",
                {"max_attempts": 2},
                impact="local",
            )
        ),
    )
    material = classify_intent_patch(
        current,
        replace(current, version=2, goal="Build the production service"),
        _patch(IntentPatchChange("set_goal", "goal", "production")),
    )

    assert local.impact == "local"
    assert not local.requires_confirmation
    assert material.impact == "material"
    assert material.requires_confirmation


def test_patch_rejects_stale_base_version() -> None:
    current = _intent(version=2)
    proposed = replace(current, version=3, goal="Changed")

    with pytest.raises(IntentValidationError, match="stale IntentPatch"):
        classify_intent_patch(
            current,
            proposed,
            _patch(
                IntentPatchChange("set_goal", "goal", "Changed"),
                base_version=1,
            ),
        )


def test_patch_rejects_authority_expansion_even_if_human_would_confirm() -> None:
    current = _intent()

    with pytest.raises(IntentValidationError, match="expand execution authority"):
        classify_intent_patch(
            current,
            replace(current, version=2, authority_hash="sha256:wider"),
            _patch(
                IntentPatchChange(
                    "change_authority",
                    "authority",
                    {"network": True},
                    authority_effect="expand",
                )
            ),
        )


def test_authority_hash_change_requires_explicit_narrowing() -> None:
    current = _intent()
    proposed = replace(current, version=2, authority_hash="sha256:narrower")

    with pytest.raises(IntentValidationError, match="explicit narrowing"):
        classify_intent_patch(
            current,
            proposed,
            _patch(IntentPatchChange("change_authority", "authority", {})),
        )

    decision = classify_intent_patch(
        current,
        proposed,
        _patch(
            IntentPatchChange(
                "change_authority",
                "authority",
                {"remove": ["network"]},
                authority_effect="narrow",
            )
        ),
    )
    assert decision.authority_effect == "narrow"
    assert decision.requires_confirmation


def test_intent_values_have_json_serializable_immutable_snapshots() -> None:
    question = IntentQuestion(
        "deployment",
        "Where?",
        "low",
        {"regions": ["cn-north", "cn-east"]},
    )
    patch = _patch(
        IntentPatchChange(
            "retry_strategy",
            "task:api",
            {"delays": [1, 2]},
            impact="local",
        )
    )

    json.dumps(question.snapshot())
    json.dumps(patch.snapshot())
    with pytest.raises(TypeError):
        question.answer["regions"] = ()  # type: ignore[index]

