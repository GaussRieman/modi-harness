"""Unit tests for ModiAgent (V0.5)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from modi_harness import ModiAgent
from modi_harness.types import InteractionProtocolConfig, TaskProtocolConfig, ToolBinding


def _spec(name: str) -> dict:
    return {"name": name, "description": "d", "input_schema": {}, "risk_level": "L0"}


def test_minimal_construction() -> None:
    a = ModiAgent(name="x", description="d", instruction="hi")
    assert a.name == "x"
    assert a.tools == ()
    assert a.subagents == ()
    assert a.metadata == {}
    assert a.task_protocol == TaskProtocolConfig()
    assert a.interaction_protocol == InteractionProtocolConfig()


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


def _write_agent(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_from_markdown_minimal(tmp_path: Path) -> None:
    p = tmp_path / "agents" / "demo.md"
    _write_agent(p, "---\nname: demo\ndescription: d\n---\nbody")
    a = ModiAgent.from_markdown(p)
    assert a.name == "demo"
    assert a.description == "d"
    assert a.instruction == "body"
    assert a.tools == ()


def test_from_markdown_attaches_extra_tools(tmp_path: Path) -> None:
    p = tmp_path / "agents" / "demo.md"
    _write_agent(p, "---\nname: demo\ndescription: d\n---\nbody")

    def h(**_): return None
    a = ModiAgent.from_markdown(
        p, tools=[ToolBinding(spec=_spec("t1"), handler=h)]
    )
    assert len(a.tools) == 1
    assert a.tools[0].spec["name"] == "t1"


def test_load_dir_returns_all_agents(tmp_path: Path) -> None:
    d = tmp_path / "agents"
    _write_agent(d / "a.md", "---\nname: a\ndescription: d\n---\nbody")
    _write_agent(d / "b.md", "---\nname: b\ndescription: d\n---\nbody")

    agents = ModiAgent.load_dir(d)
    names = sorted(x.name for x in agents)
    assert names == ["a", "b"]


def test_from_markdown_round_trips_derived_fields(tmp_path: Path) -> None:
    """from_markdown must preserve output_contract / permission_profile / metadata
    that AgentLoader._build_profile derives from the frontmatter."""
    p = tmp_path / "agents" / "structured.md"
    _write_agent(
        p,
        """---
name: structured
description: d
output_contract:
  required_fields:
    - summary
permission_profile:
  mode: auto
  preauthorized:
    - search
tags:
  - alpha
---
body""",
    )
    a = ModiAgent.from_markdown(p)

    # output_contract: required_fields preserved + free_form False (because contract is declared)
    assert a.output_contract is not None
    assert a.output_contract["required_fields"] == ["summary"]
    assert a.output_contract["free_form"] is False

    # permission_profile preserved
    assert a.permission_profile is not None
    assert a.permission_profile["mode"] == "auto"
    assert a.permission_profile["preauthorized"] == ["search"]

    # metadata default memory_level still present
    assert a.metadata.get("memory_level") == "moderate"


def test_from_markdown_parses_task_protocol(tmp_path: Path) -> None:
    p = tmp_path / "agents" / "planner.md"
    _write_agent(
        p,
        """---
name: planner
description: d
task_protocol:
  mode: required
  review: before_execution
  min_items: 2
  max_items: 6
---
Plan first.
""",
    )

    agent = ModiAgent.from_markdown(p)

    assert agent.task_protocol == TaskProtocolConfig(
        mode="required",
        review="before_execution",
        min_items=2,
        max_items=6,
    )
    assert "task_protocol" not in agent.metadata


def test_from_markdown_rejects_invalid_task_protocol(tmp_path: Path) -> None:
    from modi_harness.agents.errors import AgentFrontmatterError

    p = tmp_path / "agents" / "bad.md"
    _write_agent(
        p,
        """---
name: bad
description: d
task_protocol:
  mode: required
  review: eventually
---
Bad.
""",
    )

    with pytest.raises(AgentFrontmatterError, match=r"task_protocol\.review"):
        ModiAgent.from_markdown(p)


def test_from_markdown_parses_interaction_protocol(tmp_path: Path) -> None:
    p = tmp_path / "agents" / "interactive.md"
    _write_agent(
        p,
        """---
name: interactive
description: d
interaction_protocol:
  startup: agent
---
Ask for input.
""",
    )

    agent = ModiAgent.from_markdown(p)

    assert agent.interaction_protocol == InteractionProtocolConfig(startup="agent")
    assert "interaction_protocol" not in agent.metadata


def test_from_markdown_rejects_invalid_interaction_protocol(tmp_path: Path) -> None:
    from modi_harness.agents.errors import AgentFrontmatterError

    p = tmp_path / "agents" / "bad-interaction.md"
    _write_agent(
        p,
        """---
name: bad-interaction
description: d
interaction_protocol:
  startup: magic
---
Bad.
""",
    )

    with pytest.raises(AgentFrontmatterError, match=r"interaction_protocol\.startup"):
        ModiAgent.from_markdown(p)
