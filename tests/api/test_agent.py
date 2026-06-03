"""Unit tests for ModiAgent (V0.5)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from modi_harness import ModiAgent
from modi_harness.types import ToolBinding


def _spec(name: str) -> dict:
    return {"name": name, "description": "d", "input_schema": {}, "risk_level": "L0"}


def test_minimal_construction() -> None:
    a = ModiAgent(name="x", description="d", instruction="hi")
    assert a.name == "x"
    assert a.tools == ()
    assert a.subagents == ()
    assert a.metadata == {}


def test_is_frozen() -> None:
    a = ModiAgent(name="x", description="d", instruction="hi")
    with pytest.raises(FrozenInstanceError):
        a.name = "y"  # type: ignore[misc]


def test_constructor_accepts_list_and_normalizes_to_tuple() -> None:
    def h(**_): return None
    tb = ToolBinding(spec=_spec("t1"), handler=h)
    a = ModiAgent(
        name="x", description="d", instruction="hi",
        tools=[tb],
        subagents=[ModiAgent(name="child", description="d", instruction="i")],
        safety_constraints=["no-x"],
    )
    assert isinstance(a.tools, tuple)
    assert isinstance(a.subagents, tuple)
    assert isinstance(a.safety_constraints, tuple)


def test_constructor_accepts_legacy_tuple_tool_form() -> None:
    def h(**_): return None
    a = ModiAgent(
        name="x", description="d", instruction="hi",
        tools=[(_spec("t1"), h)],
    )
    assert isinstance(a.tools[0], ToolBinding)
    assert a.tools[0].spec["name"] == "t1"


def test_value_equality_across_distinct_instances() -> None:
    def h(**_): return None
    spec = _spec("t1")
    a = ModiAgent(
        name="x", description="d", instruction="hi",
        tools=[ToolBinding(spec=spec, handler=h)],
    )
    b = ModiAgent(
        name="x", description="d", instruction="hi",
        tools=[ToolBinding(spec=spec, handler=h)],
    )
    assert a == b


def test_metadata_normalized_to_mapping_proxy() -> None:
    from types import MappingProxyType
    a = ModiAgent(name="x", description="d", instruction="hi", metadata={"k": 1})
    assert isinstance(a.metadata, MappingProxyType)
    assert a.metadata["k"] == 1


def test_recursive_subagents() -> None:
    leaf = ModiAgent(name="leaf", description="d", instruction="i")
    mid = ModiAgent(name="mid", description="d", instruction="i", subagents=[leaf])
    top = ModiAgent(name="top", description="d", instruction="i", subagents=[mid])
    assert top.subagents[0].subagents[0].name == "leaf"
