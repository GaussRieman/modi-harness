"""IntentLineage: tie a consequential action to the intent that authorized it.

A lineage record is the compact, queryable join across an ``ActionProposal``, its
``AlignmentDecision``, and any ``HumanJudgment`` that resolved it. Trace events
embed these fields (not whole contexts) so a maintainer can answer "which intent
version and stage produced this action, and what decided it?" from trace alone.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, TypedDict

# The trace event_type that carries a full IntentLineage in its payload. A
# maintainer answers "which intent version and stage produced this action, and
# what decided it?" by reading these events alone.
LINEAGE_EVENT_TYPE = "intent_lineage_recorded"


class IntentLineage(TypedDict):
    """Compact join keys linking an action to its authorizing intent."""

    action_id: str
    alignment_decision_id: str
    intent_version: int
    stage_id: str
    parent_step_id: str | None
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
        parent_step_id=proposal.get("parent_step_id"),
        judgment_id=judgment["id"] if judgment else None,
        boundary_hits=list(decision.get("boundary_hits", []) or []),
    )


# ---------------------------------------------------------------------------
# read / group — reconstruct lineage from a recorded trace
# ---------------------------------------------------------------------------


def read_lineage(events: Iterable[Mapping[str, Any]]) -> Iterator[IntentLineage]:
    """Yield the ``IntentLineage`` carried by each ``intent_lineage_recorded`` event.

    Other event types are skipped, so a maintainer can feed the whole trace
    stream in and get back exactly the action-to-intent join records.
    """
    for event in events:
        if event.get("event_type") != LINEAGE_EVENT_TYPE:
            continue
        payload = event.get("payload") or {}
        yield IntentLineage(
            action_id=payload.get("action_id", ""),
            alignment_decision_id=payload.get("alignment_decision_id", ""),
            intent_version=payload.get("intent_version", 0),
            stage_id=payload.get("stage_id", ""),
            parent_step_id=payload.get("parent_step_id"),
            judgment_id=payload.get("judgment_id"),
            boundary_hits=list(payload.get("boundary_hits", []) or []),
        )


def group_by_intent_version(
    lineages: Iterable[IntentLineage],
) -> dict[int, list[IntentLineage]]:
    """Group lineage records by the intent version that authorized each action."""
    grouped: dict[int, list[IntentLineage]] = {}
    for lin in lineages:
        grouped.setdefault(lin["intent_version"], []).append(lin)
    return grouped


def group_by_stage(
    lineages: Iterable[IntentLineage],
) -> dict[str, list[IntentLineage]]:
    """Group lineage records by the stage each action belonged to."""
    grouped: dict[str, list[IntentLineage]] = {}
    for lin in lineages:
        grouped.setdefault(lin["stage_id"], []).append(lin)
    return grouped


def lineage_for_action(
    lineages: Iterable[IntentLineage], action_id: str
) -> IntentLineage | None:
    """Return the lineage record for ``action_id``, or None when absent."""
    for lin in lineages:
        if lin["action_id"] == action_id:
            return lin
    return None


__all__ = [
    "LINEAGE_EVENT_TYPE",
    "IntentLineage",
    "build_lineage",
    "group_by_intent_version",
    "group_by_stage",
    "lineage_for_action",
    "read_lineage",
]
