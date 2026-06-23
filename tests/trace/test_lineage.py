"""IntentLineage helpers (plan N4.2 / N8)."""
from __future__ import annotations


def _proposal(**over: object) -> dict:
    base = {
        "id": "act-1",
        "kind": "tool_call",
        "summary": "call fetch_url",
        "tool_name": "fetch_url",
        "arguments": {"url": "https://example.com"},
        "intent_version": 2,
        "stage_id": "stage-explore",
        "expected_outcome": None,
        "impact": {},
    }
    base.update(over)
    return base


def _decision(**over: object) -> dict:
    base = {
        "id": "ad-1",
        "decision": "allow",
        "reason": "inside field",
        "action_id": "act-1",
        "intent_version": 2,
        "stage_id": "stage-explore",
        "boundary_hits": [],
        "governance_requirements": [],
        "model_judged": True,
    }
    base.update(over)
    return base


def test_lineage_built_from_decision_and_proposal() -> None:
    from modi_harness.trace.lineage import build_lineage

    lin = build_lineage(proposal=_proposal(), decision=_decision())
    assert lin["action_id"] == "act-1"
    assert lin["alignment_decision_id"] == "ad-1"
    assert lin["intent_version"] == 2
    assert lin["stage_id"] == "stage-explore"
    assert lin["judgment_id"] is None
    assert lin["boundary_hits"] == []


def test_lineage_includes_judgment_when_present() -> None:
    from modi_harness.trace.lineage import build_lineage

    judgment = {"id": "judg-9", "kind": "approve"}
    lin = build_lineage(
        proposal=_proposal(),
        decision=_decision(decision="ask_judgment", boundary_hits=["b1"]),
        judgment=judgment,
    )
    assert lin["judgment_id"] == "judg-9"
    assert lin["boundary_hits"] == ["b1"]
