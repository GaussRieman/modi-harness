"""Constrained fast Brain rules.

Fast rules decide only the next semantic step. They do not execute operations,
mutate state, or expand into multi-step scripts.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..loop import validate_step_decision
from ..loop.types import (
    AskRequest,
    HumanJudgmentAssessment,
    StepContext,
    StepDecision,
)
from .types import Brain

MISSING_INPUT_RULE_ID = "fast.missing_input.clarify.v1"


def _stage_kind(context: StepContext) -> str:
    stage = context.get("stage") or {}
    return str(stage.get("kind") or "")


def _required_inputs(context: StepContext) -> list[str]:
    spec = context.get("brain_spec") or {}
    fast_rules = spec.get("fast_rules") if isinstance(spec, dict) else None
    if not isinstance(fast_rules, dict):
        return []
    return [
        str(item).strip()
        for item in fast_rules.get("required_inputs", [])
        if str(item).strip()
    ]


def _missing_required_inputs(context: StepContext) -> list[str]:
    intent = context.get("intent") or {}
    confirmed = intent.get("confirmed_inputs") or {}
    if not isinstance(confirmed, dict):
        confirmed = {}
    missing: list[str] = []
    for field in _required_inputs(context):
        value = confirmed.get(field)
        if value is None or value == "" or value == []:
            missing.append(field)
    return missing


def missing_input_decision(context: StepContext) -> StepDecision | None:
    """Ask the human when explicit required inputs are missing."""
    missing = _missing_required_inputs(context)
    if not missing or _stage_kind(context) != "clarify":
        return None

    question = "Please provide: " + ", ".join(missing[:3])
    if len(missing) > 3:
        question += ", and any other required inputs"

    decision = StepDecision(
        id=context["step_id"],
        step_kind="clarify",
        reasoning_mode="fast",
        reason="current clarify stage is missing explicit required input",
        rule_ref=MISSING_INPUT_RULE_ID,
        intent_patch=None,
        ask=AskRequest(
            prompt=question,
            reason="BrainSpec fast rule declared required inputs",
            allowed_kinds=["clarify", "revise", "cancel"],
        ),
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="wait",
        human_judgment=HumanJudgmentAssessment(
            required=False,
            reason="missing input must be supplied by the human",
            trigger="missing_input",
        ),
        continuation_basis=None,
    )
    validate_step_decision(decision)
    return decision


@dataclass(frozen=True)
class RuleBrain:
    """Fast-rule wrapper with slow fallback."""

    fallback: Brain

    def plan_step(self, context: StepContext) -> StepDecision:
        for rule in (missing_input_decision,):
            decision = rule(context)
            if decision is not None:
                return decision
        return self.fallback.plan_step(context)


__all__ = ["MISSING_INPUT_RULE_ID", "RuleBrain", "missing_input_decision"]
