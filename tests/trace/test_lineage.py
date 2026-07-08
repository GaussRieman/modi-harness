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
        "parent_step_id": "loop-abc-0001",
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
    assert lin["parent_step_id"] == "loop-abc-0001"
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


# ---------------------------------------------------------------------------
# read / group helpers (N8)
# ---------------------------------------------------------------------------


def _event(event_type: str, payload: dict) -> dict:
    return {
        "event_id": f"ev-{event_type}",
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "thread_id": None,
        "timestamp": "2026-06-23T00:00:00Z",
        "event_type": event_type,
        "payload": payload,
        "payload_ref": None,
    }


def _lineage(**over: object) -> dict:
    base = {
        "action_id": "act-1",
        "alignment_decision_id": "ad-1",
        "intent_version": 2,
        "stage_id": "stage-explore",
        "parent_step_id": "loop-abc-0001",
        "judgment_id": None,
        "boundary_hits": [],
    }
    base.update(over)
    return base


def test_read_lineage_extracts_only_lineage_events() -> None:
    from modi_harness.trace.lineage import read_lineage

    events = [
        _event("run_start", {"agent": "x"}),
        _event("intent_lineage_recorded", _lineage()),
        _event("tool_result", {"tool_name": "fetch_url"}),
        _event("intent_lineage_recorded", _lineage(action_id="act-2", intent_version=3)),
    ]
    lineages = list(read_lineage(events))
    assert len(lineages) == 2
    assert [lin["action_id"] for lin in lineages] == ["act-1", "act-2"]
    assert lineages[1]["intent_version"] == 3


def test_group_lineage_by_intent_version() -> None:
    from modi_harness.trace.lineage import group_by_intent_version, read_lineage

    events = [
        _event("intent_lineage_recorded", _lineage(action_id="a", intent_version=1)),
        _event("intent_lineage_recorded", _lineage(action_id="b", intent_version=1)),
        _event("intent_lineage_recorded", _lineage(action_id="c", intent_version=2)),
    ]
    grouped = group_by_intent_version(read_lineage(events))
    assert sorted(grouped.keys()) == [1, 2]
    assert [lin["action_id"] for lin in grouped[1]] == ["a", "b"]
    assert [lin["action_id"] for lin in grouped[2]] == ["c"]


def test_group_lineage_by_stage() -> None:
    from modi_harness.trace.lineage import group_by_stage, read_lineage

    events = [
        _event("intent_lineage_recorded", _lineage(action_id="a", stage_id="stage-explore")),
        _event("intent_lineage_recorded", _lineage(action_id="b", stage_id="stage-plan")),
        _event("intent_lineage_recorded", _lineage(action_id="c", stage_id="stage-explore")),
    ]
    grouped = group_by_stage(read_lineage(events))
    assert sorted(grouped.keys()) == ["stage-explore", "stage-plan"]
    assert [lin["action_id"] for lin in grouped["stage-explore"]] == ["a", "c"]


def test_lineage_for_action_finds_the_record() -> None:
    from modi_harness.trace.lineage import lineage_for_action, read_lineage

    events = [
        _event("intent_lineage_recorded", _lineage(action_id="a")),
        _event("intent_lineage_recorded", _lineage(action_id="b", judgment_id="judg-1")),
    ]
    lineages = list(read_lineage(events))
    found = lineage_for_action(lineages, "b")
    assert found is not None
    assert found["judgment_id"] == "judg-1"
    assert lineage_for_action(lineages, "missing") is None
