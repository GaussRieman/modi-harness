"""Governance demotion: policy proves safety *beneath* alignment (plan N4.4).

Alignment decides intent-fit first. Governance is consulted only afterward, and
only to prove/enforce safety — it can elevate (ask, deny) but it cannot overturn
an alignment ``deny`` into execution.
"""
from __future__ import annotations

from typing import Any

from modi_harness.policy import PolicyGate


def _agent() -> dict[str, Any]:
    return {
        "name": "research-assistant",
        "default_tools": ["fetch_url"],
        "permission_profile": None,
    }


def _state(mode: str = "auto") -> dict[str, Any]:
    return {
        "permission_mode": mode,
        "denied_actions": [],
    }


def _spec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "fetch_url",
        "description": "",
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
    base.update(over)
    return base


def _decision(verdict: str, *, requirements: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": "ad-1",
        "decision": verdict,
        "reason": "test",
        "action_id": "act-1",
        "intent_version": 2,
        "stage_id": "stage-explore",
        "boundary_hits": [],
        "governance_requirements": requirements or [],
        "model_judged": True,
    }


def test_alignment_deny_never_executes_even_if_policy_allows() -> None:
    from modi_harness.governance.gate import GovernanceGate

    gate = GovernanceGate(PolicyGate())
    proof = gate.prove(
        _decision("deny"),
        agent=_agent(),
        spec=_spec(risk_level="L0"),  # policy alone would allow L0
        state=_state(),
        arguments={"url": "https://x"},
    )
    assert proof["outcome"] == "deny"
    # policy is not even the deciding factor — alignment deny is final
    assert "alignment" in proof["reason"].lower()


def test_alignment_allow_with_approval_requirement_requests_judgment() -> None:
    from modi_harness.governance.gate import GovernanceGate

    gate = GovernanceGate(PolicyGate())
    proof = gate.prove(
        _decision("allow", requirements=[{"kind": "approval", "reason": "judgment"}]),
        agent=_agent(),
        spec=_spec(risk_level="L0"),
        state=_state(),
        arguments={"url": "https://x"},
    )
    assert proof["outcome"] == "ask_judgment"


def test_alignment_allow_clean_executes() -> None:
    from modi_harness.governance.gate import GovernanceGate

    gate = GovernanceGate(PolicyGate())
    proof = gate.prove(
        _decision("allow"),
        agent=_agent(),
        spec=_spec(risk_level="L0"),
        state=_state(),
        arguments={"url": "https://x"},
    )
    assert proof["outcome"] == "execute"
    assert proof["policy_decision"] is not None


def test_alignment_allow_but_policy_elevates_requests_judgment() -> None:
    from modi_harness.governance.gate import GovernanceGate

    gate = GovernanceGate(PolicyGate())
    # L3 under auto -> policy require_approval; governance surfaces it as judgment
    proof = gate.prove(
        _decision("allow"),
        agent=_agent(),
        spec=_spec(name="place_order", risk_level="L3", side_effect=True),
        state=_state("auto"),
        arguments={"item": "x"},
    )
    assert proof["outcome"] == "ask_judgment"


def test_alignment_ask_judgment_requests_judgment() -> None:
    from modi_harness.governance.gate import GovernanceGate

    gate = GovernanceGate(PolicyGate())
    proof = gate.prove(
        _decision("ask_judgment"),
        agent=_agent(),
        spec=_spec(risk_level="L0"),
        state=_state(),
        arguments={"url": "https://x"},
    )
    assert proof["outcome"] == "ask_judgment"


def test_alignment_redirect_does_not_execute() -> None:
    from modi_harness.governance.gate import GovernanceGate

    gate = GovernanceGate(PolicyGate())
    proof = gate.prove(
        _decision("redirect"),
        agent=_agent(),
        spec=_spec(risk_level="L0"),
        state=_state(),
        arguments={"url": "https://x"},
    )
    assert proof["outcome"] == "redirect"


def test_alignment_constrain_requests_judgment_before_execution() -> None:
    from modi_harness.governance.gate import GovernanceGate

    gate = GovernanceGate(PolicyGate())
    proof = gate.prove(
        _decision("constrain"),
        agent=_agent(),
        spec=_spec(risk_level="L0"),
        state=_state(),
        arguments={"url": "https://x"},
    )
    assert proof["outcome"] == "ask_judgment"
    assert "constrain" in proof["reason"].lower()
