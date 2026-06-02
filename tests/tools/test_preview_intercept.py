"""Preview mode: L1+ tools without dry_run get intercepted with synthetic success."""
from __future__ import annotations

from typing import Any

import pytest


def _proposal(tool_name: str = "save_draft", args: dict | None = None) -> dict:
    return {
        "tool_call_id": "tc1",
        "tool_name": tool_name,
        "arguments": args or {"name": "x.json", "content": "{}"},
        "malformed": False,
        "parse_error": None,
    }


def _make_gateway_state(tmp_path):
    """Build a minimal gateway + state for these tests."""
    from modi_harness.hooks import HookDispatcher, HookRegistry
    from modi_harness.policy import PolicyGate
    from modi_harness.tools import ToolGateway, ToolRegistry
    from modi_harness.workspace import WorkspaceManager

    registry = ToolRegistry()
    # Register builtin tools (save_draft is L1, no dry_run)
    from modi_harness.tools.builtin import get_builtin_specs
    for spec, handler in get_builtin_specs():
        registry.register_tool(spec, handler)

    policy = PolicyGate()
    hook_registry = HookRegistry.from_files(user_settings=None, project_settings=None)
    hooks = HookDispatcher(registry=hook_registry, project_root=tmp_path, pass_env=["PATH"])
    gateway = ToolGateway(
        registry=registry,
        policy=policy,
        hooks=hooks,
        result_inline_limit_bytes=8192,
        interactive=True,
    )

    workspace = WorkspaceManager(workspace_root=tmp_path / "ws")
    workspace.create_run("r1")

    state: dict[str, Any] = {
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "parent_thread_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": "preview",
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
        "pending_trace_events": [],
        "repair_used": 0,
    }
    agent = {
        "name": "x",
        "description": "",
        "instruction": "",
        "default_tools": [],
        "default_skills": [],
        "output_contract": None,
        "permission_profile": None,
        "safety_constraints": [],
        "tags": [],
        "metadata": {},
    }
    # Build deps minimally for builtin handler invocation
    from dataclasses import dataclass
    from modi_harness.memory import MemoryPaths, MemoryStore
    memory = MemoryStore(MemoryPaths(
        user=tmp_path / "mem/u",
        agent=tmp_path / "mem/a",
        project=tmp_path / "mem/p",
        conversation=tmp_path / "mem/c",
    ))

    @dataclass
    class _D:
        workspace: Any
        memory: Any
    deps = _D(workspace=workspace, memory=memory)
    return gateway, state, agent, deps


def test_preview_intercepts_l1_without_dry_run(tmp_path) -> None:
    """save_draft is L1, has no dry_run handler → preview should intercept."""
    gateway, state, agent, deps = _make_gateway_state(tmp_path)

    result = gateway.execute_tool_call(
        _proposal("save_draft", {"name": "x.json", "content": "{}"}),
        agent=agent,
        state=state,
        graph_deps=deps,
    )
    # Should NOT actually write to disk.
    drafts_dir = tmp_path / "ws" / "r1" / "drafts"
    files = list(drafts_dir.iterdir()) if drafts_dir.exists() else []
    assert files == [], f"preview wrote to disk: {files}"
    # Outcome is executed (not denied), but the result is synthetic.
    assert result.outcome == "executed"
    payload = result.record.get("result", {}) or {}
    assert payload.get("dry_run") is True or payload.get("simulated") is True


def test_preview_executes_l0_live(tmp_path) -> None:
    """L0 reads still run live in preview — they have no side effects."""
    gateway, state, agent, deps = _make_gateway_state(tmp_path)
    # First write a real file via auto so we have something to read.
    state["permission_mode"] = "auto"
    gateway.execute_tool_call(
        _proposal("save_draft", {"name": "x.json", "content": "{\"hi\": 1}"}),
        agent=agent, state=state, graph_deps=deps,
    )
    state["permission_mode"] = "preview"
    result = gateway.execute_tool_call(
        _proposal("read_workspace_file", {"kind": "draft", "name": "x.json"}),
        agent=agent, state=state, graph_deps=deps,
    )
    assert result.outcome == "executed"
    payload = result.record.get("result", {}) or {}
    # L0 result is the real file content, not a simulated marker.
    assert "content" in payload
    assert payload.get("simulated") is not True
