"""Tests for Tool Gateway."""

from __future__ import annotations

from typing import Any

import pytest

from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.policy import PolicyGate
from modi_harness.tools import (
    ToolDispatchResult,
    ToolGateway,
    ToolRegistry,
    ToolUnknownError,
)


# ---------- helpers ----------


def _spec(
    name: str = "t_x",
    risk_level: str = "L1",
    *,
    side_effect: bool = False,
    idempotent: bool = False,
    dry_run_supported: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        "output_schema": None,
        "risk_level": risk_level,
        "side_effect": side_effect,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": idempotent,
        "dry_run_supported": dry_run_supported,
        "tags": [],
    }


def _agent(default_tools: list[str] | None = None, *, profile: dict | None = None) -> dict:
    return {
        "name": "x",
        "description": "y",
        "instruction": "",
        "default_tools": default_tools if default_tools is not None else ["t_x"],
        "default_skills": [],
        "output_contract": None,
        "permission_profile": profile,
        "safety_constraints": [],
        "tags": [],
        "metadata": {},
    }


def _state(*, mode: str = "ask", denied: list | None = None) -> dict:
    return {
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": mode,
        "task": {},
        "messages": [],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": denied or [],
        "workspace_refs": [],
        "pending_approval": None,
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
    }


def _proposal(tool: str = "t_x", args: dict | None = None) -> dict:
    return {
        "tool_call_id": "01H_TC",
        "tool_name": tool,
        "arguments": args if args is not None else {"q": "hi"},
        "malformed": False,
        "parse_error": None,
    }


def _empty_dispatcher() -> HookDispatcher:
    return HookDispatcher(registry=HookRegistry([]), project_root=".", pass_env=[])


def _gateway(
    handlers: dict[str, Any] | None = None,
    *,
    specs: list[dict] | None = None,
    rule_packs: list[str] | None = None,
    dispatcher: HookDispatcher | None = None,
    inline_limit: int = 8192,
) -> ToolGateway:
    registry = ToolRegistry()
    for spec in specs or []:
        registry.register_tool(spec, handlers.get(spec["name"]) if handlers else (lambda **kw: {}))
    return ToolGateway(
        registry=registry,
        policy=PolicyGate(rule_packs=rule_packs),
        hooks=dispatcher or _empty_dispatcher(),
        result_inline_limit_bytes=inline_limit,
    )


# ---------- registry ----------


def test_unknown_tool_fails_closed() -> None:
    gw = _gateway()
    result = gw.execute_tool_call(_proposal("missing"), agent=_agent(), state=_state())
    assert result.outcome == "error"
    assert isinstance(result.error, ToolUnknownError) or "unknown" in (result.error_message or "")


def test_register_tool_applies_defaults() -> None:
    registry = ToolRegistry()
    registry.register_tool(
        {
            "name": "t",
            "description": "",
            "input_schema": {"type": "object"},
            "risk_level": "L1",
            "side_effect": False,
        },
        lambda **kw: {"ok": True},
    )
    spec = registry.get("t")
    assert spec["timeout_seconds"] == 30  # default applied
    assert spec["allowed_agents"] == []
    assert spec["dry_run_supported"] is False


# ---------- happy path ----------


def test_allow_executes_handler(tmp_path) -> None:
    gw = _gateway(
        handlers={"t_x": lambda **kw: {"echo": kw["q"]}},
        specs=[_spec("t_x", "L1")],
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "executed"
    assert result.record["result"] == {"echo": "hi"}
    assert result.record["decision"] == "allow"


# ---------- visibility ----------


def test_tool_not_in_agent_default_tools_denied() -> None:
    gw = _gateway(
        handlers={"t_other": lambda **kw: {}},
        specs=[_spec("t_other", "L1")],
    )
    result = gw.execute_tool_call(
        _proposal("t_other"),
        agent=_agent(default_tools=["different"]),
        state=_state(),
    )
    assert result.outcome == "error"


# ---------- schema validation ----------


def test_invalid_arguments_rejected() -> None:
    gw = _gateway(
        handlers={"t_x": lambda **kw: {"ok": True}},
        specs=[_spec("t_x", "L1")],
    )
    result = gw.execute_tool_call(
        {**_proposal(), "arguments": {}},  # missing required "q"
        agent=_agent(),
        state=_state(),
    )
    assert result.outcome == "error"
    assert "schema" in (result.error_message or "").lower()


# ---------- policy decisions ----------


def test_l3_requires_approval_does_not_execute() -> None:
    called: list[Any] = []

    def handler(**kw: Any) -> dict[str, Any]:
        called.append(kw)
        return {"ok": True}

    gw = _gateway(handlers={"t_x": handler}, specs=[_spec("t_x", "L3")])
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "interrupt"
    assert result.record["decision"] == "require_approval"
    assert called == []


def test_denied_retry_blocks_before_policy() -> None:
    gw = _gateway(
        handlers={"t_x": lambda **kw: {"ok": True}},
        specs=[_spec("t_x", "L1")],
    )
    state = _state(
        denied=[
            {
                "fingerprint": "fp",
                "tool_name": "t_x",
                "arguments": {"q": "hi"},
                "reason": "user denied",
                "decided_at": "2026-05-28T00:00:00.000Z",
            }
        ]
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=state)
    assert result.outcome == "denied_retry"


# ---------- hook integration ----------


def test_pre_tool_use_hook_block_converts_to_denial(tmp_path) -> None:
    import json
    settings = tmp_path / "s.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "pre_tool_use",
                        "command": "python:modi_harness._test_fixtures.hook_inproc.hook_block",
                        "blocking": True,
                        "pass_payload": "stdin",
                        "capture": "stdout",
                        "on_failure": "warn",
                        "timeout_seconds": 5,
                    }
                ]
            }
        )
    )
    dispatcher = HookDispatcher(
        registry=HookRegistry.from_files(None, settings),
        project_root=str(tmp_path),
        pass_env=[],
    )
    gw = _gateway(
        handlers={"t_x": lambda **kw: {"ok": True}},
        specs=[_spec("t_x", "L1")],
        dispatcher=dispatcher,
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "hook_blocked"


# ---------- result normalization ----------


def test_large_result_offloaded_to_workspace_ref() -> None:
    gw = _gateway(
        handlers={"t_x": lambda **kw: {"blob": "x" * 100}},
        specs=[_spec("t_x", "L1")],
        inline_limit=50,
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    # Either inline-truncated marker or instruction to offload via WorkspaceManager.
    rec = result.record
    assert rec["result"] is not None
    # Trust annotation present in metadata.
    assert result.trust["trust_level"] == "untrusted"


# ---------- idempotency ----------


def test_idempotent_call_cached_within_run() -> None:
    counter = {"n": 0}

    def handler(**kw: Any) -> dict[str, Any]:
        counter["n"] += 1
        return {"i": counter["n"]}

    gw = _gateway(
        handlers={"t_x": handler},
        specs=[_spec("t_x", "L1", idempotent=True)],
    )
    a = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    b = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert a.record["result"] == b.record["result"]
    assert counter["n"] == 1


# ---------- plan mode dry-run ----------


def test_plan_mode_dry_run_when_supported() -> None:
    def handler(**kw: Any) -> dict[str, Any]:
        return {"executed": True}

    def dry_run(**kw: Any) -> dict[str, Any]:
        return {"would_do": kw}

    registry = ToolRegistry()
    registry.register_tool(
        _spec("t_x", "L2", side_effect=True, dry_run_supported=True),
        handler,
        dry_run=dry_run,
    )
    gw = ToolGateway(
        registry=registry,
        policy=PolicyGate(),
        hooks=_empty_dispatcher(),
        result_inline_limit_bytes=8192,
    )
    result = gw.execute_tool_call(
        _proposal(),
        agent=_agent(default_tools=["t_x"]),
        state=_state(mode="plan"),
    )
    assert result.outcome == "executed"  # dry-run is a successful execution
    assert "would_do" in result.record["result"]
