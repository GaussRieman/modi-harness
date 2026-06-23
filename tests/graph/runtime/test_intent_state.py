"""N2.3 — setup derives clarity + autonomy scope into state and trace.

The first transition makes the intent-aligned state visible: the run's trace
carries intent_initialized / intent_clarity_estimated / autonomy_scope_derived,
and the derived state survives checkpoint/resume.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
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


class _OneShotModel(BaseChatModel):
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="Final answer."))]
        )

    @property
    def _llm_type(self) -> str:
        return "oneshot"


_AGENT_MD = """---
name: demo
description: demo agent
tools: []
skills: []
---
You are a test agent.
"""


def _adapter(tmp_path: Path) -> HarnessGraphAdapter:
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "demo.md").write_text(_AGENT_MD)
    policy = PolicyGate(rule_packs=None)
    hooks = HookDispatcher(registry=HookRegistry([]), project_root=str(tmp_path), pass_env=[])
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
        model=ModelAdapter(chat_model=_OneShotModel()),
        tools=ToolGateway(
            registry=ToolRegistry(), policy=policy, hooks=hooks,
            result_inline_limit_bytes=8192,
        ),
        policy=policy,
        output=OutputController(),
        hooks=hooks,
    )
    return HarnessGraphAdapter(deps=deps, checkpointer=MemorySaver(), max_steps=4)


def _read_trace_types(adapter: HarnessGraphAdapter, thread_id: str) -> list[str]:
    return [e["event_type"] for e in adapter.read_trace(thread_id)]


def test_first_trace_includes_intent_clarity_scope_events(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    response = adapter.run(
        RunTaskInput(agent="demo", input={"prompt": "look into X"}, thread_id="t-trace")
    )
    types = _read_trace_types(adapter, response["thread_id"])

    assert "intent_initialized" in types
    assert "intent_clarity_estimated" in types
    assert "autonomy_scope_derived" in types


def test_setup_writes_clarity_and_scope_into_state(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    response = adapter.run(
        RunTaskInput(agent="demo", input={"prompt": "look into X"}, thread_id="t-state")
    )
    snapshot = adapter.graph.get_state(adapter._config("t-state"))
    values = snapshot.values

    assert values["intent_clarity"]["level"] in {"thin", "partial", "operational", "stable"}
    assert values["autonomy_scope"]["mode"] in {
        "guided", "bounded", "delegated", "constrained",
    }
    # State survived to the checkpoint, including the embedded clarity.
    assert (
        values["autonomy_scope"]["intent_clarity"]["level"]
        == values["intent_clarity"]["level"]
    )
    assert response["status"] == "completed"
