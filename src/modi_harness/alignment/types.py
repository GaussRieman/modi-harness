"""Alignment decision contracts (plan N4.2).

``AlignmentDecision`` is the runtime's *primary* verdict: does this action fit
inside the human intent field, and if not, how should the runtime respond? It is
richer than governance's allow/deny and carries intent lineage so every
consequential action can be traced back to the intent in force.

Governance is demoted to a proof layer: alignment decides fit, then attaches
``GovernanceRequirement``s describing what governance must still prove (approval,
review, audit) before the action may execute.
"""
from __future__ import annotations

from typing import Literal, TypedDict

AlignmentVerdict = Literal[
    "allow",
    "ask_judgment",
    "redirect",
    "constrain",
    "deny",
]
"""Why richer than allow/deny:

- ``allow`` — inside the field; proceed (governance may still require proof).
- ``ask_judgment`` — needs a human judgment before proceeding (not mere approval).
- ``redirect`` — drifting; steer back toward intent without a human round-trip.
- ``constrain`` — allowed only within a tightened envelope.
- ``deny`` — outside a hard boundary; never execute.
"""

GovernanceRequirementKind = Literal[
    "approval",
    "review",
    "audit",
    "dry_run",
]


class GovernanceRequirement(TypedDict):
    """A proof obligation alignment hands down to governance.

    Alignment decides primary fit; governance proves/enforces. A requirement is
    *not* a final policy verdict — it says "before this runs, governance must
    secure X".
    """

    kind: GovernanceRequirementKind
    reason: str


class BoundaryHit(TypedDict):
    """A boundary the action touched, recorded for trace and judgment context."""

    boundary_id: str
    severity: str
    escalation: str
    statement: str


class AlignmentDecision(TypedDict):
    """The primary, lineage-bearing verdict for a proposed action."""

    id: str
    decision: AlignmentVerdict
    reason: str
    action_id: str
    intent_version: int
    stage_id: str
    boundary_hits: list[BoundaryHit]
    governance_requirements: list[GovernanceRequirement]
    # True when a model produced the semantic judgment; False when only the
    # deterministic floor ran (cold start / no estimator). Lets trace prove
    # whether alignment was model-first or floor-only on each action.
    model_judged: bool


__all__ = [
    "AlignmentDecision",
    "AlignmentVerdict",
    "BoundaryHit",
    "GovernanceRequirement",
    "GovernanceRequirementKind",
]
