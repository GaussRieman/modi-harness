"""Unit tests for ModiSession agent-graph helpers (V0.5 N2.3a)."""

from __future__ import annotations

import pytest

from modi_harness import ModiAgent
from modi_harness.api._session_helpers import (
    agent_to_profile,
    dedupe_top_level,
    flatten_and_validate,
    merge_tool_registries,
)
from modi_harness.api.errors import AgentNameConflict
from modi_harness.tools.registry import ToolRegistry
from modi_harness.types import ToolBinding
from modi_harness.workflow import parse_workflow


def _agent(name: str, instruction: str = "i", **kw) -> ModiAgent:
    workflow = parse_workflow(
        {
            "id": "default",
            "input_schema": {"type": "object"},
            "start_node": "run",
            "nodes": [
                {
                    "id": "run",
                    "execution": "operation",
                    "operation": "run",
                    "transitions": {"completed": "$complete"},
                }
            ],
        }
    )
    return ModiAgent(
        name=name,
        description="d",
        instruction=instruction,
        workflows=(workflow,),
        **kw,
    )


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


def test_flatten_returns_top_level_agents() -> None:
    index = flatten_and_validate([_agent("one"), _agent("two")])
    assert sorted(index) == ["one", "two"]


def test_agent_to_profile_shape() -> None:
    def h(**_):
        return None

    a = _agent("x", tools=[ToolBinding(spec=_spec("t1"), handler=h)])
    p = agent_to_profile(a)
    assert p["name"] == "x"
    assert p["default_tools"] == ["t1"]
    assert p["tags"] == []
    assert isinstance(p["metadata"], dict)


def test_merge_registries_builtins_plus_agent_scoped() -> None:
    builtins = ToolRegistry()
    builtins.register_tool(_spec("kernel_a"), lambda **_: None)

    def h(**_):
        return None

    agent = _agent("owner", tools=[ToolBinding(spec=_spec("agent_t"), handler=h)])
    merged = merge_tool_registries(builtins, {"owner": agent})

    names = set(merged.names())
    assert "kernel_a" in names
    assert "agent_t" in names
    # both tools present in the merged registry
    assert merged.has("agent_t")
    assert merged.has("kernel_a")
    # first-writer-wins on duplicate names
    # (registering the same name twice keeps the first handler)
