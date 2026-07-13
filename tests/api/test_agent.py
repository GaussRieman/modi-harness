"""Unit tests for the mandatory-Workflow ModiAgent."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from modi_harness import ModiAgent
from modi_harness.types import InteractionProtocolConfig, TaskProtocolConfig, ToolBinding
from modi_harness.workflow import parse_workflow


def _workflow(workflow_id: str = "default"):
    return parse_workflow(
        {
            "id": workflow_id,
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


def _agent(name: str = "x", **changes) -> ModiAgent:
    values = {
        "name": name,
        "description": "description",
        "instruction": "instruction",
        "workflows": (_workflow(),),
    }
    values.update(changes)
    return ModiAgent(**values)


def _spec(name: str) -> dict:
    return {"name": name, "description": "d", "input_schema": {}, "risk_level": "L0"}


def test_minimal_construction_requires_workflow() -> None:
    agent = _agent()
    assert agent.tools == ()
    assert agent.task_protocol == TaskProtocolConfig()
    assert agent.interaction_protocol == InteractionProtocolConfig()

    with pytest.raises(ValueError, match="at least one Workflow"):
        ModiAgent(name="x", description="d", instruction="i", workflows=())


def test_agent_is_frozen_and_normalizes_collections() -> None:
    child = _agent("child")
    agent = _agent(
        tools=[],
        subagents=[child],
        safety_constraints=["safe"],
        metadata={"k": 1},
    )
    assert isinstance(agent.subagents, tuple)
    assert isinstance(agent.metadata, MappingProxyType)
    with pytest.raises(FrozenInstanceError):
        agent.name = "changed"  # type: ignore[misc]


def test_duplicate_workflow_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        _agent(workflows=(_workflow(), _workflow()))


def test_tool_tuple_is_normalized_to_binding() -> None:
    def handler(**_):
        return None

    agent = _agent(tools=[(_spec("tool"), handler)])
    assert isinstance(agent.tools[0], ToolBinding)


def _write_package(root: Path, name: str) -> Path:
    package = root / name
    (package / "workflows").mkdir(parents=True)
    (package / "agent.toml").write_text(
        f'name = "{name}"\ndescription = "d"\ninstruction = "i"\n',
        encoding="utf-8",
    )
    (package / "workflows" / "default.yaml").write_text(
        """id: default
input_schema: {type: object}
start_node: run
nodes:
  - id: run
    execution: operation
    operation: run
    transitions: {completed: $complete}
""",
        encoding="utf-8",
    )
    return package


def test_from_package_loads_canonical_declaration(tmp_path: Path) -> None:
    package = _write_package(tmp_path / "agents", "demo")
    agent = ModiAgent.from_package(package)

    assert agent.name == "demo"
    assert [workflow.id for workflow in agent.workflows] == ["default"]


def test_load_dir_loads_only_canonical_packages(tmp_path: Path) -> None:
    root = tmp_path / "agents"
    _write_package(root, "a")
    _write_package(root, "b")
    (root / "old.md").write_text("obsolete", encoding="utf-8")

    assert [agent.name for agent in ModiAgent.load_dir(root)] == ["a", "b"]
