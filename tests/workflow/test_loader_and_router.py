"""Mandatory Agent package loading and deterministic Workflow routing tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from modi_harness.agents import AgentLoader
from modi_harness.agents.errors import AgentFrontmatterError, AgentNotFoundError
from modi_harness.api._session_helpers import agent_to_profile
from modi_harness.api.agent import ModiAgent
from modi_harness.workflow import WorkflowRoutingError, parse_workflow, select_workflow


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _agent_package(tmp_path: Path) -> Path:
    package = tmp_path / "agents" / "complaints"
    _write(
        package / "agent.toml",
        """name = "complaints"
description = "Resolve complaints."
instruction = "Resolve the complaint."
tools = ["get_order"]
""",
    )
    _write(package / "workflows" / "workflow.yaml", _workflow_yaml())
    return package


def _workflow_yaml(
    *,
    workflow_id: str = "complaint_resolution",
    operation: str = "classify_complaint",
) -> str:
    return f"""id: {workflow_id}
input_schema:
  type: object
start_node: classify
nodes:
  - id: classify
    execution: operation
    operation: {operation}
    transitions:
      completed: $complete
"""


def test_canonical_package_loads_agent_and_workflows(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    _write(package / "workflows" / "b.yaml", _workflow_yaml(workflow_id="second"))

    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent("complaints")

    assert profile["instruction"] == "Resolve the complaint."
    assert [workflow.id for workflow in profile["workflows"]] == [
        "second",
        "complaint_resolution",
    ]
    assert profile["metadata"]["package"]["files"]["workflows"] == [
        "workflows/b.yaml",
        "workflows/workflow.yaml",
    ]


def test_package_requires_nonempty_workflows(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    (package / "workflows" / "workflow.yaml").unlink()

    with pytest.raises(AgentFrontmatterError, match="at least one Workflow"):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("complaints")


@pytest.mark.parametrize(
    "filename",
    ["agent.md", "brain.toml", "brain.md", "rules.toml", "stages.toml"],
)
def test_package_rejects_obsolete_reserved_files(tmp_path: Path, filename: str) -> None:
    package = _agent_package(tmp_path)
    _write(package / filename, "obsolete")

    with pytest.raises(AgentFrontmatterError, match=r"obsolete control file"):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("complaints")


def test_markdown_agent_is_not_discovered(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "plain.md",
        "---\nname: plain\ndescription: Plain.\n---\nBody.",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")

    assert loader.list_agent_names() == []
    with pytest.raises(AgentNotFoundError):
        loader.load_agent("plain")


def test_instruction_is_required_inline(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    _write(
        package / "agent.toml",
        'name = "complaints"\ndescription = "Resolve complaints."\ninstruction_file = "agent.txt"\n',
    )
    _write(package / "agent.txt", "old fallback")

    with pytest.raises(AgentFrontmatterError, match="instruction_file"):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("complaints")


def test_loader_rejects_duplicate_workflow_ids(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    _write(package / "workflows" / "duplicate.yaml", _workflow_yaml())

    with pytest.raises(AgentFrontmatterError, match="duplicate Workflow id"):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("complaints")


def test_loader_rejects_node_capability_widening(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    _write(
        package / "workflows" / "workflow.yaml",
        """id: investigate
input_schema: {type: object}
start_node: investigate
nodes:
  - id: investigate
    execution: autonomous
    goal: Investigate.
    completion:
      output_schema: {type: object}
      validator: validate_investigation
    capabilities:
      tools: [search_messages]
    transitions:
      completed: $complete
""",
    )

    with pytest.raises(AgentFrontmatterError, match="widens Agent capabilities"):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("complaints")


def test_workflow_edit_add_and_delete_invalidate_loader_cache(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    first_file = package / "workflows" / "workflow.yaml"
    _write(first_file, _workflow_yaml(operation="first_operation"))
    loader = AgentLoader(project_dir=tmp_path / "agents")

    first = loader.load_agent("complaints")
    assert first["workflows"][0].node("classify").operation == "first_operation"

    _write(first_file, _workflow_yaml(operation="second_operation"))
    os.utime(first_file, ns=(first_file.stat().st_atime_ns, first_file.stat().st_mtime_ns + 1))
    second = loader.load_agent("complaints")
    assert second["workflows"][0].node("classify").operation == "second_operation"

    second_file = package / "workflows" / "second.yaml"
    _write(second_file, _workflow_yaml(workflow_id="second"))
    added = loader.load_agent("complaints")
    assert {workflow.id for workflow in added["workflows"]} == {
        "complaint_resolution",
        "second",
    }

    second_file.unlink()
    removed = loader.load_agent("complaints")
    assert [workflow.id for workflow in removed["workflows"]] == ["complaint_resolution"]


def test_modi_agent_package_and_profile_require_workflow(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    agent = ModiAgent.from_package(package)
    profile = agent_to_profile(agent)

    assert [workflow.id for workflow in agent.workflows] == ["complaint_resolution"]
    assert [workflow.id for workflow in profile["workflows"]] == ["complaint_resolution"]

    with pytest.raises(ValueError, match="at least one Workflow"):
        ModiAgent(
            name="invalid",
            description="invalid",
            instruction="invalid",
            workflows=(),
        )


def _parsed_workflow(workflow_id: str):
    return parse_workflow(
        {
            "id": workflow_id,
            "input_schema": {"type": "object"},
            "start_node": "start",
            "nodes": [
                {
                    "id": "start",
                    "execution": "operation",
                    "operation": "start",
                    "transitions": {"completed": "$complete"},
                }
            ],
        }
    )


def test_router_requires_workflow_and_defaults_sole_workflow() -> None:
    with pytest.raises(WorkflowRoutingError) as captured:
        select_workflow([])
    assert captured.value.code == "workflow_required"

    only = _parsed_workflow("only")
    assert select_workflow([only]) is only
    assert select_workflow([only], "only") is only


def test_router_requires_explicit_id_for_multiple_workflows() -> None:
    first = _parsed_workflow("first")
    second = _parsed_workflow("second")

    with pytest.raises(WorkflowRoutingError) as captured:
        select_workflow([second, first])

    assert captured.value.code == "workflow_required"
    assert captured.value.available_workflow_ids == ("first", "second")
    assert select_workflow([first, second], "second") is second


def test_router_reports_unknown_and_invalid_explicit_ids() -> None:
    workflow = _parsed_workflow("known")
    with pytest.raises(WorkflowRoutingError) as missing:
        select_workflow([workflow], "missing")
    assert missing.value.code == "workflow_not_found"
    assert missing.value.available_workflow_ids == ("known",)

    with pytest.raises(WorkflowRoutingError) as blank:
        select_workflow([workflow], " ")
    assert blank.value.code == "workflow_required"
