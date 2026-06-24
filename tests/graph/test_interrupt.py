"""Interrupt + Command(resume=) round-trip on the compiled graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import Field

from modi_harness._utils import new_ulid
from modi_harness.agents import AgentLoader
from modi_harness.context import ContextManager
from modi_harness.graph import GraphDeps, build_main_graph
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.memory import MemoryPaths, MemoryStore
from modi_harness.models import ModelAdapter
from modi_harness.output import OutputController
from modi_harness.policy import PolicyGate
from modi_harness.tools import ToolGateway, ToolRegistry
from modi_harness.workspace import WorkspaceManager


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "script"


def _write_agent(root: Path, name: str, tools: list[str]) -> None:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tool_block = "\n".join(f"  - {t}" for t in tools)
    p.write_text(
        f"""---
name: {name}
description: demo
tools:
{tool_block}
permission_profile:
  mode: auto
---
Reply.
"""
    )


def _deps(tmp_path: Path, chat_model: BaseChatModel) -> tuple[GraphDeps, ToolRegistry]:
    registry = ToolRegistry()
    workspace = WorkspaceManager(workspace_root=tmp_path / "ws")
    memory_root = tmp_path / "mem"
    memory = MemoryStore(
        MemoryPaths(
            user=memory_root / "user",
            agent=memory_root / "agent",
            workspace=memory_root / "workspace",
            thread=memory_root / "thread",
        )
    )
    policy = PolicyGate()
    hook_registry = HookRegistry.from_files(user_settings=None, project_settings=None)
    hooks = HookDispatcher(registry=hook_registry, project_root=tmp_path, pass_env=["PATH"])
    gateway = ToolGateway(
        registry=registry, policy=policy, hooks=hooks, result_inline_limit_bytes=8192
    )
    context = ContextManager(policy=policy)
    output = OutputController()
    deps = GraphDeps(
        agents=AgentLoader(project_dir=tmp_path / "agents"),
        skills=None,
        memory=memory,
        workspace=workspace,
        context=context,
        model=ModelAdapter(chat_model=chat_model),
        tools=gateway,
        policy=policy,
        output=output,
        hooks=hooks,
    )
    return deps, registry


def _seed(thread_id: str) -> dict[str, Any]:
    run_id = new_ulid()
    return {
        "run_id": run_id,
        "root_run_id": run_id,
        "parent_run_id": None,
        "parent_thread_id": None,
        "thread_id": thread_id,
        "agent_name": "demo",
        "permission_mode": "auto",
        "task": {"goal": "do thing"},
        "messages": [{"role": "user", "content": "go", "tool_call_id": None, "metadata": {}}],
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
        "max_steps": 20,
    }


def test_interrupt_and_resume_approve(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo", tools=["send"])
    deps, registry = _deps(
        tmp_path,
        _ScriptModel(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc1"}],
                ),
                AIMessage(content="done"),
            ]
        ),
    )
    registry.register_tool(
        {
            "name": "send",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"to": {"type": "string"}},
                "required": ["to"],
            },
            "risk_level": "L3",
            "side_effect": True,
        },
        lambda **kw: {"sent": kw["to"]},
    )
    checkpointer = MemorySaver()
    graph = build_main_graph(deps, checkpointer=checkpointer)
    config = {
        "configurable": {"thread_id": "t1", "modi_deps": deps},
    }
    graph.invoke(_seed("t1"), config=config)
    # The graph paused on interrupt; inspect via get_state.
    snap = graph.get_state(config)
    assert snap.next  # has next nodes (still running)
    # Approve.
    out2 = graph.invoke(
        Command(resume={"decision": "approved", "approval_id": "any"}),
        config=config,
    )
    assert out2["status"] == "completed"
    assert out2["final_output"]["value"] == "done"


def test_interrupt_and_resume_reject(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo", tools=["send"])
    deps, registry = _deps(
        tmp_path,
        _ScriptModel(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc1"}],
                ),
                AIMessage(content="ok i stopped"),
            ]
        ),
    )
    registry.register_tool(
        {
            "name": "send",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"to": {"type": "string"}},
                "required": ["to"],
            },
            "risk_level": "L3",
            "side_effect": True,
        },
        lambda **kw: {"sent": kw["to"]},
    )
    graph = build_main_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t2", "modi_deps": deps}}
    graph.invoke(_seed("t2"), config=config)
    out = graph.invoke(
        Command(resume={"decision": "rejected", "reason": "nope", "approval_id": "any"}),
        config=config,
    )
    assert out["status"] == "completed"
    assert out["final_output"]["value"] == "ok i stopped"
    assert any(d["tool_name"] == "send" for d in out["denied_actions"])


def _send_spec() -> dict[str, Any]:
    return {
        "name": "send",
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
        },
        "risk_level": "L3",
        "side_effect": True,
    }


def test_resume_with_judgment_approve_executes(tmp_path: Path) -> None:
    """A judgment payload (kind=approve) runs the reviewed action."""
    _write_agent(tmp_path / "agents", "demo", tools=["send"])
    deps, registry = _deps(
        tmp_path,
        _ScriptModel(
            script=[
                AIMessage(content="", tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc1"}]),
                AIMessage(content="done"),
            ]
        ),
    )
    registry.register_tool(_send_spec(), lambda **kw: {"sent": kw["to"]})
    graph = build_main_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "tj1", "modi_deps": deps}}
    graph.invoke(_seed("tj1"), config=config)
    out = graph.invoke(
        Command(resume={"kind": "approve", "judgment_id": "j1"}),
        config=config,
    )
    assert out["status"] == "completed"
    assert out["final_output"]["value"] == "done"
    # The reviewed action actually executed (not just the loop continuing).
    sent = [r for r in out["tool_calls"] if r["tool_name"] == "send"]
    assert sent and sent[-1]["result"] == {"sent": "x"}
    assert not any(d["tool_name"] == "send" for d in out.get("denied_actions", []))


def test_resume_with_judgment_reject_denies(tmp_path: Path) -> None:
    """A judgment payload (kind=reject) denies the action like the old reject."""
    _write_agent(tmp_path / "agents", "demo", tools=["send"])
    deps, registry = _deps(
        tmp_path,
        _ScriptModel(
            script=[
                AIMessage(content="", tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc1"}]),
                AIMessage(content="stopped"),
            ]
        ),
    )
    registry.register_tool(_send_spec(), lambda **kw: {"sent": kw["to"]})
    graph = build_main_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "tj2", "modi_deps": deps}}
    graph.invoke(_seed("tj2"), config=config)
    out = graph.invoke(
        Command(resume={"kind": "reject", "judgment_id": "j2", "rationale": "no"}),
        config=config,
    )
    assert out["status"] == "completed"
    assert any(d["tool_name"] == "send" for d in out["denied_actions"])


def test_resume_with_judgment_revise_updates_intent(tmp_path: Path) -> None:
    """A revise judgment denies the action and bumps the intent version."""
    _write_agent(tmp_path / "agents", "demo", tools=["send"])
    deps, registry = _deps(
        tmp_path,
        _ScriptModel(
            script=[
                AIMessage(content="", tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc1"}]),
                AIMessage(content="replanned"),
            ]
        ),
    )
    registry.register_tool(_send_spec(), lambda **kw: {"sent": kw["to"]})
    graph = build_main_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "tj3", "modi_deps": deps}}
    graph.invoke(_seed("tj3"), config=config)
    snap_before = graph.get_state(config)
    version_before = snap_before.values["human_intent"]["version"]
    out = graph.invoke(
        Command(
            resume={
                "kind": "revise",
                "judgment_id": "j3",
                "rationale": "wrong target",
                "intent_updates": {"goal": "send to the right person"},
            }
        ),
        config=config,
    )
    assert out["status"] == "completed"
    # Action denied (revise does not authorize the reviewed action).
    assert any(d["tool_name"] == "send" for d in out["denied_actions"])
    # Intent updated: version bumped and goal changed.
    assert out["human_intent"]["version"] == version_before + 1
    assert out["human_intent"]["goal"] == "send to the right person"
