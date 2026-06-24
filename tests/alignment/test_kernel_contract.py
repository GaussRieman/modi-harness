"""Alignment decision contracts (plan N4.2).

The runtime's *first* decision answers "is this action inside the human's intent
field?" — richer than governance's allow/deny, and lineage-bearing.
"""
from __future__ import annotations

from typing import get_args, get_type_hints


def test_alignment_decision_is_primary() -> None:
    from modi_harness.alignment import AlignmentDecision

    hints = get_type_hints(AlignmentDecision)
    assert "decision" in hints
    verdicts = set(get_args(hints["decision"]))
    assert {"allow", "ask_judgment", "redirect", "constrain", "deny"} <= verdicts
    assert {"intent_version", "stage_id"} <= set(hints)


def test_governance_requirement_shape() -> None:
    from modi_harness.alignment import GovernanceRequirement

    hints = get_type_hints(GovernanceRequirement)
    assert {"kind", "reason"} <= set(hints)


def test_decision_carries_lineage_fields() -> None:
    from modi_harness.alignment import AlignmentDecision

    hints = get_type_hints(AlignmentDecision)
    required = {
        "id",
        "decision",
        "reason",
        "action_id",
        "intent_version",
        "stage_id",
        "boundary_hits",
        "governance_requirements",
        "model_judged",
    }
    assert required <= set(hints), f"missing: {required - set(hints)}"
