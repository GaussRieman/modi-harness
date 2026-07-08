"""Slow Brain implementations.

Slow mode handles the "I do not know that I know" side of control: cases where
fast rules are too narrow and the model has to reason about the next semantic
step. A structured planner can be injected for that reasoning. When no planner
is supplied, this class preserves the legacy model-turn path by returning a
validated slow ``plan`` decision.
"""

from __future__ import annotations

from typing import Any

from ..loop import validate_step_decision
from ..loop.types import ContinuationBasis, HumanJudgmentAssessment, StepContext, StepDecision
from .types import BrainPlanningError, StructuredSlowPlanner


def _step_id(context: StepContext) -> str:
    step_id = context.get("step_id")
    if not isinstance(step_id, str) or not step_id:
        raise BrainPlanningError("StepContext.step_id is required")
    return step_id


def _legacy_slow_decision(
    *,
    step_id: str,
    reason: str = "existing model turn wrapped as slow Brain behavior",
) -> StepDecision:
    decision = StepDecision(
        id=step_id,
        step_kind="plan",
        reasoning_mode="slow",
        reason=reason,
        rule_ref=None,
        intent_patch=None,
        ask=None,
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="continue",
        human_judgment=HumanJudgmentAssessment(
            required=False,
            reason="model planning stays inside the current autonomy scope",
            trigger="none",
        ),
        continuation_basis=ContinuationBasis(
            source="slow_plan",
            reference=None,
            reason="continue after obtaining the model's next planning result",
        ),
    )
    validate_step_decision(decision)
    return decision


def _wait_for_human_decision(
    *,
    step_id: str,
    reason: str,
    detail: str,
) -> StepDecision:
    prompt = "Slow Brain could not produce a safe structured next step. Please review or redirect."
    decision = StepDecision(
        id=step_id,
        step_kind="handoff",
        reasoning_mode="slow",
        reason=reason,
        rule_ref=None,
        intent_patch=None,
        ask={
            "prompt": prompt,
            "reason": detail,
            "allowed_kinds": [
                "approve",
                "reject",
                "revise",
                "redirect",
                "constrain",
                "clarify",
                "cancel",
            ],
        },
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="wait",
        human_judgment=HumanJudgmentAssessment(
            required=True,
            reason=detail,
            trigger="failure_recovery",
        ),
        continuation_basis={
            "source": "slow_plan",
            "reference": "slow_brain.validation_failed",
            "reason": "wait because slow Brain output was malformed or unsafe",
        },
    )
    validate_step_decision(decision)
    return decision


class SlowModelBrain:
    """Plan the next semantic step in slow mode.

    ``planner`` is optional during migration. Without it, slow mode returns the
    same legacy "continue into model_turn" decision as the first slice. With it,
    the planner must return a structured ``StepDecision`` that validates before
    it can drive the Loop.
    """

    def __init__(self, planner: StructuredSlowPlanner | None = None) -> None:
        self._planner = planner

    def plan_step(self, context: StepContext) -> StepDecision:
        step_id = _step_id(context)
        if self._planner is None:
            return _legacy_slow_decision(step_id=step_id)

        try:
            decision = self._planner.plan_structured_step(context)
        except Exception as exc:
            return _wait_for_human_decision(
                step_id=step_id,
                reason="slow Brain planner failed before producing a decision",
                detail=f"slow Brain planner error: {exc}",
            )

        try:
            structured = StepDecision(**dict(decision))
            structured["id"] = step_id
            structured["reasoning_mode"] = "slow"
            structured["rule_ref"] = None
            validate_step_decision(structured)
        except (TypeError, ValueError, KeyError) as exc:
            return _wait_for_human_decision(
                step_id=step_id,
                reason="slow Brain planner returned an invalid StepDecision",
                detail=f"slow Brain validation failed: {exc}",
            )
        return structured


class StaticStructuredSlowPlanner:
    """Small test/adapter planner that returns a prebuilt structured decision."""

    def __init__(self, decision: StepDecision | dict[str, Any]) -> None:
        self._decision = decision

    def plan_structured_step(self, context: StepContext) -> StepDecision:
        return StepDecision(**dict(self._decision))


__all__ = ["SlowModelBrain", "StaticStructuredSlowPlanner"]
