"""Unit tests for ModiSession.from_discovery (V0.5 N3.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness import ModiAgent, ModiHarness, ModiSession
from modi_harness.discovery import discover_agents


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


def _harness() -> ModiHarness:
    return ModiHarness(chat_model=_ScriptModel())


def test_from_discovery_loads_directory(tmp_path: Path) -> None:
    d = tmp_path / "agents"
    d.mkdir()
    (d / "a.md").write_text("---\nname: a\ndescription: d\n---\nbody")
    (d / "b.md").write_text("---\nname: b\ndescription: d\n---\nbody")
    s = ModiSession.from_discovery(
        _harness(), checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
        agents_dir=d, plugins=[],
    )
    assert sorted(s.list_agents()) == ["a", "b"]


def test_from_discovery_merges_extra_and_plugins(tmp_path: Path) -> None:
    extra = ModiAgent(name="z", description="d", instruction="i")
    plugin_agent = ModiAgent(name="p", description="d", instruction="i")
    plugin_info = {"name": "fake", "agents": [plugin_agent], "kernel_tools": [], "source": "explicit"}
    s = ModiSession.from_discovery(
        _harness(), checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
        plugins=[plugin_info], extra_agents=[extra],
    )
    assert sorted(s.list_agents()) == ["p", "z"]


def test_from_discovery_conflict_raises(tmp_path: Path) -> None:
    from modi_harness.api.errors import AgentNameConflict
    a1 = ModiAgent(name="x", description="d", instruction="one")
    a2 = ModiAgent(name="x", description="d", instruction="two")
    plugin_info = {"name": "fake", "agents": [a2], "kernel_tools": [], "source": "explicit"}
    with pytest.raises(AgentNameConflict):
        ModiSession.from_discovery(
            _harness(), checkpointer=MemorySaver(),
            workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
            plugins=[plugin_info], extra_agents=[a1],
        )


def test_from_registry_resolves_one_runnable_agent(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "demo.md").write_text(
        "---\nname: demo\ndescription: d\n---\nbody", encoding="utf-8"
    )
    (tmp_path / "modi.toml").write_text(
        "[agents]\ninclude_plugins = false\ninclude_user = false\n",
        encoding="utf-8",
    )
    registry = discover_agents(cwd=tmp_path, plugins=[]).registry

    session = ModiSession.from_registry(
        _harness(),
        registry=registry,
        agent="project:demo",
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        project_root=tmp_path,
    )

    assert session.list_agents() == ["demo"]
