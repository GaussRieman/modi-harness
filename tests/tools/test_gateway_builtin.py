"""Tool gateway dispatch for builtin tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.policy import PolicyGate
from modi_harness.tools import ToolGateway, ToolRegistry
from modi_harness.tools.builtin import get_builtin_specs
from modi_harness.types import AgentProfile
from modi_harness.workspace import WorkspaceManager


@dataclass
class _Deps:
    workspace: WorkspaceManager


def _agent(name: str = "demo", tools: list[str] | None = None) -> AgentProfile:
    return {  # type: ignore[typeddict-item]
        "name": name,
        "description": "test",
        "instruction": "",
        "default_tools": tools or [],
        "default_skills": [],
        "output_contract": None,
        "permission_profile": {"mode": "auto"},
        "safety_constraints": [],
        "tags": [],
        "metadata": {},
    }


def _gateway() -> tuple[ToolGateway, ToolRegistry]:
    reg = ToolRegistry()
    for spec, handler in get_builtin_specs():
        reg.register_tool(spec, handler)
    hooks = HookDispatcher(registry=HookRegistry([]), project_root=Path.cwd(), pass_env=[])
    gw = ToolGateway(
        registry=reg,
        policy=PolicyGate(rule_packs=None),
        hooks=hooks,
        result_inline_limit_bytes=8192,
    )
    return gw, reg


def test_gateway_dispatches_builtin_without_agent_listing(tmp_path: Path) -> None:
    """agent.md does NOT list save_draft, but the gateway still dispatches it."""
    gw, _ = _gateway()
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    state = {
        "run_id": "run-1",
        "thread_id": "t-1",
        "permission_mode": "auto",
        "denied_actions": [],
    }

    proposal = {
        "tool_call_id": "tc-1",
        "tool_name": "save_draft",
        "arguments": {"name": "x.md", "content": "hi"},
        "malformed": False,
        "parse_error": None,
    }
    result = gw.execute_tool_call(
        proposal,
        agent=_agent(tools=[]),
        state=state,
        graph_deps=_Deps(workspace=wm),
    )
    assert result.outcome == "executed"
    assert (tmp_path / "ws" / "run-1" / "drafts" / "x.md").exists()


def test_gateway_save_draft_accepts_object_content(tmp_path: Path) -> None:
    """save_draft accepts a JSON object as content (auto-serialized).

    Regression: Agents may pass a JSON object to save_draft; an earlier schema
    pinned content to ``string`` only and made well-behaved agents loop forever
    on validation errors.
    """
    import json

    gw, _ = _gateway()
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    state = {
        "run_id": "run-1",
        "thread_id": "t-1",
        "permission_mode": "auto",
        "denied_actions": [],
    }

    briefing = {"question": "q?", "key_findings": [], "confidence": "low"}
    proposal = {
        "tool_call_id": "tc-1",
        "tool_name": "save_draft",
        "arguments": {"name": "briefing.json", "content": briefing},
        "malformed": False,
        "parse_error": None,
    }
    result = gw.execute_tool_call(
        proposal,
        agent=_agent(tools=[]),
        state=state,
        graph_deps=_Deps(workspace=wm),
    )
    assert result.outcome == "executed"
    saved = tmp_path / "ws" / "run-1" / "drafts" / "briefing.json"
    assert saved.exists()
    assert json.loads(saved.read_text()) == briefing


def test_gateway_still_validates_builtin_schema(tmp_path: Path) -> None:
    """Schema validation still rejects bad arguments for builtins."""
    gw, _ = _gateway()
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    state = {
        "run_id": "run-1",
        "thread_id": "t-1",
        "permission_mode": "auto",
        "denied_actions": [],
    }

    proposal = {
        "tool_call_id": "tc-1",
        "tool_name": "save_draft",
        "arguments": {"name": "x.md"},  # missing 'content'
        "malformed": False,
        "parse_error": None,
    }
    result = gw.execute_tool_call(
        proposal,
        agent=_agent(tools=[]),
        state=state,
        graph_deps=_Deps(workspace=wm),
    )
    assert result.outcome == "error"
    assert "schema" in (result.error_message or "").lower()
