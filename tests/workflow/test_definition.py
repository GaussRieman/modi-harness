"""Tests for the closed Workflow definition kernel."""

from __future__ import annotations

from copy import deepcopy

import pytest

from modi_harness.workflow import (
    MAX_INSTANCE_DEPTH,
    SchemaDefinition,
    SchemaRegistry,
    WorkflowDefinitionError,
    WorkflowInstanceError,
    parse_workflow,
    validate_instance,
    workflow_to_dict,
)


def _operation_node(
    node_id: str = "classify",
    *,
    target: str = "$complete",
) -> dict:
    return {
        "id": node_id,
        "execution": "operation",
        "operation": f"op_{node_id}",
        "transitions": {"completed": target},
    }


def _workflow(*nodes: dict, start_node: str | None = None) -> dict:
    selected = list(nodes) or [_operation_node()]
    return {
        "id": "complaints",
        "description": "Resolve complaints.",
        "input_schema": {
            "type": "object",
            "properties": {"complaint": {"type": "string"}},
        },
        "start_node": start_node or selected[0]["id"],
        "nodes": selected,
    }


def _autonomous_node(target: str = "$complete") -> dict:
    return {
        "id": "investigate",
        "execution": "autonomous",
        "goal": "Find the root cause.",
        "inputs": {
            "complaint": {"$ref": "#/workflow/input/complaint"},
        },
        "completion": {
            "output_schema": {
                "type": "object",
                "required": ["root_cause"],
                "properties": {"root_cause": {"type": "string"}},
            },
            "validator": "validate_investigation",
        },
        "capabilities": {"tools": ["get_order"]},
        "limits": {"max_steps": 20},
        "transitions": {"completed": target, "failed": "$fail"},
    }


def _schema_registry(*, version: str = "1", include_goal: bool = True) -> SchemaRegistry:
    registry = SchemaRegistry()
    required = ["goal_verified"] if include_goal else ["result"]
    registry.register(
        SchemaDefinition(
            id="task-graph-result-v1",
            version=version,
            schema={
                "type": "object",
                "required": required,
                "properties": {
                    "goal_verified": {"type": "boolean"},
                    "result": {"type": "string"},
                },
            },
        )
    )
    return registry


def _task_graph_node(target: str = "$complete") -> dict:
    return {
        "id": "execute_goal",
        "execution": "task_graph",
        "inputs": {"intent": {"$ref": "#/workflow/input/intent"}},
        "planner": "rolling-wave-planner-v1",
        "graph_policy": "long-task-v1",
        "context_builder": "isolated-context-v1",
        "task_validators": ["task-schema-v1"],
        "group_validators": ["all-required-v1", "any-success-v1"],
        "criterion_validators": ["criterion-v1"],
        "goal_verifier": "goal-v1",
        "operation_adapters": ["build-v1"],
        "parent_inline_components": [],
        "human_task_contracts": [],
        "child_templates": [],
        "limits": {
            "max_tasks": 50,
            "max_graph_depth": 6,
            "max_replans": 10,
            "max_concurrency": 4,
            "max_child_runs": 20,
        },
        "completion": {
            "output_schema_id": "task-graph-result-v1",
            "validator": "task-graph-node-result-v1",
            "require": ["goal_verified"],
        },
        "transitions": {
            "completed": target,
            "waiting": "$wait",
            "failed": "$fail",
        },
    }


def test_parse_operation_workflow_is_canonical_and_immutable() -> None:
    workflow = parse_workflow(_workflow())

    assert workflow.id == "complaints"
    assert workflow.start_node == "classify"
    assert workflow.node("classify").operation == "op_classify"
    assert len(workflow.definition_fingerprint) == 64
    with pytest.raises(TypeError):
        workflow.input_schema["type"] = "array"  # type: ignore[index]


def test_fingerprint_ignores_mapping_and_node_order() -> None:
    first = _operation_node("first", target="second")
    second = _operation_node("second")
    raw = _workflow(first, second)
    reordered = {
        "nodes": [deepcopy(second), deepcopy(first)],
        "start_node": "first",
        "input_schema": {
            "properties": {"complaint": {"type": "string"}},
            "type": "object",
        },
        "id": "complaints",
        "description": "Resolve complaints.",
    }

    one = parse_workflow(raw)
    two = parse_workflow(reordered)

    assert one.definition_fingerprint == two.definition_fingerprint
    assert [node.id for node in one.nodes] == ["first", "second"]
    assert workflow_to_dict(one) == workflow_to_dict(two)


def test_parse_autonomous_node_with_runtime_registries() -> None:
    workflow = parse_workflow(
        _workflow(_autonomous_node()),
        known_validators={"validate_investigation"},
        agent_tools={"get_order", "search_messages"},
    )

    node = workflow.node("investigate")
    assert node.execution == "autonomous"
    assert node.goal == "Find the root cause."
    assert node.capability_tools == ("get_order",)
    assert node.max_steps == 20
    assert node.completion_required == ()


def test_parse_task_graph_node_is_closed_canonical_and_fingerprinted() -> None:
    workflow = parse_workflow(
        _workflow(_task_graph_node()),
        known_operations={"build-v1"},
        selectable_operations={"build-v1"},
        known_validators={"task-graph-node-result-v1"},
        schema_registry=_schema_registry(),
    )

    node = workflow.node("execute_goal")
    assert node.execution == "task_graph"
    assert node.task_graph is not None
    assert node.task_graph.planner == "rolling-wave-planner-v1"
    assert node.task_graph.limits.max_concurrency == 4
    assert node.completion_output_schema_id == "task-graph-result-v1"
    assert node.completion_output_schema["required"] == ("goal_verified",)  # type: ignore[index]
    serialized = workflow_to_dict(workflow)["nodes"][0]
    assert serialized["completion"]["output_schema_id"] == "task-graph-result-v1"
    assert "output_schema" not in serialized["completion"]
    assert serialized["transitions"]["waiting"] == "$wait"


def test_task_graph_schema_snapshot_changes_fingerprint_without_changing_source_shape() -> None:
    first = parse_workflow(
        _workflow(_task_graph_node()),
        schema_registry=_schema_registry(version="1"),
    )
    second = parse_workflow(
        _workflow(_task_graph_node()),
        schema_registry=_schema_registry(version="2"),
    )

    assert workflow_to_dict(first) == workflow_to_dict(second)
    assert first.definition_fingerprint != second.definition_fingerprint


def test_task_graph_requires_registered_schema_and_closed_transitions() -> None:
    with pytest.raises(WorkflowDefinitionError, match="requires schema_registry"):
        parse_workflow(_workflow(_task_graph_node()))

    unknown_schema = _task_graph_node()
    unknown_schema["completion"]["output_schema_id"] = "missing"
    with pytest.raises(WorkflowDefinitionError, match="unknown schema"):
        parse_workflow(
            _workflow(unknown_schema),
            schema_registry=_schema_registry(),
        )

    bad_wait = _task_graph_node()
    bad_wait["transitions"]["waiting"] = "$fail"
    with pytest.raises(WorkflowDefinitionError, match=r"waiting.*must target \$wait"):
        parse_workflow(_workflow(bad_wait), schema_registry=_schema_registry())

    missing_wait = _task_graph_node()
    del missing_wait["transitions"]["waiting"]
    with pytest.raises(WorkflowDefinitionError, match=r"must declare.*waiting"):
        parse_workflow(_workflow(missing_wait), schema_registry=_schema_registry())

    extra = _task_graph_node()
    extra["transitions"]["approved"] = "$complete"
    with pytest.raises(
        WorkflowDefinitionError, match=r"unsupported task_graph event.*approved"
    ):
        parse_workflow(_workflow(extra), schema_registry=_schema_registry())


def test_wait_sentinel_is_rejected_outside_task_graph() -> None:
    operation = _operation_node()
    operation["transitions"] = {"completed": "$wait"}
    with pytest.raises(WorkflowDefinitionError, match=r"unknown target '\$wait'"):
        parse_workflow(_workflow(operation))

    autonomous = _autonomous_node(target="$wait")
    with pytest.raises(WorkflowDefinitionError, match=r"unknown target '\$wait'"):
        parse_workflow(_workflow(autonomous))


def test_task_graph_rejects_inline_schema_and_invalid_limits_or_bindings() -> None:
    inline = _task_graph_node()
    inline["completion"] = {"output_schema": {"type": "object"}}
    with pytest.raises(WorkflowDefinitionError, match="requires output_schema_id"):
        parse_workflow(_workflow(inline), schema_registry=_schema_registry())

    bad_limit = _task_graph_node()
    bad_limit["limits"]["max_tasks"] = 0
    with pytest.raises(WorkflowDefinitionError, match="max_tasks must be a positive integer"):
        parse_workflow(_workflow(bad_limit), schema_registry=_schema_registry())

    widened = _task_graph_node()
    with pytest.raises(WorkflowDefinitionError, match="unknown operation"):
        parse_workflow(
            _workflow(widened),
            schema_registry=_schema_registry(),
            known_operations={"other"},
        )


def test_autonomous_completion_review_is_explicit_and_fingerprinted() -> None:
    reviewed = _workflow(_autonomous_node())
    reviewed["nodes"][0]["completion"]["review"] = "required"

    workflow = parse_workflow(reviewed, known_validators={"validate_investigation"})

    assert workflow.node("investigate").completion_review == "required"
    assert workflow_to_dict(workflow)["nodes"][0]["completion"]["review"] == "required"
    assert workflow.definition_fingerprint != parse_workflow(
        _workflow(_autonomous_node()),
        known_validators={"validate_investigation"},
    ).definition_fingerprint


def test_operation_node_cannot_request_completion_review() -> None:
    raw = _workflow()
    raw["nodes"][0]["completion"] = {"review": "required"}

    with pytest.raises(WorkflowDefinitionError, match="only for autonomous"):
        parse_workflow(raw)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update({"edges": []}), "unknown field.*edges"),
        (
            lambda raw: raw["nodes"][0].update({"fallback": "$fail"}),
            "unknown field.*fallback",
        ),
        (
            lambda raw: raw["nodes"][0].update({"execution": "reasoning"}),
            "operation.*unknown field|execution must be",
        ),
        (
            lambda raw: raw["nodes"][0].update({"execution": "human"}),
            "operation.*unknown field|execution must be",
        ),
    ],
)
def test_closed_schema_rejects_hidden_author_concepts(mutate, message: str) -> None:
    raw = _workflow()
    mutate(raw)
    with pytest.raises(WorkflowDefinitionError, match=message):
        parse_workflow(raw)


def test_nested_closed_schemas_reject_unknown_fields() -> None:
    autonomous = _autonomous_node()
    autonomous["completion"]["fallback"] = True
    with pytest.raises(WorkflowDefinitionError, match=r"completion.*unknown field.*fallback"):
        parse_workflow(_workflow(autonomous))

    autonomous = _autonomous_node()
    autonomous["capabilities"]["operations"] = ["x"]
    with pytest.raises(WorkflowDefinitionError, match=r"capabilities.*unknown field"):
        parse_workflow(_workflow(autonomous))

    autonomous = _autonomous_node()
    autonomous["limits"]["timeout"] = 1
    with pytest.raises(WorkflowDefinitionError, match=r"limits.*unknown field"):
        parse_workflow(_workflow(autonomous))


def test_duplicate_missing_and_unreachable_nodes_are_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="duplicate id"):
        parse_workflow(_workflow(_operation_node(), _operation_node()))

    with pytest.raises(WorkflowDefinitionError, match="start_node references unknown"):
        parse_workflow(_workflow(_operation_node(), start_node="missing"))

    with pytest.raises(WorkflowDefinitionError, match=r"unreachable node.*unused"):
        parse_workflow(_workflow(_operation_node(), _operation_node("unused")))


def test_transition_targets_and_failure_terminal_are_rejected() -> None:
    unknown = _operation_node()
    unknown["transitions"] = {"completed": "missing"}
    with pytest.raises(WorkflowDefinitionError, match="unknown target 'missing'"):
        parse_workflow(_workflow(unknown))

    failed_complete = _operation_node()
    failed_complete["transitions"] = {"failed": "$complete"}
    with pytest.raises(WorkflowDefinitionError, match=r"failed cannot target \$complete"):
        parse_workflow(_workflow(failed_complete))


def test_autonomous_transition_surface_is_closed() -> None:
    node = _autonomous_node()
    node["transitions"] = {"approved": "$complete"}
    with pytest.raises(WorkflowDefinitionError, match=r"unsupported autonomous event.*approved"):
        parse_workflow(_workflow(node))

    node = _autonomous_node()
    node["transitions"] = {"failed": "$fail"}
    with pytest.raises(WorkflowDefinitionError, match="must declare 'completed'"):
        parse_workflow(_workflow(node))

    operation = _operation_node()
    operation["transitions"] = {"waiting": "$fail"}
    with pytest.raises(WorkflowDefinitionError, match="cannot declare 'waiting'"):
        parse_workflow(_workflow(operation))


def test_autonomous_completion_requires_schema() -> None:
    node = _autonomous_node()
    del node["completion"]["output_schema"]
    with pytest.raises(WorkflowDefinitionError, match="requires output_schema"):
        parse_workflow(_workflow(node))


def test_autonomous_completion_semantic_validator_is_optional() -> None:
    node = _autonomous_node()
    del node["completion"]["validator"]

    workflow = parse_workflow(_workflow(node))

    assert workflow.node("investigate").completion_validator is None


def test_runtime_registry_and_capability_constraints() -> None:
    operation = _operation_node()
    with pytest.raises(WorkflowDefinitionError, match="unknown operation"):
        parse_workflow(_workflow(operation), known_operations={"different"})
    with pytest.raises(WorkflowDefinitionError, match="not selectable"):
        parse_workflow(
            _workflow(operation),
            known_operations={"op_classify"},
            selectable_operations=set(),
        )

    autonomous = _autonomous_node()
    with pytest.raises(WorkflowDefinitionError, match="unknown validator"):
        parse_workflow(_workflow(autonomous), known_validators={"different"})
    with pytest.raises(WorkflowDefinitionError, match="widens Agent capabilities"):
        parse_workflow(_workflow(autonomous), agent_tools={"search_messages"})


def test_input_reference_validation() -> None:
    first = _operation_node("first", target="second")
    second = _operation_node("second")
    second["inputs"] = {"value": {"$ref": "#/nodes/first/output/value"}}
    workflow = parse_workflow(_workflow(first, second))
    assert workflow.node("second").inputs["value"]["$ref"].endswith("/value")

    second["inputs"] = {"value": {"$ref": "#/nodes/missing/output"}}
    with pytest.raises(WorkflowDefinitionError, match="references unknown node"):
        parse_workflow(_workflow(first, second))

    single = _operation_node()
    single["inputs"] = {"value": {"$ref": "#/nodes/classify/output"}}
    with pytest.raises(WorkflowDefinitionError, match="own uncommitted output"):
        parse_workflow(_workflow(single))


def test_completion_require_is_normalized_into_object_schema() -> None:
    operation = _operation_node()
    operation["completion"] = {"require": ["answer"]}
    workflow = parse_workflow(_workflow(operation))
    node = workflow.node("classify")
    assert node.completion_output_schema["type"] == "object"  # type: ignore[index]
    assert node.completion_required == ("answer",)

    operation["completion"] = {
        "output_schema": {"type": "string"},
        "require": ["answer"],
    }
    with pytest.raises(WorkflowDefinitionError, match="type == 'object'"):
        parse_workflow(_workflow(operation))


@pytest.mark.parametrize("value", [0, -1, True, "20"])
def test_max_steps_must_be_positive_integer(value) -> None:
    node = _autonomous_node()
    node["limits"]["max_steps"] = value
    with pytest.raises(WorkflowDefinitionError, match="positive integer"):
        parse_workflow(_workflow(node))


def test_schema_profile_rejects_format_external_ref_and_recursion() -> None:
    raw = _workflow()
    raw["input_schema"] = {"type": "string", "format": "email"}
    with pytest.raises(WorkflowDefinitionError, match=r"format.*not supported"):
        parse_workflow(raw)

    raw = _workflow()
    raw["input_schema"] = {"$ref": "https://example.com/schema.json"}
    with pytest.raises(WorkflowDefinitionError, match="local JSON Pointer"):
        parse_workflow(raw)

    raw = _workflow()
    raw["input_schema"] = {
        "$defs": {"item": {"$ref": "#/$defs/item"}},
        "$ref": "#/$defs/item",
    }
    with pytest.raises(WorkflowDefinitionError, match="recursive JSON Schema"):
        parse_workflow(raw)


def test_schema_profile_accepts_non_recursive_local_ref() -> None:
    raw = _workflow()
    raw["input_schema"] = {
        "$defs": {"complaint": {"type": "string"}},
        "type": "object",
        "properties": {"complaint": {"$ref": "#/$defs/complaint"}},
    }
    workflow = parse_workflow(raw)
    validate_instance(workflow.input_schema, {"complaint": "late delivery"})


def test_schema_profile_allows_business_data_named_format_or_ref() -> None:
    raw = _workflow()
    raw["input_schema"] = {
        "type": "object",
        "properties": {
            "format": {"type": "string"},
            "metadata": {
                "type": "object",
                "const": {"$ref": "business-value", "format": "plain"},
            },
        },
    }
    workflow = parse_workflow(raw)
    validate_instance(
        workflow.input_schema,
        {"format": "plain", "metadata": {"$ref": "business-value", "format": "plain"}},
    )


def test_validate_instance_enforces_schema_and_depth() -> None:
    workflow = parse_workflow(_workflow())
    validate_instance(workflow.input_schema, {"complaint": "late delivery"})
    with pytest.raises(WorkflowInstanceError, match="not of type 'string'"):
        validate_instance(workflow.input_schema, {"complaint": 42})

    deeply_nested: object = "leaf"
    for _ in range(MAX_INSTANCE_DEPTH + 1):
        deeply_nested = [deeply_nested]
    with pytest.raises(WorkflowInstanceError, match="maximum JSON depth"):
        validate_instance({}, deeply_nested)
