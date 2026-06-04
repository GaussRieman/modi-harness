"""Unit tests for ModiSession agent-graph helpers (V0.5 N2.3a)."""

from __future__ import annotations

import pytest

from modi_harness import ModiAgent
from modi_harness.api._session_helpers import (
    agent_to_profile,
    dedupe_top_level,
    flatten_and_validate,
    index_backed_loader,
    merge_tool_registries,
)
from modi_harness.api.errors import AgentNameConflict
from modi_harness.tools.registry import ToolRegistry
from modi_harness.types import ToolBinding


def _agent(name: str, instruction: str = "i", **kw) -> ModiAgent:
    return ModiAgent(name=name, description="d", instruction=instruction, **kw)


def _spec(name: str) -> dict:
    return {"name": name, "description": "d", "input_schema": {}, "risk_level": "L0"}


def test_dedupe_equal_top_level() -> None:
    a = _agent("x")
    b = _agent("x")
    assert [x.name for x in dedupe_top_level([a, b])] == ["x"]


def test_dedupe_conflict_raises() -> None:
    a = _agent("x", instruction="one")
    b = _agent("x", instruction="two")
    with pytest.raises(AgentNameConflict):
        dedupe_top_level([a, b])


def test_flatten_includes_nested() -> None:
    leaf = _agent("leaf")
    top = _agent("top", subagents=[leaf])
    index = flatten_and_validate([top])
    assert sorted(index.keys()) == ["leaf", "top"]


def test_flatten_nested_conflict_raises() -> None:
    leaf_a = _agent("leaf", instruction="a")
    leaf_b = _agent("leaf", instruction="b")
    t1 = _agent("t1", subagents=[leaf_a])
    t2 = _agent("t2", subagents=[leaf_b])
    with pytest.raises(AgentNameConflict):
        flatten_and_validate([t1, t2])


def test_agent_to_profile_shape() -> None:
    def h(**_): return None
    a = _agent("x", tools=[ToolBinding(spec=_spec("t1"), handler=h)])
    p = agent_to_profile(a)
    assert p["name"] == "x"
    assert p["default_tools"] == ["t1"]
    assert p["tags"] == []
    assert isinstance(p["metadata"], dict)


def test_index_backed_loader_serves_profiles() -> None:
    a = _agent("x")
    loader = index_backed_loader({"x": a})
    profile = loader.load_agent("x")
    assert profile["name"] == "x"
    assert loader.list_agent_names() == ["x"]


def test_index_backed_loader_unknown_raises() -> None:
    from modi_harness.agents.errors import AgentNotFoundError
    loader = index_backed_loader({})
    with pytest.raises(AgentNotFoundError):
        loader.load_agent("nope")


def test_merge_registries_builtins_plus_agent_scoped() -> None:
    builtins = ToolRegistry()
    builtins.register_tool(_spec("kernel_a"), lambda **_: None)

    def h(**_): return None
    agent = _agent("owner", tools=[ToolBinding(spec=_spec("agent_t"), handler=h)])
    merged = merge_tool_registries(builtins, {"owner": agent})

    names = set(merged.names())
    assert "kernel_a" in names
    assert "agent_t" in names
    # agent-scoped tool restricted to its owner
    assert merged.get("agent_t")["allowed_agents"] == ["owner"]
    # builtin unrestricted
    assert merged.get("kernel_a")["allowed_agents"] == []
