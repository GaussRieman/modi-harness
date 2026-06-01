"""ContextManager exposes builtin tools regardless of agent.md tools: list."""

from __future__ import annotations

import pytest

from modi_harness.context import ContextManager
from modi_harness.policy import PolicyGate
from modi_harness.tools import ToolRegistry
from modi_harness.tools.builtin import get_builtin_specs
from modi_harness.types import AgentProfile


def _agent(tools: list[str]) -> AgentProfile:
    return {  # type: ignore[typeddict-item]
        "name": "demo", "description": "x", "instruction": "",
        "default_tools": tools, "default_skills": [],
        "output_contract": None,
        "permission_profile": {"mode": "auto"},
        "safety_constraints": [], "tags": [], "metadata": {},
    }


def _state() -> dict:
    return {
        "run_id": "r", "root_run_id": "r", "parent_run_id": None,
        "parent_thread_id": None, "thread_id": "t",
        "agent_name": "demo", "permission_mode": "auto",
        "task": {}, "messages": [], "loaded_skills": [],
        "tool_calls": [], "denied_actions": [], "workspace_refs": [],
        "pending_approval": None, "draft_output": None, "final_output": None,
        "step_count": 0, "status": "running",
        "pending_trace_events": [], "repair_used": 0,
    }


def test_builtin_tools_visible_when_agent_lists_none() -> None:
    cm = ContextManager(policy=PolicyGate(rule_packs=None))
    catalog = {s["name"]: s for s, _ in get_builtin_specs()}
    pack = cm.build_context(
        state=_state(),
        agent=_agent(tools=[]),
        skills=[],
        memory_index={"records": [], "by_scope": {}, "by_type": {}, "by_tag": {}},
        workspace_index=[],
        tool_catalog=catalog,
        output_contract=None,
    )
    visible = {td["name"] for td in pack["tool_descriptions"]}
    # Every builtin should be visible.
    assert "save_draft" in visible
    assert "save_artifact" in visible
    assert "read_workspace_file" in visible


def test_agent_can_deny_specific_builtin() -> None:
    cm = ContextManager(policy=PolicyGate(rule_packs=None))
    catalog = {s["name"]: s for s, _ in get_builtin_specs()}
    agent = _agent(tools=[])
    agent["permission_profile"] = {"mode": "auto", "deny": ["save_memory"]}

    pack = cm.build_context(
        state=_state(),
        agent=agent,
        skills=[],
        memory_index={"records": [], "by_scope": {}, "by_type": {}, "by_tag": {}},
        workspace_index=[],
        tool_catalog=catalog,
        output_contract=None,
    )
    visible = {td["name"] for td in pack["tool_descriptions"]}
    assert "save_memory" not in visible
    assert "save_draft" in visible  # other builtins unaffected


# ---------------------------------------------------------------------------
# ModiHarness wiring tests (Task 12)
# ---------------------------------------------------------------------------

from pathlib import Path

from modi_harness import ModiHarness
from modi_harness.tools.builtin import BUILTIN_TOOL_NAMES


def test_modi_harness_registers_all_builtins_by_default(tmp_path: Path) -> None:
    h = ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
    )
    registered = set(h._tools_registry.names())
    for name in BUILTIN_TOOL_NAMES:
        assert name in registered, f"missing builtin {name!r}"


def test_modi_harness_disables_builtins_when_flag_false(tmp_path: Path) -> None:
    h = ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        enable_builtin_tools=False,
    )
    registered = set(h._tools_registry.names())
    for name in BUILTIN_TOOL_NAMES:
        assert name not in registered


def test_modi_harness_registers_builtin_subset(tmp_path: Path) -> None:
    h = ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        builtin_tools=["save_draft", "read_workspace_file"],
    )
    registered = set(h._tools_registry.names())
    assert "save_draft" in registered
    assert "read_workspace_file" in registered
    assert "save_artifact" not in registered
    assert "save_memory" not in registered
