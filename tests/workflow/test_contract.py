"""Execution-contract tests for the mandatory Workflow runtime."""

from __future__ import annotations

from dataclasses import replace

import pytest

from modi_harness.workflow import parse_workflow
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


def _validator(*, version: str = "1") -> CompletionValidator:
    return CompletionValidator(id="valid_answer", version=version, validate=lambda _value: True)


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
    assert replace(adapter, recovery_mode="provider_idempotent").effective_max_attempts(
        tool_retry_attempts=5
    ) == 5


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
    assert first.snapshot["validators"][0]["version"] == "1"


@pytest.mark.parametrize(
    "change",
    ["adapter_version", "validator_version", "output", "capability", "limit", "protocol"],
)
def test_execution_contract_fingerprint_changes_with_runtime_dependency(change: str) -> None:
    adapters = OperationAdapterRegistry()
    adapter = _adapter(version="2" if change == "adapter_version" else "1")
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
