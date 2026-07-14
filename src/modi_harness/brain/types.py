"""The single Brain protocol used by autonomous Workflow Nodes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..loop.types import StepContext, StepDecision


class Brain(Protocol):
    def plan_step(self, context: StepContext) -> StepDecision:
        """Return one closed semantic StepDecision."""


class BrainPlanningError(ValueError):
    """Provider, parsing, normalization, or decision schema planning failed."""


class StructuredPlanner(Protocol):
    def plan_structured_step(self, context: StepContext) -> Mapping[str, Any]:
        """Return a mapping candidate for closed StepDecision validation."""


__all__ = ["Brain", "BrainPlanningError", "StructuredPlanner"]
