"""Offline tests for the support_triage multi-agent example."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_EXPERTS_PATH = Path(__file__).resolve().parents[2] / "examples" / "support_triage" / "_experts.py"


def _load_experts():
    """Load the example's _experts.py by file path (examples/ is not a package)."""
    spec = importlib.util.spec_from_file_location("support_triage_experts", _EXPERTS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lookup_account_known() -> None:
    experts = _load_experts()
    result = experts.lookup_account("acct_123")
    assert result["plan"] == "Pro"
    assert result["account_id"] == "acct_123"


def test_lookup_account_unknown() -> None:
    experts = _load_experts()
    result = experts.lookup_account("nope")
    assert "error" in result


def test_lookup_order_known() -> None:
    experts = _load_experts()
    result = experts.lookup_order("ord_555")
    assert result["refundable"] is True
    assert result["amount"] == 290


def test_lookup_order_unknown() -> None:
    experts = _load_experts()
    result = experts.lookup_order("nope")
    assert "error" in result


def test_build_triage_agent_topology() -> None:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    assert triage.name == "triage"
    # three specialists attached as subagents
    sub_names = sorted(a.name for a in triage.subagents)
    assert sub_names == ["billing", "refund", "technical"]
    # orchestrator declares delegate tools in its profile
    assert "billing" in (triage.permission_profile or {}).get("allowed_subagents", [])


def test_specialists_have_expected_tools() -> None:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    by_name = {a.name: a for a in triage.subagents}
    assert [t.spec["name"] for t in by_name["billing"].tools] == ["lookup_account"]
    assert [t.spec["name"] for t in by_name["refund"].tools] == ["lookup_order"]
    assert by_name["technical"].tools == ()  # pure-reasoning specialist
