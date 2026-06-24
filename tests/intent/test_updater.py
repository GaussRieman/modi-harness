"""intent/updater.py: apply a HumanJudgment to the intent field (plan N6.2)."""
from __future__ import annotations

from typing import Any

from modi_harness.autonomy.scope import derive_autonomy_scope
from modi_harness.intent.types import (
    HumanIntentContext,
    HumanJudgment,
    IntentBoundary,
    IntentStage,
)
from modi_harness.intent.updater import apply_judgment, recompute_autonomy

# --- builders ----------------------------------------------------------------


def _stage(kind: str = "explore", id: str = "stage-explore") -> IntentStage:
    return IntentStage(
        id=id,
        kind=kind,  # type: ignore[typeddict-item]
        goal="g",
        exit_criteria=[],
        judgment_required_before_exit=False,
    )


def _intent(**over: Any) -> HumanIntentContext:
    base = HumanIntentContext(
        version=2,
        goal="research X",
        desired_outcome=None,
        boundaries=[],
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
    base.update(over)  # type: ignore[typeddict-item]
    return base


def _judgment(kind: str = "approve", **over: Any) -> HumanJudgment:
    base = HumanJudgment(
        id="j-1",
        kind=kind,  # type: ignore[typeddict-item]
        target_action_id=None,
        target_stage_id=None,
        rationale=None,
        intent_updates={},
        created_at="2026-06-23T00:00:00.000Z",
    )
    base.update(over)  # type: ignore[typeddict-item]
    return base


def _hard_boundary(id: str = "b-1") -> IntentBoundary:
    return IntentBoundary(
        id=id,
        kind="external_commitment",
        statement="never contact competitors",
        severity="hard",
        escalation="deny",
    )


# --- version + history -------------------------------------------------------


def test_judgment_bumps_version() -> None:
    out = apply_judgment(_intent(version=2), _judgment("approve"))
    assert out["version"] == 3


def test_judgment_appended_to_decisions() -> None:
    out = apply_judgment(_intent(), _judgment("approve", id="j-99"))
    assert out["decisions"][-1]["id"] == "j-99"


def test_original_intent_not_mutated() -> None:
    intent = _intent(version=2)
    apply_judgment(intent, _judgment("revise", intent_updates={"goal": "new"}))
    assert intent["version"] == 2
    assert intent["goal"] == "research X"


# --- patch application -------------------------------------------------------


def test_revise_changes_goal() -> None:
    out = apply_judgment(
        _intent(goal="research X"),
        _judgment("revise", intent_updates={"goal": "research Y"}),
    )
    assert out["goal"] == "research Y"


def test_constrain_adds_hard_boundary() -> None:
    out = apply_judgment(
        _intent(),
        _judgment("constrain", intent_updates={"add_boundaries": [_hard_boundary()]}),
    )
    assert any(b["id"] == "b-1" for b in out["boundaries"])


def test_remove_boundary() -> None:
    out = apply_judgment(
        _intent(boundaries=[_hard_boundary("b-1"), _hard_boundary("b-2")]),
        _judgment("revise", intent_updates={"remove_boundary_ids": ["b-1"]}),
    )
    ids = {b["id"] for b in out["boundaries"]}
    assert ids == {"b-2"}


def test_redirect_changes_stage() -> None:
    new_stage = _stage(kind="execute", id="stage-execute")
    out = apply_judgment(
        _intent(),
        _judgment("redirect", intent_updates={"set_stage": new_stage}),
    )
    assert out["current_stage"]["id"] == "stage-execute"


def test_clarify_adds_confirmed_input() -> None:
    out = apply_judgment(
        _intent(),
        _judgment("clarify", intent_updates={"confirmed_inputs": {"deadline": "friday"}}),
    )
    assert out["confirmed_inputs"]["deadline"] == "friday"


def test_confirmed_inputs_merge_not_replace() -> None:
    out = apply_judgment(
        _intent(confirmed_inputs={"a": 1}),
        _judgment("clarify", intent_updates={"confirmed_inputs": {"b": 2}}),
    )
    assert out["confirmed_inputs"] == {"a": 1, "b": 2}


def test_add_non_goals_and_success_criteria() -> None:
    out = apply_judgment(
        _intent(),
        _judgment(
            "revise",
            intent_updates={
                "add_non_goals": ["no scraping"],
                "add_success_criteria": ["cite sources"],
            },
        ),
    )
    assert "no scraping" in out["non_goals"]
    assert "cite sources" in out["success_criteria"]


# --- corrections -------------------------------------------------------------


def test_revise_records_a_correction() -> None:
    out = apply_judgment(
        _intent(),
        _judgment("revise", rationale="wrong direction", intent_updates={"goal": "Y"}),
    )
    assert len(out["corrections"]) == 1
    assert out["corrections"][0]["summary"]


def test_approve_records_no_correction() -> None:
    out = apply_judgment(_intent(), _judgment("approve"))
    assert out["corrections"] == []


# --- recompute clarity + scope ----------------------------------------------


def test_recompute_returns_clarity_and_scope() -> None:
    intent = _intent()
    clarity, scope = recompute_autonomy(intent)
    assert clarity["level"] in ("thin", "partial", "operational", "stable")
    assert scope["intent_clarity"] == clarity


def test_recompute_narrows_scope_after_hard_boundary() -> None:
    # Adding a hard/deny boundary forces the constrained autonomy mode.
    after = apply_judgment(
        _intent(),
        _judgment("constrain", intent_updates={"add_boundaries": [_hard_boundary()]}),
    )
    _clarity, scope = recompute_autonomy(after)
    assert scope["mode"] == "constrained"


def test_recompute_matches_direct_derivation() -> None:
    intent = _intent()
    clarity, scope = recompute_autonomy(intent)
    assert scope == derive_autonomy_scope(clarity, intent)
