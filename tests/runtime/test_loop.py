"""End-to-end Runtime Adapter tests with fake model + fake tools.

These exercise the full chain: AgentLoader → SkillLoader → MemoryStore →
ContextManager → ModelAdapter (fake) → ToolGateway → Policy → OutputController →
WorkspaceManager → TraceRecorder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness.agents import AgentLoader
from modi_harness.context import ContextManager
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.memory import MemoryPaths, MemoryStore
from modi_harness.models import ModelAdapter
from modi_harness.output import OutputController
from modi_harness.policy import PolicyGate
from modi_harness.runtime import RuntimeAdapter, RunTaskInput
from modi_harness.skills import SkillLoader
from modi_harness.tools import ToolGateway, ToolRegistry
from modi_harness.workspace import WorkspaceManager


# ----------------------------------------------------------------------
# scriptable fake chat model
# ----------------------------------------------------------------------


class ScriptedChatModel(BaseChatModel):
    """Returns canned AIMessages from a queue. Used to drive the runtime loop."""

    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[override]
        i = self.cursor["i"]
        if i >= len(self.script):
            raise RuntimeError(f"ScriptedChatModel exhausted after {i} calls")
        msg = self.script[i]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "scripted"


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


def _write_agent(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agents" / "demo.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p.parent


def _basic_agent_md(*, tools: list[str], skills: list[str] = ()) -> str:
    tools_yaml = "\n".join(f"  - {t}" for t in tools)
    skills_yaml = "\n".join(f"  - {s}" for s in skills) if skills else "  []"
    return f"""---
name: demo
description: demo agent
tools:
{tools_yaml}
skills:
{skills_yaml}
---
You are a test agent. Use your tools and produce a final reply.
"""


def _make_runtime(
    tmp_path: Path,
    *,
    agent_dir: Path,
    skill_dir: Path | None,
    scripted_messages: list[AIMessage],
    tool_specs: list[tuple[dict, Any]],
    rule_packs: list[str] | None = None,
) -> RuntimeAdapter:
    workspace = WorkspaceManager(workspace_root=tmp_path / "ws")
    memory = MemoryStore(
        MemoryPaths(
            user=tmp_path / "mem" / "user",
            agent=tmp_path / "mem" / "agent",
            project=tmp_path / "mem" / "project",
            conversation=tmp_path / "mem" / "conv",
        )
    )
    policy = PolicyGate(rule_packs=rule_packs)
    tool_registry = ToolRegistry()
    for spec, handler in tool_specs:
        tool_registry.register_tool(spec, handler)
    dispatcher = HookDispatcher(
        registry=HookRegistry([]),
        project_root=str(tmp_path),
        pass_env=[],
    )
    tool_gateway = ToolGateway(
        registry=tool_registry,
        policy=policy,
        hooks=dispatcher,
        result_inline_limit_bytes=8192,
    )
    context_manager = ContextManager(policy=policy)
    model = ScriptedChatModel(script=list(scripted_messages))
    model_adapter = ModelAdapter(chat_model=model)
    output = OutputController()
    return RuntimeAdapter(
        agent_loader=AgentLoader(project_dir=agent_dir),
        skill_loader=SkillLoader(project_dir=skill_dir) if skill_dir else None,
        memory_store=memory,
        workspace=workspace,
        context_manager=context_manager,
        model_adapter=model_adapter,
        tool_gateway=tool_gateway,
        policy=policy,
        output_controller=output,
        hooks=dispatcher,
        max_steps=8,
        repair_budget=2,
    )


# ----------------------------------------------------------------------
# scenarios
# ----------------------------------------------------------------------


def test_s1_governance_happy_path(tmp_path: Path) -> None:
    """Model proposes a tool call, tool executes, model returns final reply."""
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "search", "args": {"q": "modi"}, "id": "tc_1"}],
            ),
            AIMessage(content="Final answer: found three results."),
        ],
        tool_specs=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"results": [kw["q"]]},
            )
        ],
    )
    response = runtime.run(
        RunTaskInput(agent="demo", input={"goal": "search modi"}, options={})
    )
    assert response["status"] == "completed"
    assert "Final answer" in (response["output"] or {}).get("value", "")


def test_l3_tool_interrupts_for_approval(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["file_ticket"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_1"}],
            ),
        ],
        tool_specs=[
            (
                {
                    "name": "file_ticket",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"ticket_id": "T1"},
            )
        ],
    )
    response = runtime.run(
        RunTaskInput(agent="demo", input={}, options={})
    )
    assert response["status"] == "interrupted"
    assert response["pending_approval"] is not None
    assert response["pending_approval"]["decision"] == "require_approval"


def test_denied_retry_blocks_repeat(tmp_path: Path) -> None:
    """User rejects → model proposes same call again → blocked before execute."""
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["file_ticket"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_2"}],
            ),
            AIMessage(content="Could not file ticket; user has denied this action."),
        ],
        tool_specs=[
            (
                {
                    "name": "file_ticket",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"ticket_id": "T1"},
            )
        ],
    )
    first = runtime.run(RunTaskInput(agent="demo", input={}, options={}))
    assert first["status"] == "interrupted"

    rejected = runtime.reject(
        run_id=first["run_id"],
        approval_id=first["pending_approval"]["approval_id"],
        reason="user denied",
    )
    # Model retries same call -> denied-retry; then produces a final reply.
    assert rejected["status"] == "completed"
    trace_events = [
        e["event_type"]
        for e in runtime.read_trace(first["run_id"])
    ]
    assert "denial" in trace_events
    # Either Tool Gateway emitted tool_result with denied_retry or runtime
    # recorded a denial event — either is acceptable. The key invariant is the
    # action did NOT execute twice.


def test_plan_mode_no_side_effects(tmp_path: Path) -> None:
    """Plan mode: side-effect tools that lack dry_run_supported go to review."""
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["write_file"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "write_file", "args": {"path": "x"}, "id": "tc_1"}],
            ),
        ],
        tool_specs=[
            (
                {
                    "name": "write_file",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                    "risk_level": "L2",
                    "side_effect": True,
                },
                lambda **kw: {"written": kw["path"]},
            )
        ],
    )
    response = runtime.run(
        RunTaskInput(
            agent="demo",
            input={},
            options={},
            permission_mode="plan",
        )
    )
    assert response["status"] == "interrupted"
    assert response["pending_approval"]["decision"] == "require_review"


def test_max_steps_failure(tmp_path: Path) -> None:
    """Model keeps proposing tools without finalizing → step limit exhausted."""
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "search", "args": {"q": str(i)}, "id": f"tc_{i}"}],
            )
            for i in range(20)
        ],
        tool_specs=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"results": []},
            )
        ],
    )
    response = runtime.run(RunTaskInput(agent="demo", input={}, options={}))
    assert response["status"] == "failed"
    assert "step" in (response["error"] or {}).get("code", "").lower()


def test_approval_resume_executes_tool(tmp_path: Path) -> None:
    """Approve a pending L3 call → run resumes and tool executes."""
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["file_ticket"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_1"}],
            ),
            AIMessage(content="Ticket filed. Done."),
        ],
        tool_specs=[
            (
                {
                    "name": "file_ticket",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"ticket_id": "T1"},
            )
        ],
    )
    first = runtime.run(RunTaskInput(agent="demo", input={}, options={}))
    assert first["status"] == "interrupted"
    approved = runtime.approve(
        run_id=first["run_id"],
        approval_id=first["pending_approval"]["approval_id"],
        decision="approved",
    )
    assert approved["status"] == "completed"
    assert "Ticket filed" in (approved["output"] or {}).get("value", "")
