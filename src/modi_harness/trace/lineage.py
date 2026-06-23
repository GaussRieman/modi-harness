"""IntentLineage: tie a consequential action to the intent that authorized it.

A lineage record is the compact, queryable join across an ``ActionProposal``, its
``AlignmentDecision``, and any ``HumanJudgment`` that resolved it. Trace events
embed these fields (not whole contexts) so a maintainer can answer "which intent
version and stage produced this action, and what decided it?" from trace alone.
"""
from __future__ import annotations

from typing import Any, TypedDict


class IntentLineage(TypedDict):
    """Compact join keys linking an action to its authorizing intent."""

    action_id: str
    alignment_decision_id: str
    intent_version: int
    stage_id: str
    judgment_id: str | None
    boundary_hits: list[Any]


def build_lineage(
    *,
    proposal: dict[str, Any],
    decision: dict[str, Any],
    judgment: dict[str, Any] | None = None,
) -> IntentLineage:
    """Build a lineage record from a proposal + alignment decision (+ judgment)."""
    return IntentLineage(
        action_id=proposal["id"],
        alignment_decision_id=decision["id"],
        intent_version=decision.get("intent_version", proposal.get("intent_version", 0)),
        stage_id=decision.get("stage_id", proposal.get("stage_id", "")),
        judgment_id=judgment["id"] if judgment else None,
        boundary_hits=list(decision.get("boundary_hits", []) or []),
    )


__all__ = ["IntentLineage", "build_lineage"]
