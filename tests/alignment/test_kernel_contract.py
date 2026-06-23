"""Architecture marker test: AlignmentDecision is the primary decision type.

Fails until plan N4 introduces the alignment center. Governance (allow/deny by
risk and mode) is demoted to a proof layer beneath this.
"""
from __future__ import annotations


def test_alignment_decision_is_primary() -> None:
    """The runtime's first decision answers 'is this inside the intent field?'

    AlignmentDecision must offer the richer alignment verdicts — not just the
    governance allow/deny — and carry intent lineage (intent_version, stage).
    """
    from modi_harness.alignment import AlignmentDecision  # noqa: F401
    from typing import get_args, get_type_hints

    hints = get_type_hints(AlignmentDecision)
    assert "decision" in hints
    verdicts = set(get_args(hints["decision"]))
    assert {"allow", "ask_judgment", "redirect", "constrain", "deny"} <= verdicts
    assert {"intent_version", "stage_id"} <= set(hints)
