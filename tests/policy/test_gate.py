"""Tests for PolicyGate."""

from __future__ import annotations

from typing import Any

import pytest

from modi_harness.policy import PolicyGate


# ---------- helpers ----------


def _agent_profile(**overrides: Any) -> dict:
    base = {
        "name": "x",
        "description": "y",
        "instruction": "",
        "default_tools": ["t_read", "t_draft", "t_business", "t_external"],
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
        "permission_mode": "ask",
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


def _tool(risk_level: str, name: str = "t_x", side_effect: bool = True) -> dict:
    return {
        "name": name,
        "description": "",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": risk_level,
        "side_effect": side_effect,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": False,
        "dry_run_supported": False,
        "tags": [],
    }


def _ctx(
    *,
    risk: str,
    mode: str = "ask",
    fingerprint: str = "fp1",
    target: dict[str, Any] | None = None,
    agent_overrides: dict | None = None,
    state_overrides: dict | None = None,
    requested_kind: str = "tool_call",
    tool_name: str | None = "t_x",
) -> dict:
    return {
        "agent": _agent_profile(**(agent_overrides or {})),
        "skill": None,
        "tool_spec": _tool(risk, name=tool_name or "t_x") if risk else None,
        "state": _state(permission_mode=mode, **(state_overrides or {})),
        "requested_action": {
            "kind": requested_kind,
            "tool_name": tool_name,
            "arguments": {},
            "target": target,
            "fingerprint": fingerprint,
        },
        "permission_mode": mode,
    }


# ---------- risk x mode matrix ----------


@pytest.mark.parametrize("risk,expected", [("L0", "allow"), ("L1", "allow")])
def test_low_risk_always_allowed(risk: str, expected: str) -> None:
    decision = PolicyGate().decide(_ctx(risk=risk))
    assert decision["decision"] == expected


def test_l2_in_workspace_allowed(tmp_path) -> None:
    decision = PolicyGate().decide(
        _ctx(risk="L2", target={"scope": "workspace"})
    )
    assert decision["decision"] == "allow"


def test_l2_outside_workspace_requires_approval() -> None:
    decision = PolicyGate().decide(
        _ctx(risk="L2", target={"scope": "external"})
    )
    assert decision["decision"] == "require_approval"


def test_l3_requires_approval() -> None:
    decision = PolicyGate().decide(_ctx(risk="L3"))
    assert decision["decision"] == "require_approval"
    assert decision["approval_id"] is not None


def test_l4_requires_approval_with_audit() -> None:
    decision = PolicyGate().decide(_ctx(risk="L4"))
    assert decision["decision"] == "require_approval"
    assert decision["approval_id"] is not None
    assert decision["audit"].get("requires_audit") is True


def test_auto_mode_preauthorized_l3_allowed() -> None:
    agent_overrides = {
        "permission_profile": {
            "mode": "auto",
            "preauthorized": ["t_x"],
            "deny": [],
            "review_required": [],
        }
    }
    decision = PolicyGate().decide(
        _ctx(risk="L3", mode="auto", agent_overrides=agent_overrides)
    )
    assert decision["decision"] == "allow"


def test_auto_mode_non_preauthorized_l3_still_approval() -> None:
    agent_overrides = {
        "permission_profile": {
            "mode": "auto",
            "preauthorized": ["other"],
            "deny": [],
            "review_required": [],
        }
    }
    decision = PolicyGate().decide(
        _ctx(risk="L3", mode="auto", agent_overrides=agent_overrides)
    )
    assert decision["decision"] == "require_approval"


def test_auto_mode_l4_still_approval_even_preauthorized() -> None:
    agent_overrides = {
        "permission_profile": {
            "mode": "auto",
            "preauthorized": ["t_x"],
            "deny": [],
            "review_required": [],
        }
    }
    decision = PolicyGate().decide(
        _ctx(risk="L4", mode="auto", agent_overrides=agent_overrides)
    )
    assert decision["decision"] == "require_approval"


def test_plan_mode_rewrites_l2_plus_to_review() -> None:
    for risk in ("L2", "L3", "L4"):
        decision = PolicyGate().decide(_ctx(risk=risk, mode="plan"))
        assert decision["decision"] == "require_review", risk


def test_plan_mode_keeps_l0_l1_allowed() -> None:
    for risk in ("L0", "L1"):
        decision = PolicyGate().decide(_ctx(risk=risk, mode="plan"))
        assert decision["decision"] == "allow"


def test_bypass_allows_l3_l4() -> None:
    decision = PolicyGate().decide(_ctx(risk="L3", mode="bypass"))
    assert decision["decision"] == "allow"


def test_review_required_list_overrides_approval() -> None:
    agent_overrides = {
        "permission_profile": {
            "mode": "ask",
            "preauthorized": [],
            "deny": [],
            "review_required": ["t_x"],
        }
    }
    decision = PolicyGate().decide(_ctx(risk="L3", agent_overrides=agent_overrides))
    assert decision["decision"] == "require_review"


def test_deny_list_always_denies() -> None:
    agent_overrides = {
        "permission_profile": {
            "mode": "ask",
            "preauthorized": [],
            "deny": ["t_x"],
            "review_required": [],
        }
    }
    decision = PolicyGate().decide(_ctx(risk="L1", agent_overrides=agent_overrides))
    assert decision["decision"] == "deny"


# ---------- denied-retry ----------


def test_denied_retry_rejected_even_in_bypass() -> None:
    state_overrides = {
        "denied_actions": [
            {
                "fingerprint": "fpD",
                "tool_name": "t_x",
                "arguments": {},
                "reason": "user denied",
                "decided_at": "2026-05-28T00:00:00.000Z",
            }
        ]
    }
    decision = PolicyGate().decide(
        _ctx(risk="L1", mode="bypass", fingerprint="fpD", state_overrides=state_overrides)
    )
    assert decision["decision"] == "deny"
    assert decision["denied_retry"] is True


# ---------- memory_write decisions ----------


def test_memory_write_conversation_allowed() -> None:
    ctx = _ctx(risk="", requested_kind="memory_write", tool_name=None, target={"scope": "conversation"})
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "allow"


def test_memory_write_thread_allowed() -> None:
    ctx = _ctx(risk="", requested_kind="memory_write", tool_name=None, target={"scope": "thread"})
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "allow"


def test_memory_write_project_requires_approval() -> None:
    ctx = _ctx(risk="", requested_kind="memory_write", tool_name=None, target={"scope": "project"})
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "require_approval"


def test_memory_write_workspace_requires_approval() -> None:
    ctx = _ctx(risk="", requested_kind="memory_write", tool_name=None, target={"scope": "workspace"})
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "require_approval"


def test_memory_write_from_untrusted_denied() -> None:
    ctx = _ctx(
        risk="",
        requested_kind="memory_write",
        tool_name=None,
        target={"scope": "user", "source_kind": "tool_result"},
    )
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "deny"


# ---------- output_finalize decisions ----------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("validated", "allow"),
        ("final", "allow"),
        ("needs_review", "require_review"),
        ("rejected", "deny"),
    ],
)
def test_output_finalize_by_status(status: str, expected: str) -> None:
    ctx = _ctx(
        risk="",
        requested_kind="output_finalize",
        tool_name=None,
        target={"status": status},
    )
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == expected


# ---------- visible_tools ----------


def test_visible_tools_intersection() -> None:
    profile = _agent_profile(default_tools=["a", "b", "c"])
    state = _state()
    visible = PolicyGate().visible_tools(profile, "ask", state)
    assert visible == ["a", "b", "c"]


def test_visible_tools_respects_deny_list() -> None:
    profile = _agent_profile(
        default_tools=["a", "b", "c"],
        permission_profile={"mode": "ask", "preauthorized": [], "deny": ["b"], "review_required": []},
    )
    state = _state()
    visible = PolicyGate().visible_tools(profile, "ask", state)
    assert "b" not in visible


# ---------- purity ----------


def test_decide_is_pure() -> None:
    gate = PolicyGate()
    ctx = _ctx(risk="L3")
    a = gate.decide(ctx)
    b = gate.decide(ctx)
    assert a["decision"] == b["decision"]
    assert a["audit"] == b["audit"]


# ---------- rule pack ----------


def test_coding_rule_pack_denies_git_mutation() -> None:
    gate = PolicyGate(rule_packs=["core", "coding"])
    ctx = _ctx(risk="L1", tool_name="git_push")
    decision = gate.decide(ctx)
    assert decision["decision"] == "deny"
    assert "coding" in decision["audit"].get("rule_pack_hits", [])


# ---------- TTY-aware auto mode ----------


def test_auto_l3_interactive_requires_approval() -> None:
    """auto + TTY: L3 still requires human → require_approval."""
    ctx = _ctx(risk="L3", mode="auto")
    ctx["interactive"] = True
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "require_approval"


def test_auto_l3_non_interactive_denies() -> None:
    """auto + no TTY: L3 cannot ask the user → deny."""
    ctx = _ctx(risk="L3", mode="auto")
    ctx["interactive"] = False
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "deny"
    assert "non-interactive" in decision["reason"] or "no human" in decision["reason"].lower()


def test_auto_l4_non_interactive_denies() -> None:
    ctx = _ctx(risk="L4", mode="auto")
    ctx["interactive"] = False
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "deny"


def test_auto_l4_interactive_requires_approval() -> None:
    ctx = _ctx(risk="L4", mode="auto")
    ctx["interactive"] = True
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "require_approval"


def test_auto_l1_unaffected_by_interactive_flag() -> None:
    """L1 is always allow under auto — interactive doesn't matter."""
    for interactive in (True, False):
        ctx = _ctx(risk="L1", mode="auto")
        ctx["interactive"] = interactive
        assert PolicyGate().decide(ctx)["decision"] == "allow"


def test_auto_default_interactive_true_when_unset() -> None:
    """If `interactive` is missing, default to True (preserve old ask behavior)."""
    ctx = _ctx(risk="L3", mode="auto")
    # No interactive key set
    decision = PolicyGate().decide(ctx)
    assert decision["decision"] == "require_approval"


# ---------- MODI_INTERACTIVE env override ----------


def test_modi_interactive_env_override(monkeypatch) -> None:
    """MODI_INTERACTIVE=0 forces non-interactive even in default."""
    from modi_harness.tools.gateway import _detect_interactive

    monkeypatch.delenv("MODI_INTERACTIVE", raising=False)
    assert _detect_interactive() is True

    for value in ("0", "false", "no", "off", "FALSE", " 0 ", ""):
        monkeypatch.setenv("MODI_INTERACTIVE", value)
        assert _detect_interactive() is False, f"value={value!r}"

    for value in ("1", "true", "yes"):
        monkeypatch.setenv("MODI_INTERACTIVE", value)
        assert _detect_interactive() is True, f"value={value!r}"
