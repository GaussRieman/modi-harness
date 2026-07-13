"""Tests for the user/project ``settings.permissions`` layer in PolicyGate."""

from __future__ import annotations

from typing import Any

from modi_harness.config.settings import PermissionsSettings
from modi_harness.policy import PolicyGate

# ---------- helpers (mirror test_gate.py shape) ----------


def _agent_profile(**overrides: Any) -> dict:
    base = {
        "name": "x",
        "description": "y",
        "instruction": "",
        "default_tools": ["save_draft", "fetch_url", "send_email_blast"],
        "default_skills": [],
        "output_contract": None,
        "permission_profile": None,
        "safety_constraints": [],
        "tags": [],
        "metadata": {},
    }
    base.update(overrides)
    return base


def _state(**overrides: Any) -> dict:
    base = {
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": "auto",
        "task": {},
        "messages": [],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": [],
        "workspace_refs": [],
        "pending_approval": None,
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
    }
    base.update(overrides)
    return base


def _tool(risk: str, name: str) -> dict:
    return {
        "name": name,
        "description": "",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": risk,
        "side_effect": True,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": False,
        "dry_run_supported": False,
        "tags": [],
    }


def _ctx(*, risk: str, name: str, mode: str = "auto") -> dict:
    return {
        "agent": _agent_profile(),
        "skill": None,
        "tool_spec": _tool(risk, name),
        "state": _state(permission_mode=mode),
        "requested_action": {
            "kind": "tool_call",
            "tool_name": name,
            "arguments": {},
            "target": None,
            "fingerprint": "fp1",
        },
        "permission_mode": mode,
        "interactive": True,
    }


# ---------- tests ----------


def test_always_deny_by_tool_name() -> None:
    gate = PolicyGate(permissions=PermissionsSettings(always_deny=["send_email_blast"]))
    decision = gate.decide(_ctx(risk="L2", name="send_email_blast"))
    assert decision["decision"] == "deny"
    assert decision["audit"].get("layer") == "always_deny"


def test_always_deny_by_risk_token() -> None:
    """L4 listed as a risk token is enough — tool name need not match."""
    gate = PolicyGate(permissions=PermissionsSettings(always_deny=["L4"]))
    decision = gate.decide(_ctx(risk="L4", name="anything"))
    assert decision["decision"] == "deny"


def test_always_allow_promotes_l3_under_auto() -> None:
    """auto+L3 normally requires approval; always_allow lifts it to allow."""
    gate = PolicyGate(permissions=PermissionsSettings(always_allow=["fetch_url"]))
    decision = gate.decide(_ctx(risk="L3", name="fetch_url"))
    assert decision["decision"] == "allow"


def test_always_ask_demotes_l1_to_approval() -> None:
    """L1 normally allows; always_ask elevates to require_approval."""
    gate = PolicyGate(permissions=PermissionsSettings(always_ask=["save_draft"]))
    decision = gate.decide(_ctx(risk="L1", name="save_draft"))
    assert decision["decision"] == "require_approval"
    assert decision["approval_id"] is not None


def test_deny_beats_allow_when_both_match() -> None:
    """If a tool is on both lists, deny wins."""
    gate = PolicyGate(
        permissions=PermissionsSettings(
            always_allow=["send_email_blast"],
            always_deny=["send_email_blast"],
        )
    )
    decision = gate.decide(_ctx(risk="L3", name="send_email_blast"))
    assert decision["decision"] == "deny"


def test_agent_deny_list_beats_settings_allow() -> None:
    """Agent permission_profile.deny is hard — settings cannot override it."""
    gate = PolicyGate(permissions=PermissionsSettings(always_allow=["fetch_url"]))
    ctx = _ctx(risk="L3", name="fetch_url")
    ctx["agent"]["permission_profile"] = {"deny": ["fetch_url"]}
    decision = gate.decide(ctx)
    assert decision["decision"] == "deny"
    assert decision["audit"].get("check") == "deny_list"
