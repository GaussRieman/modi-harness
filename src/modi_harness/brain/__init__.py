"""Brain control-layer interfaces for AgentLoop."""

from .rules import (
    HARD_BOUNDARY_RULE_ID,
    MISSING_INPUT_RULE_ID,
    STAGE_EXIT_RULE_ID,
    RuleBrain,
    hard_boundary_decision,
    missing_input_decision,
    stage_exit_transition_decision,
)
from .slow import SlowModelBrain, StaticStructuredSlowPlanner
from .types import Brain, BrainPlanningError, StructuredSlowPlanner


def default_brain() -> Brain:
    """Default control stack: constrained fast rules, then slow model planning."""
    return RuleBrain(fallback=SlowModelBrain())


__all__ = [
    "HARD_BOUNDARY_RULE_ID",
    "MISSING_INPUT_RULE_ID",
    "STAGE_EXIT_RULE_ID",
    "Brain",
    "BrainPlanningError",
    "RuleBrain",
    "SlowModelBrain",
    "StaticStructuredSlowPlanner",
    "StructuredSlowPlanner",
    "default_brain",
    "hard_boundary_decision",
    "missing_input_decision",
    "stage_exit_transition_decision",
]
