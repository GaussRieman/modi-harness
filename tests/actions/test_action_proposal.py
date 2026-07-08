"""ActionProposal normalization (plan N4.1).

A model never sends an opaque tool call straight into governance. The runtime
normalizes every proposed action into an ``ActionProposal`` carrying intent
lineage (version + stage) and a mechanically-derived ``ActionImpact`` first.
"""
from __future__ import annotations

from typing import Any, get_type_hints

from modi_harness.types import ToolSpec


def _spec(**overrides: Any) -> ToolSpec:
    base: dict[str, Any] = {
        "name": "fetch_url",
        "description": "fetch a url",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": "L1",
        "side_effect": False,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": False,
        "dry_run_supported": False,
        "tags": [],
        "kind": "regular",
        "subagent_target": None,
    }
    base.update(overrides)
    return ToolSpec(**base)  # type: ignore[typeddict-item]


def _tc(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_call_id": "tc-1",
        "tool_name": tool_name,
        "arguments": arguments,
        "malformed": False,
        "parse_error": None,
    }


# --- marker: shape -----------------------------------------------------------


def test_tool_call_normalizes_to_action_proposal() -> None:
    from modi_harness.actions import ActionProposal

    hints = get_type_hints(ActionProposal)
    required = {
        "id",
        "kind",
        "summary",
        "tool_name",
        "arguments",
        "intent_version",
        "stage_id",
        "parent_step_id",
        "impact",
    }
    assert required <= set(hints), f"missing proposal fields: {required - set(hints)}"


def test_action_impact_shape() -> None:
    from modi_harness.actions import ActionImpact

    hints = get_type_hints(ActionImpact)
    required = {
        "risk_level",
        "side_effect",
        "external_commitment",
        "irreversible",
        "user_visible_state_changes",
        "changes_scope_or_goal",
        "sensitive_data",
        "cost_impact",
    }
    assert required <= set(hints), f"missing impact fields: {required - set(hints)}"


# --- behavior ----------------------------------------------------------------


def test_normal_tool_call_becomes_proposal() -> None:
    from modi_harness.actions import from_tool_call

    p = from_tool_call(
        _tc("fetch_url", {"url": "https://example.com"}),
        spec=_spec(),
        intent_version=3,
        stage_id="stage-explore",
    )
    assert p["kind"] == "tool_call"
    assert p["tool_name"] == "fetch_url"
    assert p["arguments"] == {"url": "https://example.com"}
    assert p["intent_version"] == 3
    assert p["stage_id"] == "stage-explore"
    assert p["parent_step_id"] is None
    assert p["impact"]["risk_level"] == "L1"
    assert p["id"]  # populated


def test_parent_step_id_is_carried_from_tool_metadata() -> None:
    from modi_harness.actions import from_tool_call

    call = _tc("fetch_url", {"url": "https://example.com"})
    call["metadata"] = {"parent_step_id": "loop-abc-0001"}

    p = from_tool_call(
        call,
        spec=_spec(),
        intent_version=3,
        stage_id="stage-explore",
    )

    assert p["parent_step_id"] == "loop-abc-0001"


def test_step_lineage_required_only_for_consequential_actions() -> None:
    from modi_harness.actions import from_tool_call, requires_step_lineage

    read_only = from_tool_call(
        _tc("fetch_url", {"url": "https://example.com"}),
        spec=_spec(name="fetch_url", side_effect=False),
        intent_version=1,
        stage_id="s",
    )
    side_effect = from_tool_call(
        _tc("write_file", {"path": "/tmp/x"}),
        spec=_spec(name="write_file", side_effect=True),
        intent_version=1,
        stage_id="s",
    )

    assert requires_step_lineage(read_only) is False
    assert requires_step_lineage(side_effect) is True


def test_submit_output_becomes_output_finalize() -> None:
    from modi_harness.actions import from_tool_call

    p = from_tool_call(
        _tc("submit_output", {"status": "final"}),
        spec=_spec(name="submit_output", risk_level="L1"),
        intent_version=1,
        stage_id="stage-deliver",
    )
    assert p["kind"] == "output_finalize"


def test_stage_transition_proposal_supported() -> None:
    from modi_harness.actions import from_tool_call

    p = from_tool_call(
        _tc("stage_transition", {"to": "deliver"}),
        spec=_spec(name="stage_transition", risk_level="L0"),
        intent_version=2,
        stage_id="stage-verify",
    )
    assert p["kind"] == "stage_transition"
    assert p["impact"]["changes_scope_or_goal"] is True


def test_same_tool_different_impact_from_args() -> None:
    from modi_harness.actions import from_tool_call

    # A *side-effecting* call (e.g. placing an order) commits externally only when
    # its args reach a remote endpoint; the same call against a local file does
    # not. External commitment is about committing to the outside world, so it
    # requires a side effect — a read-only GET of a remote URL is not a
    # commitment (see test_readonly_remote_fetch_is_not_external_commitment).
    spec = _spec(name="post_order", risk_level="L2", side_effect=True)
    external = from_tool_call(
        _tc("post_order", {"url": "https://api.example.com/order"}),
        spec=spec,
        intent_version=1,
        stage_id="s",
    )
    local = from_tool_call(
        _tc("post_order", {"url": "file:///tmp/local.json"}),
        spec=spec,
        intent_version=1,
        stage_id="s",
    )
    assert external["impact"]["external_commitment"] is True
    assert local["impact"]["external_commitment"] is False


def test_readonly_remote_fetch_is_not_external_commitment() -> None:
    """Model-first: reading a remote URL is a reasoning call for the model to
    judge, not a mechanical external *commitment*. A read-only GET (no side
    effect) reaching the network must not auto-escalate via the impact floor —
    otherwise a research agent's every fetch interrupts for judgment. The risk
    ceiling and the model judge still govern it; the mechanical impact does not
    pre-decide it is a commitment.
    """
    from modi_harness.actions import from_tool_call

    p = from_tool_call(
        _tc("fetch_url", {"url": "https://example.com/article"}),
        spec=_spec(name="fetch_url", risk_level="L1", side_effect=False),
        intent_version=1,
        stage_id="s",
    )
    assert p["impact"]["external_commitment"] is False
    # An explicit spec tag still marks genuine commitments regardless of method.
    tagged = from_tool_call(
        _tc("fetch_url", {"url": "https://example.com/article"}),
        spec=_spec(name="fetch_url", risk_level="L1", side_effect=False, tags=["external_commitment"]),
        intent_version=1,
        stage_id="s",
    )
    assert tagged["impact"]["external_commitment"] is True


def test_spec_tags_drive_impact() -> None:
    from modi_harness.actions import from_tool_call

    p = from_tool_call(
        _tc("place_order", {"item": "x"}),
        spec=_spec(
            name="place_order",
            risk_level="L3",
            side_effect=True,
            tags=["irreversible", "external_commitment", "sensitive_data"],
        ),
        intent_version=1,
        stage_id="s",
    )
    impact = p["impact"]
    assert impact["irreversible"] is True
    assert impact["external_commitment"] is True
    assert impact["sensitive_data"] is True
    assert impact["side_effect"] is True
