"""Brain control-layer protocol.

Brain decides the next semantic step. It does not execute operations, mutate
loop state, persist step records, or run postchecks; AgentLoop owns those
boundaries.
"""

from __future__ import annotations

from typing import Protocol

from ..loop.types import StepContext, StepDecision


class Brain(Protocol):
    """Planner interface consumed by AgentLoop."""

    def plan_step(self, context: StepContext) -> StepDecision:
        """Return the next semantic step decision for ``context``."""


class BrainPlanningError(ValueError):
    """Brain could not produce a valid next-step decision."""


class StructuredSlowPlanner(Protocol):
    """Model-backed planner used by slow Brain mode.

    The planner may call a model or another reasoning service, but its boundary
    with Brain is structured: it must return a StepDecision-shaped mapping for
    validation before Loop sees it.
    """

    def plan_structured_step(self, context: StepContext) -> StepDecision:
        """Return a structured slow ``StepDecision`` candidate."""


__all__ = ["Brain", "BrainPlanningError", "StructuredSlowPlanner"]
