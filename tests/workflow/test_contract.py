"""Execution-contract tests for the mandatory Workflow runtime."""

from __future__ import annotations

from dataclasses import replace

import pytest

from modi_harness.workflow import (
    PinnedComponent,
    PinnedComponentRegistry,
    SchemaDefinition,
    SchemaRegistry,
    parse_workflow,
)
from modi_harness.workflow.contract import (
    CompletionValidator,
    CompletionValidatorRegistry,
    ExecutionContractError,
    OperationAdapter,
    OperationAdapterRegistry,
    build_execution_contract,
)


def _workflow(*, operation: str = "lookup"):
    return parse_workflow(
        {
            "id": "answer",
            "description": "Answer a question.",
            "input_schema": {"type": "object"},
            "start_node": "lookup",
            "nodes": [
                {
                    "id": "lookup",
                    "execution": "operation",
                    "operation": operation,
                    "completion": {"validator": "valid_answer"},
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )


def _adapter(**changes: object) -> OperationAdapter:
    values = {
        "id": "lookup",
        "version": "1",
        "kind": "tool",
        "target": "search",
        "node_selectable": True,
        "required_capabilities": ("search",),
        "side_effect": False,
        "recovery_mode": "pure",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    values.update(changes)
    return OperationAdapter(**values)  # type: ignore[arg-type]


def _validator(*, id: str = "valid_answer", version: str = "1") -> CompletionValidator:
    return CompletionValidator(id=id, version=version, validate=lambda _value: True)


def _task_graph_workflow():
    schemas = SchemaRegistry()
    schemas.register(
        SchemaDefinition(
            id="task-graph-result-v1",
            version="1",
            schema={"type": "object", "required": ["goal_verified"]},
        )
    )
    return parse_workflow(
        {
            "id": "long-task",
            "description": "Execute a long task.",
            "input_schema": {"type": "object"},
            "start_node": "execute_goal",
            "nodes": [
                {
                    "id": "execute_goal",
                    "execution": "task_graph",
                    "inputs": {},
                    "planner": "planner-v1",
                    "graph_policy": "policy-v1",
                    "context_builder": "context-v1",
                    "task_validators": ["task-v1"],
                    "group_validators": ["group-v1"],
                    "criterion_validators": ["criterion-v1"],
                    "goal_verifier": "goal-v1",
                    "operation_adapters": ["build-v1"],
                    "parent_inline_components": [],
                    "human_task_contracts": [],
                    "child_templates": [],
                    "limits": {
                        "max_tasks": 10,
                        "max_graph_depth": 3,
                        "max_replans": 2,
                        "max_concurrency": 2,
                        "max_child_runs": 2,
                    },
                    "completion": {
                        "output_schema_id": "task-graph-result-v1",
                        "validator": "task-graph-node-result-v1",
                    },
                    "transitions": {
                        "completed": "$complete",
                        "waiting": "$wait",
                        "failed": "$fail",
                    },
                }
            ],
        },
        schema_registry=schemas,
        known_operations={"build-v1"},
        selectable_operations={"build-v1"},
        known_validators={"task-graph-node-result-v1"},
    )


def _task_graph_components(*, digest: str = "sha256:component") -> PinnedComponentRegistry:
    registry = PinnedComponentRegistry()
    for component_id, kind in (
        ("planner-v1", "planner"),
        ("policy-v1", "graph_policy"),
        ("context-v1", "context_builder"),
        ("task-v1", "task_verifier"),
        ("group-v1", "group_verifier"),
        ("criterion-v1", "criterion_verifier"),
        ("goal-v1", "goal_verifier"),
    ):
        registry.register(
            PinnedComponent(
                id=component_id,
                version="1",
                kind=kind,  # type: ignore[arg-type]
                implementation_digest=digest,
                protocol_version="task-graph-v1",
                input_schema_id="component-input-v1",
                output_schema_id="component-output-v1",
                supported_outcomes=("passed", "repairable", "ambiguous", "terminal"),
                configuration={},
                implementation=lambda value: value,
            )
        )
    return registry


def _autonomous_workflow(*, tools: tuple[str, ...] = ("lookup",)):
    return parse_workflow(
        {
            "id": "research",
            "description": "Research a topic.",
            "input_schema": {"type": "object"},
            "start_node": "investigate",
            "nodes": [
                {
                    "id": "investigate",
                    "execution": "autonomous",
                    "goal": "Investigate",
                    "completion": {"output_schema": {"type": "object"}},
                    "capabilities": {"tools": list(tools)},
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )


def test_registry_rejects_duplicate_and_internal_node_adapter() -> None:
    registry = OperationAdapterRegistry()
    registry.register(_adapter())
    with pytest.raises(ExecutionContractError, match="duplicate Operation adapter"):
        registry.register(_adapter())

    internal = _adapter(
        id="complete_node",
        kind="workflow_control",
        target="complete_node",
        node_selectable=False,
        required_capabilities=(),
        recovery_mode="pure",
    )
    registry.register(internal)
    with pytest.raises(ExecutionContractError, match="not selectable"):
        registry.resolve_node_adapter("complete_node")


def test_workflow_control_adapter_cannot_be_author_selectable() -> None:
    with pytest.raises(ExecutionContractError, match=r"workflow_control.*internal"):
        OperationAdapterRegistry().register(
            _adapter(
                id="complete_node",
                kind="workflow_control",
                target="complete_node",
                node_selectable=True,
                required_capabilities=(),
            )
        )


def test_manual_reconciliation_side_effect_forbids_gateway_retry() -> None:
    adapter = _adapter(
        side_effect=True,
        recovery_mode="manual_reconciliation",
    )
    assert adapter.effective_max_attempts(tool_retry_attempts=5) == 1
    assert (
        replace(adapter, recovery_mode="provider_idempotent").effective_max_attempts(
            tool_retry_attempts=5
        )
        == 5
    )


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5, "4"])
def test_operation_adapter_rejects_invalid_node_call_budget(maximum: object) -> None:
    with pytest.raises(ExecutionContractError, match="must be a positive integer"):
        _adapter(max_calls_per_node=maximum)


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5, "2"])
def test_operation_adapter_rejects_invalid_task_call_budget(maximum: object) -> None:
    with pytest.raises(ExecutionContractError, match="must be a positive integer"):
        _adapter(max_calls_per_task=maximum)


@pytest.mark.parametrize(
    ("prerequisite", "message"),
    [
        ({}, "invalid fields"),
        (
            {
                "argument": "time_token",
                "issuer_adapter": "clock",
                "issuer_output_field": "time_token",
                "issued_at_field": "issued_at",
                "ttl_seconds": 0,
            },
            "positive integer",
        ),
        (
            {
                "argument": " ",
                "issuer_adapter": "clock",
                "issuer_output_field": "time_token",
                "issued_at_field": "issued_at",
                "ttl_seconds": 120,
            },
            "must be non-empty",
        ),
    ],
)
def test_operation_adapter_rejects_invalid_fresh_output_prerequisite(
    prerequisite: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ExecutionContractError, match=message):
        _adapter(fresh_output_prerequisite=prerequisite)


def test_completion_validator_returns_specific_rejection_reason() -> None:
    validator = CompletionValidator(
        id="specific",
        version="1",
        validate=lambda value: value == "valid",
        explain=lambda value: f"expected 'valid', got {value!r}",
    )

    assert validator.rejection_reason("valid") is None
    assert validator.rejection_reason("invalid") == "expected 'valid', got 'invalid'"


def test_execution_contract_pins_all_runtime_dependencies() -> None:
    adapters = OperationAdapterRegistry()
    adapters.register(_adapter())
    validators = CompletionValidatorRegistry()
    validators.register(_validator())

    first = build_execution_contract(
        workflow=_workflow(),
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 10},
        protocol_version="workflow-v1",
    )
    second = build_execution_contract(
        workflow=_workflow(),
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 10},
        protocol_version="workflow-v1",
    )

    assert first.fingerprint == second.fingerprint
    assert first.snapshot["workflow"]["id"] == "answer"
    assert first.snapshot["adapters"][0]["version"] == "1"
    assert first.snapshot["adapters"][0]["max_calls_per_node"] is None
    assert first.snapshot["validators"][0]["version"] == "1"


def test_execution_contract_binds_autonomous_node_capability_adapters() -> None:
    adapters = OperationAdapterRegistry()
    adapters.register(_adapter(max_calls_per_node=4))
    contract = build_execution_contract(
        workflow=_autonomous_workflow(),
        adapters=adapters,
        validators=CompletionValidatorRegistry(),
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 10},
        protocol_version="workflow-v1",
    )

    assert len(contract.snapshot["adapters"]) == 1
    assert contract.snapshot["adapters"][0]["id"] == "lookup"
    assert contract.snapshot["adapters"][0]["max_calls_per_node"] == 4


def test_execution_contract_pins_fresh_output_prerequisite_metadata() -> None:
    prerequisite = {
        "argument": "time_token",
        "issuer_adapter": "clock",
        "issuer_output_field": "time_token",
        "issued_at_field": "issued_at",
        "ttl_seconds": 120,
    }
    adapters = OperationAdapterRegistry()
    adapters.register(_adapter(fresh_output_prerequisite=prerequisite))
    adapters.register(
        _adapter(
            id="clock",
            target="clock",
            required_capabilities=(),
        )
    )
    contract = build_execution_contract(
        workflow=_autonomous_workflow(tools=("clock", "lookup")),
        adapters=adapters,
        validators=CompletionValidatorRegistry(),
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 10},
        protocol_version="workflow-v1",
    )

    lookup = next(item for item in contract.snapshot["adapters"] if item["id"] == "lookup")
    assert lookup["fresh_output_prerequisite"] == prerequisite


def test_execution_contract_requires_prerequisite_issuer_in_selected_workflow() -> None:
    adapters = OperationAdapterRegistry()
    adapters.register(
        _adapter(
            fresh_output_prerequisite={
                "argument": "time_token",
                "issuer_adapter": "clock",
                "issuer_output_field": "time_token",
                "issued_at_field": "issued_at",
                "ttl_seconds": 120,
            }
        )
    )
    adapters.register(_adapter(id="clock", target="clock", required_capabilities=()))

    with pytest.raises(ExecutionContractError, match="requires issuer adapter 'clock'"):
        build_execution_contract(
            workflow=_autonomous_workflow(),
            adapters=adapters,
            validators=CompletionValidatorRegistry(),
            output_contract={"free_form": True},
            capability_ceiling={"search"},
            limits={"max_transitions": 10},
            protocol_version="workflow-v1",
        )


@pytest.mark.parametrize(
    "change",
    [
        "adapter_version",
        "adapter_budget",
        "validator_version",
        "output",
        "capability",
        "limit",
        "protocol",
    ],
)
def test_execution_contract_fingerprint_changes_with_runtime_dependency(change: str) -> None:
    adapters = OperationAdapterRegistry()
    adapter = _adapter(
        version="2" if change == "adapter_version" else "1",
        max_calls_per_node=4 if change == "adapter_budget" else None,
    )
    adapters.register(adapter)
    validators = CompletionValidatorRegistry()
    validators.register(_validator(version="2" if change == "validator_version" else "1"))

    baseline_adapters = OperationAdapterRegistry()
    baseline_adapters.register(_adapter())
    baseline_validators = CompletionValidatorRegistry()
    baseline_validators.register(_validator())
    baseline = build_execution_contract(
        workflow=_workflow(),
        adapters=baseline_adapters,
        validators=baseline_validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 10},
        protocol_version="workflow-v1",
    )
    changed = build_execution_contract(
        workflow=_workflow(),
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": change != "output"},
        capability_ceiling={"search", "write"} if change == "capability" else {"search"},
        limits={"max_transitions": 11 if change == "limit" else 10},
        protocol_version="workflow-v2" if change == "protocol" else "workflow-v1",
    )

    assert changed.fingerprint != baseline.fingerprint


def test_execution_contract_rejects_unknown_or_unavailable_dependency() -> None:
    adapters = OperationAdapterRegistry()
    adapters.register(_adapter(required_capabilities=("search", "private")))
    validators = CompletionValidatorRegistry()
    validators.register(_validator())

    with pytest.raises(ExecutionContractError, match="capability ceiling"):
        build_execution_contract(
            workflow=_workflow(),
            adapters=adapters,
            validators=validators,
            output_contract={"free_form": True},
            capability_ceiling={"search"},
            limits={"max_transitions": 10},
            protocol_version="workflow-v1",
        )


def test_task_graph_contract_pins_component_and_schema_snapshots() -> None:
    adapters = OperationAdapterRegistry()
    adapters.register(_adapter(id="build-v1", target="build"))
    validators = CompletionValidatorRegistry()
    validators.register(
        CompletionValidator(
            id="task-graph-node-result-v1",
            version="1",
            validate=lambda value: bool(value.get("goal_verified")),
        )
    )
    contract = build_execution_contract(
        workflow=_task_graph_workflow(),
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 4},
        protocol_version="workflow-task-graph-v1",
        task_graph_components=_task_graph_components(),
    )

    graph = contract.snapshot["task_graph"]
    assert graph["protocol_version"] == "task-graph-v1"
    assert graph["nodes"][0]["bindings"]["goal_verifier"]["id"] == "goal-v1"
    assert graph["nodes"][0]["output_schema"]["id"] == "task-graph-result-v1"


def test_task_graph_contract_requires_components_and_changes_with_digest() -> None:
    adapters = OperationAdapterRegistry()
    adapters.register(_adapter(id="build-v1", target="build"))
    validators = CompletionValidatorRegistry()
    validators.register(_validator(id="task-graph-node-result-v1"))

    with pytest.raises(ExecutionContractError, match="requires task_graph_components"):
        build_execution_contract(
            workflow=_task_graph_workflow(),
            adapters=adapters,
            validators=validators,
            output_contract={"free_form": True},
            capability_ceiling={"search"},
            limits={"max_transitions": 4},
            protocol_version="workflow-task-graph-v1",
        )

    first = build_execution_contract(
        workflow=_task_graph_workflow(),
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 4},
        protocol_version="workflow-task-graph-v1",
        task_graph_components=_task_graph_components(digest="sha256:one"),
    )
    second = build_execution_contract(
        workflow=_task_graph_workflow(),
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 4},
        protocol_version="workflow-task-graph-v1",
        task_graph_components=_task_graph_components(digest="sha256:two"),
    )
    assert first.fingerprint != second.fingerprint

    with pytest.raises(ExecutionContractError, match="unknown Operation adapter"):
        build_execution_contract(
            workflow=_workflow(operation="missing"),
            adapters=adapters,
            validators=validators,
            output_contract={"free_form": True},
            capability_ceiling={"search", "private"},
            limits={"max_transitions": 10},
            protocol_version="workflow-v1",
        )
