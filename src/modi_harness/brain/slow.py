"""Slow Brain implementation that preserves the existing model-turn behavior."""

from __future__ import annotations

from ..loop import validate_step_decision
from ..loop.types import ContinuationBasis, HumanJudgmentAssessment, StepContext, StepDecision
from .types import BrainPlanningError


class SlowModelBrain:
    """Plan by delegating semantic work to the existing model turn.

    This first Brain implementation intentionally keeps behavior unchanged:
    it produces a slow ``plan`` decision that tells AgentLoop to proceed into
    the current model-turn path. The model call itself remains below the Loop.
    """

    def plan_step(self, context: StepContext) -> StepDecision:
        step_id = context.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            raise BrainPlanningError("StepContext.step_id is required")

        decision = StepDecision(
            id=step_id,
            step_kind="plan",
            reasoning_mode="slow",
            reason="existing model turn wrapped as slow Brain behavior",
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


__all__ = ["SlowModelBrain"]
