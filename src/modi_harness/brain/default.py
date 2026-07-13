"""Default implementation of the one Brain protocol."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ..loop import validate_step_decision
from ..loop.types import StepContext, StepDecision
from .types import BrainPlanningError, StructuredPlanner


class DefaultBrain:
    """Adapt one structured planner into one validated StepDecision path."""

    def __init__(self, planner: StructuredPlanner) -> None:
        self._planner = planner

    def plan_step(self, context: StepContext) -> StepDecision:
        step_id = context.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip():
            raise BrainPlanningError("StepContext.step_id is required")
        try:
            candidate = self._planner.plan_structured_step(context)
        except Exception as exc:
            raise BrainPlanningError(f"planner provider failed: {exc}") from exc
        try:
            if not isinstance(candidate, Mapping):
                raise TypeError("planner result must be a mapping")
            raw: dict[str, Any] = dict(candidate)
            raw["id"] = step_id
            decision = StepDecision(**raw)  # type: ignore[typeddict-item]
            validate_step_decision(decision)
        except (KeyError, TypeError, ValueError) as exc:
            raise BrainPlanningError(f"planner returned an invalid StepDecision: {exc}") from exc
        return cast(StepDecision, decision)


class StaticStructuredPlanner:
    """Deterministic planner for tests and programmatic Agents."""

    def __init__(self, decision: Mapping[str, Any]) -> None:
        self._decision = dict(decision)

    def plan_structured_step(self, context: StepContext) -> Mapping[str, Any]:
        return dict(self._decision)


__all__ = ["DefaultBrain", "StaticStructuredPlanner"]
