"""Mandatory Agent package loading and deterministic Workflow routing tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from modi_harness.agents import AgentLoader
from modi_harness.agents.errors import AgentFrontmatterError, AgentNotFoundError
from modi_harness.api._session_helpers import agent_to_profile
from modi_harness.api.agent import ModiAgent
from modi_harness.workflow import (
    WorkflowRoutingError,
    parse_workflow,
    route_workflow,
    select_workflow,
    workflow_to_dict,
)


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
description: Resolve one complaint.
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


def test_loader_rejects_invalid_child_template_limits(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    _write(
        package / "agent.toml",
        '''name = "complaints"
description = "Resolve complaints."
instruction = "Resolve the complaint."

[[child_templates]]
id = "worker"
agent_name = "worker-agent"
workflow_id = "execute"

[child_templates.limits]
max_steps = 0
timeout_seconds = 900
''',
    )

    with pytest.raises(AgentFrontmatterError, match="max_steps must be a positive integer"):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("complaints")


def test_loader_rejects_node_capability_widening(tmp_path: Path) -> None:
    package = _agent_package(tmp_path)
    _write(
        package / "workflows" / "workflow.yaml",
        """id: investigate
description: Investigate a complaint.
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
            "description": f"Run {workflow_id}.",
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


class _RouteModel:
    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = 0
        self.pack: dict[str, object] | None = None

    def call(self, pack: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.pack = pack
        return {
            "tool_calls": [
                {
                    "tool_name": self.tool_name,
                    "arguments": self.arguments,
                }
            ]
        }


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


def test_model_router_selects_one_workflow_and_builds_valid_input() -> None:
    quick = parse_workflow(
        {
            **workflow_to_dict(_parsed_workflow("quick_lookup")),
            "description": "Use for a narrow lookup.",
            "input_schema": {
                "type": "object",
                "required": ["subject"],
                "properties": {"subject": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        }
    )
    deep = parse_workflow(
        {
            **workflow_to_dict(_parsed_workflow("deep_research")),
            "description": "Use for broad analysis.",
        }
    )
    model = _RouteModel("route__quick_lookup", {"subject": "中控技术"})

    route = route_workflow(
        [deep, quick],
        {"prompt": "中控技术"},
        workflow_id=None,
        model=model,
        agent_instruction="Research public information.",
    )

    assert route.workflow is quick
    assert route.workflow_input == {"subject": "中控技术"}
    assert route.strategy == "model"
    assert model.calls == 1
    assert model.pack is not None
    tools = model.pack["tool_descriptions"]
    assert isinstance(tools, list)
    assert [item["name"] for item in tools] == [
        "route__deep_research",
        "route__quick_lookup",
    ]
    assert tools[1]["description"] == "Use for a narrow lookup."
    assert isinstance(tools[1]["input_schema"]["properties"], dict)
    router_request = model.pack["recent_messages"][0]["content"]
    assert "must not be downgraded to a narrow lookup" in router_request


def test_model_router_rejects_invalid_routed_input() -> None:
    first = parse_workflow(
        {
            **workflow_to_dict(_parsed_workflow("first")),
            "input_schema": {
                "type": "object",
                "required": ["subject"],
                "properties": {"subject": {"type": "string", "minLength": 1}},
            },
        }
    )
    model = _RouteModel("route__first", {})

    with pytest.raises(WorkflowRoutingError) as captured:
        route_workflow(
            [first, _parsed_workflow("second")],
            {"prompt": "hello"},
            workflow_id=None,
            model=model,
            agent_instruction="",
        )

    assert captured.value.code == "workflow_route_input_invalid"


def test_router_does_not_downgrade_explicit_deep_search_to_quick_lookup() -> None:
    quick = parse_workflow(
        {
            **workflow_to_dict(_parsed_workflow("quick_lookup")),
            "description": "Use for a narrow lookup.",
            "input_schema": {
                "type": "object",
                "required": ["subject", "question"],
                "properties": {
                    "subject": {"type": "string", "minLength": 1},
                    "question": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        }
    )
    deep = parse_workflow(
        {
            **workflow_to_dict(_parsed_workflow("deep_research")),
            "description": "Use for careful, multi-search research.",
            "input_schema": {
                "type": "object",
                "required": ["request"],
                "properties": {
                    "request": {"type": "string", "minLength": 1},
                    "subject": {"type": "string"},
                    "question": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    )
    model = _RouteModel(
        "route__quick_lookup",
        {
            "subject": "拉格朗日具身智能公司",
            "question": "拉格朗日具身智能公司的基本公开信息",
        },
    )

    route = route_workflow(
        [quick, deep],
        {"prompt": "仔细搜寻一下拉格朗日具身只能公司"},
        workflow_id=None,
        model=model,
        agent_instruction="Research public information.",
    )

    assert route.workflow is deep
    assert route.workflow_input == {
        "request": "拉格朗日具身智能公司的基本公开信息",
        "subject": "拉格朗日具身智能公司",
        "question": "拉格朗日具身智能公司的基本公开信息",
    }


def test_explicit_and_sole_routes_do_not_call_model() -> None:
    first = _parsed_workflow("first")
    second = _parsed_workflow("second")
    model = _RouteModel("route__second", {})

    explicit = route_workflow(
        [first, second],
        {"value": 1},
        workflow_id="second",
        model=model,
        agent_instruction="",
    )
    sole = route_workflow(
        [first],
        {"value": 2},
        workflow_id=None,
        model=model,
        agent_instruction="",
    )

    assert explicit.strategy == "explicit"
    assert explicit.workflow_input == {"value": 1}
    assert sole.strategy == "sole"
    assert sole.workflow_input == {"value": 2}
    assert model.calls == 0


def test_workflow_description_is_serialized_and_fingerprinted() -> None:
    base = workflow_to_dict(_parsed_workflow("lookup"))
    first = parse_workflow({**base, "description": "First route description."})
    second = parse_workflow({**base, "description": "Second route description."})

    assert workflow_to_dict(first)["description"] == "First route description."
    assert first.definition_fingerprint != second.definition_fingerprint
