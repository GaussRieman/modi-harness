"""Derive an ``AutonomyScope`` from intent clarity and active boundaries (N2.2).

The scope is what the runtime actually enforces: which stages the agent may be
in, which action kinds it may take, what forces a human judgment, and the
highest tool risk it may run without one. It is derived — never selected — so
autonomy tracks how clear the intent is.
"""

from __future__ import annotations

from typing import TypedDict

from modi_harness.autonomy.modes import AutonomyMode, mode_for_clarity
from modi_harness.intent.types import HumanIntentContext, IntentClarity
from modi_harness.types import RiskLevel


class AutonomyScope(TypedDict):
    """The enforced envelope of agent freedom for the current turn."""

    mode: AutonomyMode
    intent_clarity: IntentClarity
    allowed_stages: list[str]
    allowed_action_kinds: list[str]
    requires_judgment_for: list[str]
    max_tool_risk_without_judgment: RiskLevel


# Per-mode templates. ``requires_judgment_for`` names impact signals (see
# ``ActionImpact`` in N4) and action kinds that always escalate.
class _ScopeTemplate(TypedDict):
    allowed_stages: list[str]
    allowed_action_kinds: list[str]
    requires_judgment_for: list[str]
    max_tool_risk_without_judgment: RiskLevel


_SCOPE_TEMPLATES: dict[AutonomyMode, _ScopeTemplate] = {
    "guided": {
        "allowed_stages": ["clarify", "explore"],
        "allowed_action_kinds": ["tool_call", "stage_transition", "memory_write"],
        "requires_judgment_for": [
            "external_commitment",
            "irreversible",
            "changes_scope_or_goal",
            "output_finalize",
        ],
        "max_tool_risk_without_judgment": "L1",
    },
    "bounded": {
        "allowed_stages": ["clarify", "explore", "plan", "execute", "verify"],
        "allowed_action_kinds": [
            "tool_call",
            "stage_transition",
            "memory_write",
            "output_finalize",
        ],
        "requires_judgment_for": [
            "external_commitment",
            "irreversible",
            "changes_scope_or_goal",
        ],
        "max_tool_risk_without_judgment": "L2",
    },
    "delegated": {
        "allowed_stages": ["clarify", "explore", "plan", "execute", "verify", "deliver"],
        "allowed_action_kinds": [
            "tool_call",
            "stage_transition",
            "memory_write",
            "output_finalize",
        ],
        "requires_judgment_for": ["external_commitment", "irreversible"],
        "max_tool_risk_without_judgment": "L3",
    },
    "constrained": {
        "allowed_stages": ["clarify"],
        "allowed_action_kinds": ["stage_transition", "memory_write"],
        "requires_judgment_for": [
            "tool_call",
            "external_commitment",
            "irreversible",
            "changes_scope_or_goal",
            "output_finalize",
        ],
        "max_tool_risk_without_judgment": "L0",
    },
}


def _has_hard_red_line(ctx: HumanIntentContext) -> bool:
    """A ``hard``/``deny`` boundary is a true red line that lowers autonomy.

    Note: ``hard``/``ask`` boundaries (e.g. an agent's safety constraints) do
    *not* force ``constrained`` — they raise a judgment only when actually hit,
    so a safety-constrained agent can still run in ``bounded`` autonomy.
    """
    return any(
        b["severity"] == "hard" and b["escalation"] == "deny"
        for b in ctx["boundaries"]
    )


def derive_autonomy_scope(
    clarity: IntentClarity, ctx: HumanIntentContext
) -> AutonomyScope:
    """Derive the enforced scope from clarity and the active intent boundaries."""
    if _has_hard_red_line(ctx):
        mode: AutonomyMode = "constrained"
    else:
        mode = mode_for_clarity(clarity["level"])

    template = _SCOPE_TEMPLATES[mode]
    return AutonomyScope(
        mode=mode,
        intent_clarity=clarity,
        allowed_stages=list(template["allowed_stages"]),
        allowed_action_kinds=list(template["allowed_action_kinds"]),
        requires_judgment_for=list(template["requires_judgment_for"]),
        max_tool_risk_without_judgment=template["max_tool_risk_without_judgment"],
    )


__all__ = ["AutonomyScope", "derive_autonomy_scope"]
