"""Brain control-layer interfaces for AgentLoop."""

from .rules import MISSING_INPUT_RULE_ID, RuleBrain, missing_input_decision
from .slow import SlowModelBrain
from .types import Brain, BrainPlanningError


def default_brain() -> Brain:
    """Default control stack: constrained fast rules, then slow model planning."""
    return RuleBrain(fallback=SlowModelBrain())


__all__ = [
    "MISSING_INPUT_RULE_ID",
    "Brain",
    "BrainPlanningError",
    "RuleBrain",
    "SlowModelBrain",
    "default_brain",
    "missing_input_decision",
]
