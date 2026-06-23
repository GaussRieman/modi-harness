"""N1.3 — the graph seeds an authoritative HumanIntentContext.

Every run starts with an intent field, even thin ones, and a thin task is
``running`` (not failed/blocked) at seed time. The seed also carries the
agent's safety constraints down as hard boundaries.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness.agents import AgentLoader
from modi_harness.context import ContextManager
from modi_harness.graph import GraphDeps
from modi_harness.graph.harness_adapter import HarnessGraphAdapter, RunTaskInput
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.memory import MemoryStore
from modi_harness.memory.store import MemoryPaths
from modi_harness.models import ModelAdapter
from modi_harness.output import OutputController
from modi_harness.policy import PolicyGate
from modi_harness.tools import ToolGateway, ToolRegistry
from modi_harness.workspace import WorkspaceManager


class _IdleModel(BaseChatModel):
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise RuntimeError("seed test should not call the model")

    @property
    def _llm_type(self) -> str:
        return "idle"


def _make_adapter(tmp_path: Path, *, agent_md: str) -> HarnessGraphAdapter:
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "demo.md").write_text(agent_md)
    policy = PolicyGate(rule_packs=None)
    deps = GraphDeps(
        agents=AgentLoader(project_dir=agent_dir),
        skills=None,
        memory=MemoryStore(
            MemoryPaths(
                user=tmp_path / "mem" / "user",
                agent=tmp_path / "mem" / "agent",
                workspace=tmp_path / "mem" / "workspace",
                thread=tmp_path / "mem" / "thread",
            )
        ),
        workspace=WorkspaceManager(workspace_root=tmp_path / "ws"),
        context=ContextManager(policy=policy),
        model=ModelAdapter(chat_model=_IdleModel()),
        tools=ToolGateway(
            registry=ToolRegistry(),
            policy=policy,
            hooks=HookDispatcher(
                registry=HookRegistry([]), project_root=str(tmp_path), pass_env=[]
            ),
            result_inline_limit_bytes=8192,
        ),
        policy=policy,
        output=OutputController(),
        hooks=HookDispatcher(
            registry=HookRegistry([]), project_root=str(tmp_path), pass_env=[]
        ),
    )
    return HarnessGraphAdapter(deps=deps, checkpointer=MemorySaver(), max_steps=4)


_AGENT_MD = """---
name: demo
description: demo agent
tools: []
skills: []
safety_constraints:
  - do not invent facts outside provided sources
---
You are a test agent.
"""


def test_seed_state_includes_human_intent(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path, agent_md=_AGENT_MD)
    state: dict[str, Any] = adapter._seed_state(
        RunTaskInput(agent="demo", input={"prompt": "look into something vague"})
    )

    assert "human_intent" in state
    intent = state["human_intent"]
    assert intent["version"] == 1
    assert intent["goal"] == "look into something vague"
    # Top-level lineage shortcuts mirror the embedded intent.
    assert state["intent_version"] == 1
    assert state["stage_id"] == intent["current_stage"]["id"]


def test_thin_task_seeds_running_status(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path, agent_md=_AGENT_MD)
    state = adapter._seed_state(RunTaskInput(agent="demo", input={}))

    assert state["status"] == "running"
    assert state["human_intent"]["current_stage"]["kind"] == "clarify"


def test_seed_carries_agent_safety_constraints_as_boundaries(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path, agent_md=_AGENT_MD)
    state = adapter._seed_state(
        RunTaskInput(agent="demo", input={"prompt": "research X"})
    )

    statements = [b["statement"] for b in state["human_intent"]["boundaries"]]
    assert "do not invent facts outside provided sources" in statements
