"""intent/stages.py: stages as the runtime alignment layer above task plans (N7).

A stage is the *phase of work* — clarify / explore / plan / execute / verify /
deliver — not a micro-task. ``assess_transition`` is the deterministic floor the
AlignmentKernel calls for ``stage_transition`` proposals: it only *raises*
severity (escalate to judgment / deny), it never lowers the model's verdict. The
explainers answer the N7 exit-gate questions in plain language.
"""
from __future__ import annotations

from typing import Any

from modi_harness.intent.types import HumanIntentContext, IntentStage

# --- builders ----------------------------------------------------------------


def _stage(kind: str = "explore", **over: Any) -> IntentStage:
    base = IntentStage(
        id=f"stage-{kind}",
        kind=kind,  # type: ignore[typeddict-item]
        goal="g",
        exit_criteria=[],
        judgment_required_before_exit=False,
    )
    base.update(over)  # type: ignore[typeddict-item]
    return base


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


def _transition(to: str | None = None, **args: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = dict(args)
    if to is not None:
        arguments["to"] = to
    return {
        "id": "act-1",
        "kind": "stage_transition",
        "summary": f"transition stage -> {to}",
        "tool_name": "stage_transition",
        "arguments": arguments,
        "intent_version": 2,
        "stage_id": "stage-explore",
        "expected_outcome": None,
        "impact": {},
    }


def _tool_call() -> dict[str, Any]:
    return {
        "id": "act-2",
        "kind": "tool_call",
        "summary": "call fetch_url",
        "tool_name": "fetch_url",
        "arguments": {"url": "https://a.example"},
        "intent_version": 2,
        "stage_id": "stage-explore",
        "expected_outcome": None,
        "impact": {},
    }


def _scope(mode: str = "delegated", allowed_stages: list[str] | None = None) -> dict[str, Any]:
    defaults = {
        "guided": ["clarify", "explore"],
        "bounded": ["clarify", "explore", "plan", "execute", "verify"],
        "delegated": ["clarify", "explore", "plan", "execute", "verify", "deliver"],
        "constrained": ["clarify"],
    }
    return {
        "mode": mode,
        "allowed_stages": allowed_stages if allowed_stages is not None else defaults[mode],
    }


# --- stage model -------------------------------------------------------------


def test_default_exit_criteria_per_kind() -> None:
    from modi_harness.intent.stages import default_exit_criteria

    assert default_exit_criteria("clarify")
    assert default_exit_criteria("verify")
    # deliver is terminal — nothing follows, so no forward exit criteria.
    assert default_exit_criteria("deliver") == []


def test_build_stage_applies_defaults() -> None:
    from modi_harness.intent.stages import build_stage

    stage = build_stage("explore")
    assert stage["kind"] == "explore"
    assert stage["goal"]  # a per-kind default goal
    assert stage["exit_criteria"]  # default exit criteria
    assert stage["id"].startswith("stg-")
    assert stage["judgment_required_before_exit"] is False


def test_build_stage_overrides_win() -> None:
    from modi_harness.intent.stages import build_stage

    stage = build_stage(
        "plan",
        goal="my goal",
        exit_criteria=["x"],
        judgment_required_before_exit=True,
        id="stage-custom",
    )
    assert stage["goal"] == "my goal"
    assert stage["exit_criteria"] == ["x"]
    assert stage["judgment_required_before_exit"] is True
    assert stage["id"] == "stage-custom"


def test_target_stage_kind_reads_common_keys() -> None:
    from modi_harness.intent.stages import target_stage_kind

    assert target_stage_kind(_transition(to="deliver")) == "deliver"
    assert target_stage_kind({"arguments": {"to_stage": "plan"}}) == "plan"
    assert target_stage_kind({"arguments": {"stage": "verify"}}) == "verify"
    assert target_stage_kind({"arguments": {"kind": "execute"}}) == "execute"
    # Unknown / missing target is reported as None, not guessed.
    assert target_stage_kind({"arguments": {"to": "wat"}}) is None
    assert target_stage_kind({"arguments": {}}) is None


# --- assess_transition (the deterministic floor) -----------------------------


def test_unknown_target_asks_judgment() -> None:
    from modi_harness.intent.stages import assess_transition

    escalations = assess_transition(
        proposal=_transition(to="wat"), intent=_intent(), scope=_scope()
    )
    assert any(e["verdict"] == "ask_judgment" for e in escalations)


def test_target_outside_scope_asks_judgment() -> None:
    from modi_harness.intent.stages import assess_transition

    # guided scope does not allow ``plan``; moving there needs a human.
    escalations = assess_transition(
        proposal=_transition(to="plan"), intent=_intent(), scope=_scope("guided")
    )
    assert escalations
    assert all(e["verdict"] == "ask_judgment" for e in escalations)
    assert any("scope" in e["reason"] for e in escalations)


def test_gated_exit_asks_judgment() -> None:
    from modi_harness.intent.stages import assess_transition

    intent = _intent(
        current_stage=_stage("plan", judgment_required_before_exit=True)
    )
    escalations = assess_transition(
        proposal=_transition(to="execute"), intent=intent, scope=_scope()
    )
    assert any("before exit" in e["reason"] for e in escalations)


def test_deliver_without_success_criteria_asks_judgment() -> None:
    from modi_harness.intent.stages import assess_transition

    # In scope (delegated allows deliver) but no declared coverage criteria.
    escalations = assess_transition(
        proposal=_transition(to="deliver"), intent=_intent(), scope=_scope()
    )
    assert escalations
    assert any("success criteria" in e["reason"] for e in escalations)


def test_deliver_with_success_criteria_and_in_scope_is_clean() -> None:
    from modi_harness.intent.stages import assess_transition

    intent = _intent(success_criteria=["briefing covers all sources"])
    escalations = assess_transition(
        proposal=_transition(to="deliver"), intent=intent, scope=_scope()
    )
    # Floor sees no structural reason to raise; the model verdict stands.
    assert escalations == []


def test_in_scope_non_committing_transition_is_clean() -> None:
    from modi_harness.intent.stages import assess_transition

    escalations = assess_transition(
        proposal=_transition(to="plan"), intent=_intent(), scope=_scope("bounded")
    )
    assert escalations == []


# --- explainers (the exit gate) ----------------------------------------------


def test_explain_action_stage_for_tool_call() -> None:
    from modi_harness.intent.stages import explain_action_stage

    text = explain_action_stage(proposal=_tool_call(), intent=_intent())
    assert "explore" in text
    assert "stage-explore" in text


def test_explain_action_stage_for_transition() -> None:
    from modi_harness.intent.stages import explain_action_stage

    text = explain_action_stage(proposal=_transition(to="deliver"), intent=_intent())
    assert "explore" in text
    assert "deliver" in text


def test_explain_transition_allowed_vs_interrupted() -> None:
    from modi_harness.intent.stages import explain_transition

    clean = explain_transition(
        proposal=_transition(to="deliver"),
        intent=_intent(success_criteria=["covered"]),
        scope=_scope(),
        decision={"decision": "allow"},
    )
    assert "deliver" in clean
    assert "allow" in clean

    blocked = explain_transition(
        proposal=_transition(to="deliver"),
        intent=_intent(),  # no success criteria
        scope=_scope(),
        decision={"decision": "ask_judgment"},
    )
    assert "ask_judgment" in blocked
    assert "success criteria" in blocked
