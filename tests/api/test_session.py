"""Unit tests for ModiSession construction + registry (V0.5 N2.3b)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

from modi_harness import ModiAgent, ModiHarness, ModiSession
from modi_harness.actions import ActionGateway
from modi_harness.api.errors import AgentNameConflict, AgentNotRegistered, ModiSessionConfigError
from modi_harness.api.session import _derive_workspace_key


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(self.script[i]))])

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


def test_session_wires_action_gateway_as_runtime_center(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent()])

    assert isinstance(s._tool_gateway, ActionGateway)
    assert s._adapter._deps.tools is s._tool_gateway


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


def test_workspace_key_uses_readable_workspace_root_name(tmp_path: Path) -> None:
    key = _derive_workspace_key(
        tmp_path / ".modi" / "workspace" / "research_assistant",
        tmp_path,
    )
    assert key == "research_assistant"


def test_workspace_key_falls_back_for_generic_run_store_root(tmp_path: Path) -> None:
    key = _derive_workspace_key(tmp_path / ".modi" / "workspace", tmp_path)
    assert key != "workspace"
    assert len(key) == 16


def test_delegate_tool_only_for_nested_subagents(tmp_path: Path) -> None:
    leaf = ModiAgent(name="leaf", description="d", instruction="i")
    top = ModiAgent(name="top", description="d", instruction="i", subagents=[leaf])
    s = _session(tmp_path, [top])
    # internal check: the merged registry should have delegate_to_leaf, not delegate_to_top
    reg = s._tool_gateway._registry
    assert reg.has("delegate_to_leaf")
    assert not reg.has("delegate_to_top")


def test_run_task_completes(tmp_path: Path) -> None:
    from langchain_core.messages import AIMessage
    harness = ModiHarness(chat_model=_ScriptModel(script=[AIMessage(content="ok")]))
    s = ModiSession(
        harness=harness, agents=[_agent("demo")], checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
    )
    resp = s.run_task(agent="demo", input={"goal": "hi"})
    assert resp["status"] == "completed"
    assert resp["thread_id"]


def test_run_task_materializes_inputs_and_injects_refs(tmp_path: Path) -> None:
    from langchain_core.messages import AIMessage

    harness = ModiHarness(chat_model=_ScriptModel(script=[AIMessage(content="ok")]))
    s = ModiSession(
        harness=harness, agents=[_agent("demo")], checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
    )

    resp = s.run_task(
        agent="demo",
        input={"goal": "read input"},
        inputs=[{
            "name": "task.json",
            "data": {"hello": 1},
            "metadata": {"source": "test"},
        }],
        thread_id="input-thread",
    )

    assert resp["status"] == "completed"
    state = s.get_state("input-thread")
    assert state is not None
    refs = state["task"]["input_refs"]
    assert refs[0]["kind"] == "input"
    input_path = Path(refs[0]["path"])
    assert input_path.read_text() == '{"hello": 1}'
    run_dir = tmp_path / "ws" / resp["run_id"]
    assert (run_dir / "input" / "task.json").exists()
    assert not (run_dir / "artifacts").exists()
    assert not (run_dir / "references").exists()
    assert not (run_dir / "state").exists()


def test_run_task_rejects_unregistered(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent("demo")])
    with pytest.raises(AgentNotRegistered):
        s.run_task(agent="nope", input={})


def test_run_task_rejects_subagent_only(tmp_path: Path) -> None:
    leaf = ModiAgent(name="leaf", description="d", instruction="i")
    top = ModiAgent(name="top", description="d", instruction="i", subagents=[leaf])
    s = _session(tmp_path, [top])
    with pytest.raises(AgentNotRegistered):
        s.run_task(agent="leaf", input={})


def test_run_task_touches_thread(tmp_path: Path) -> None:
    from langchain_core.messages import AIMessage
    harness = ModiHarness(chat_model=_ScriptModel(script=[AIMessage(content="ok")]))
    s = ModiSession(
        harness=harness, agents=[_agent("demo")], checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
    )
    resp = s.run_task(agent="demo", input={"goal": "hi"})
    tid = resp["thread_id"]
    assert tid in s._threads
    assert s._threads[tid]["run_count"] == 1


def test_introspection_after_run(tmp_path: Path) -> None:
    from langchain_core.messages import AIMessage
    harness = ModiHarness(chat_model=_ScriptModel(script=[AIMessage(content="ok")]))
    s = ModiSession(
        harness=harness, agents=[_agent("demo")], checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
    )
    resp = s.run_task(agent="demo", input={"goal": "hi"})
    tid = resp["thread_id"]
    state = s.get_state(tid)
    assert state is not None
    assert s.get_denials(tid) == []
    assert isinstance(s.get_artifacts(tid), list)


def test_get_state_unknown_thread_returns_none(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent("demo")])
    assert s.get_state("nonexistent") is None
    assert s.get_artifacts("nonexistent") == []
    assert s.get_denials("nonexistent") == []


def test_memory_roundtrip(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent("demo")])
    rec = s.add_memory({
        "id": "m1", "scope": "agent", "type": "reference",
        "name": "n", "description": "d", "body": "hello", "tags": ["t1"],
    })
    assert rec["id"] == "m1"
    assert (tmp_path / "mem" / "agent" / "demo" / "m1.md").exists()
    assert not (tmp_path / "mem" / "agent" / "m1.md").exists()
    found = s.list_memory(scopes=["agent"])
    assert any(r["id"] == "m1" for r in found)
    s.forget_memory("m1")
    assert all(r["id"] != "m1" for r in s.list_memory(scopes=["agent"]))


def test_list_hooks_empty_by_default(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent("demo")])
    assert s.list_hooks() == []


def test_threads_index_and_end(tmp_path: Path) -> None:
    from langchain_core.messages import AIMessage
    harness = ModiHarness(chat_model=_ScriptModel(script=[AIMessage(content="ok")]))
    s = ModiSession(
        harness=harness, agents=[_agent("demo")], checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws", memory_root=tmp_path / "mem",
    )
    resp = s.run_task(agent="demo", input={"goal": "hi"})
    tid = resp["thread_id"]
    assert len(s.list_threads()) == 1
    s.end_thread(tid)
    assert s._threads[tid]["status"] == "closed"


def test_close_is_noop(tmp_path: Path) -> None:
    s = _session(tmp_path, [_agent("demo")])
    assert s.close() is None


def test_agent_scoped_tool_not_in_other_agents_profile(tmp_path: Path) -> None:
    """Agent-scoping works via default_tools: agent B's tool must not appear in
    agent A's projected profile, so A cannot call it."""
    from modi_harness.api._session_helpers import agent_to_profile

    def h(**_): return None
    from modi_harness.types import ToolBinding

    spec = {"name": "b_tool", "description": "d", "input_schema": {}, "risk_level": "L0"}
    agent_a = ModiAgent(name="a", description="d", instruction="i")
    agent_b = ModiAgent(
        name="b", description="d", instruction="i",
        tools=[ToolBinding(spec=spec, handler=h)],
    )
    s = _session(tmp_path, [agent_a, agent_b])

    # Agent A's profile has no tools; Agent B's profile lists b_tool.
    prof_a = agent_to_profile(s.get_agent("a"))
    prof_b = agent_to_profile(s.get_agent("b"))
    assert prof_a["default_tools"] == []
    assert "b_tool" in prof_b["default_tools"]


def test_model_override_projects_into_profile(tmp_path: Path) -> None:
    from modi_harness.api._session_helpers import agent_to_profile
    from modi_harness.types import ModelSpec

    agent = ModiAgent(
        name="m", description="d", instruction="i",
        model_override=ModelSpec(provider="anthropic", name="claude-x", base_url="http://x"),
    )
    s = _session(tmp_path, [agent])
    prof = agent_to_profile(s.get_agent("m"))
    assert prof["metadata"]["model"]["provider"] == "anthropic"
    assert prof["metadata"]["model"]["name"] == "claude-x"
    assert prof["metadata"]["model"]["base_url"] == "http://x"


def test_skills_wired_into_session_deps(tmp_path: Path) -> None:
    from modi_harness.types import Skill

    loaded = {
        "name": "greet", "description": "d", "instruction": "say hi",
        "allowed_tools": None, "risk_notes": [], "references": [],
        "scripts": [], "templates": [], "examples": [], "tags": [], "metadata": {},
    }
    agent = ModiAgent(
        name="sk", description="d", instruction="i",
        skills=[Skill(name="greet", profile=loaded)],
    )
    s = _session(tmp_path, [agent])
    loader = s._adapter._deps.skills
    assert loader is not None
    resolved = loader.load_skills(["greet"])
    assert len(resolved) == 1
    assert resolved[0]["name"] == "greet"


def test_no_skills_leaves_deps_skills_none(tmp_path: Path) -> None:
    agent = ModiAgent(name="plain", description="d", instruction="i")
    s = _session(tmp_path, [agent])
    assert s._adapter._deps.skills is None


def test_index_backed_skill_loader_direct() -> None:
    from modi_harness.api._session_helpers import index_backed_skill_loader
    from modi_harness.types import Skill

    loaded = {"name": "greet", "description": "d", "instruction": "hi",
              "allowed_tools": None, "risk_notes": [], "references": [],
              "scripts": [], "templates": [], "examples": [], "tags": [], "metadata": {}}
    agent = ModiAgent(name="sk", description="d", instruction="i",
                      skills=[Skill(name="greet", profile=loaded)])
    loader = index_backed_skill_loader({"sk": agent})
    assert loader.load_skills(["greet"])[0]["name"] == "greet"
    assert index_backed_skill_loader(
        {"x": ModiAgent(name="x", description="d", instruction="i")}
    ) is None
