"""Unit tests for ModiSession construction + registry (V0.5 N2.3b)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness import ModiAgent, ModiHarness, ModiSession
from modi_harness.api.errors import AgentNameConflict, AgentNotRegistered, ModiSessionConfigError


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


def _agent(name: str = "demo", instruction: str = "reply") -> ModiAgent:
    return ModiAgent(name=name, description="d", instruction=instruction)


def _session(tmp_path: Path, agents: list[ModiAgent], **opts: Any) -> ModiSession:
    harness = ModiHarness(chat_model=_ScriptModel())
    return ModiSession(
        harness=harness,
        agents=agents,
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        **opts,
    )


def test_minimal_construction(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent()])
    assert s.list_agents() == ["demo"]


def test_empty_agents_raises(tmp_path: Path) -> None:
    harness = ModiHarness(chat_model=_ScriptModel())
    with pytest.raises(ModiSessionConfigError):
        ModiSession(
            harness=harness, agents=[], checkpointer=MemorySaver(),
            workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
        )


def test_name_conflict_raises_at_construction(tmp_path: Path) -> None:
    a = ModiAgent(name="x", description="d", instruction="one")
    b = ModiAgent(name="x", description="d", instruction="two")
    with pytest.raises(AgentNameConflict):
        _session(tmp_path, [a, b])


def test_equal_dupes_silently_dedupe(tmp_path: Path) -> None:
    a = ModiAgent(name="x", description="d", instruction="i")
    b = ModiAgent(name="x", description="d", instruction="i")
    s = _session(tmp_path, [a, b])
    assert s.list_agents() == ["x"]


def test_subagents_auto_register_top_level_listing(tmp_path: Path) -> None:
    leaf = ModiAgent(name="leaf", description="d", instruction="i")
    top = ModiAgent(name="top", description="d", instruction="i", subagents=[leaf])
    s = _session(tmp_path, [top])
    assert s.list_agents() == ["top"]
    assert sorted(s.list_all_agents()) == ["leaf", "top"]


def test_get_agent_returns_object(tmp_path: Path) -> None:
    a = _agent("demo")
    s = _session(tmp_path, [a])
    assert s.get_agent("demo").name == "demo"


def test_get_agent_unknown_raises(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent("demo")])
    with pytest.raises(AgentNotRegistered):
        s.get_agent("nope")


def test_one_harness_many_sessions(tmp_path: Path) -> None:
    harness = ModiHarness(chat_model=_ScriptModel())
    s1 = ModiSession(
        harness=harness, agents=[_agent("a")], checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws1", memory_root=tmp_path / "mem1",
    )
    s2 = ModiSession(
        harness=harness, agents=[_agent("b")], checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws2", memory_root=tmp_path / "mem2",
    )
    assert s1.list_agents() == ["a"]
    assert s2.list_agents() == ["b"]


def test_delegate_tool_only_for_nested_subagents(tmp_path: Path) -> None:
    leaf = ModiAgent(name="leaf", description="d", instruction="i")
    top = ModiAgent(name="top", description="d", instruction="i", subagents=[leaf])
    s = _session(tmp_path, [top])
    # internal check: the merged registry should have delegate_to_leaf, not delegate_to_top
    reg = s._tool_gateway._registry
    assert reg.has("delegate_to_leaf")
    assert not reg.has("delegate_to_top")
