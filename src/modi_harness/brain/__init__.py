"""Single Brain control boundary for autonomous Workflow Nodes."""

from .default import DefaultBrain, StaticStructuredPlanner
from .types import Brain, BrainPlanningError, StructuredPlanner


def default_brain(*, planner: StructuredPlanner) -> Brain:
    """Construct the only production Brain implementation."""

    return DefaultBrain(planner)


__all__ = [
    "Brain",
    "BrainPlanningError",
    "DefaultBrain",
    "StaticStructuredPlanner",
    "StructuredPlanner",
    "default_brain",
]
