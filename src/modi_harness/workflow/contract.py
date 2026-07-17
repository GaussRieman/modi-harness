"""Versioned execution dependencies for Workflow runs.

Workflow YAML describes business control. This module binds that definition to
the trusted adapters, validators, capabilities, limits, and protocol versions
that actually determine runtime behavior. A persisted run resumes only against
the same canonical execution contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from .._utils import compute_fingerprint
from .components import ComponentRegistryError, PinnedComponentRegistry
from .definition import workflow_to_dict
from .types import Workflow

AdapterKind = Literal["tool", "memory_write", "workflow_control"]
RecoveryMode = Literal[
    "pure",
    "provider_idempotent",
    "gateway_claimed",
    "manual_reconciliation",
]

_ADAPTER_KINDS = frozenset({"tool", "memory_write", "workflow_control"})
_RECOVERY_MODES = frozenset(
    {"pure", "provider_idempotent", "gateway_claimed", "manual_reconciliation"}
)


class ExecutionContractError(ValueError):
    """A Workflow cannot be bound to a complete trusted execution contract."""


@dataclass(frozen=True, slots=True)
class OperationAdapter:
    """Trusted metadata for translating one Workflow Operation."""

    id: str
    version: str
    kind: AdapterKind
    target: str
    node_selectable: bool
    required_capabilities: tuple[str, ...]
    side_effect: bool
    recovery_mode: RecoveryMode
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    max_calls_per_node: int | None = None
    max_calls_per_task: int | None = None
    fresh_output_prerequisite: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "version", "target"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ExecutionContractError(f"Operation adapter {field_name} must be non-empty")
            object.__setattr__(self, field_name, value.strip())
        if self.kind not in _ADAPTER_KINDS:
            raise ExecutionContractError(f"unsupported Operation adapter kind {self.kind!r}")
        if self.recovery_mode not in _RECOVERY_MODES:
            raise ExecutionContractError(
                f"unsupported Operation recovery mode {self.recovery_mode!r}"
            )
        capabilities = tuple(sorted(set(self.required_capabilities)))
        if any(not item.strip() for item in capabilities):
            raise ExecutionContractError("adapter capabilities must be non-empty strings")
        object.__setattr__(self, "required_capabilities", capabilities)
        object.__setattr__(self, "input_schema", _freeze_mapping(self.input_schema))
        object.__setattr__(self, "output_schema", _freeze_mapping(self.output_schema))
        prerequisite = self.fresh_output_prerequisite
        if prerequisite is not None:
            if not isinstance(prerequisite, Mapping):
                raise ExecutionContractError(
                    "adapter fresh_output_prerequisite must be a mapping"
                )
            required = {
                "argument",
                "issuer_adapter",
                "issuer_output_field",
                "issued_at_field",
                "ttl_seconds",
            }
            if set(prerequisite) != required:
                raise ExecutionContractError(
                    "adapter fresh_output_prerequisite has invalid fields"
                )
            normalized_prerequisite = dict(prerequisite)
            for field_name in required - {"ttl_seconds"}:
                value = normalized_prerequisite[field_name]
                if not isinstance(value, str) or not value.strip():
                    raise ExecutionContractError(
                        f"adapter prerequisite {field_name} must be non-empty"
                    )
                normalized_prerequisite[field_name] = value.strip()
            ttl_seconds = normalized_prerequisite["ttl_seconds"]
            if (
                not isinstance(ttl_seconds, int)
                or isinstance(ttl_seconds, bool)
                or ttl_seconds < 1
            ):
                raise ExecutionContractError(
                    "adapter prerequisite ttl_seconds must be a positive integer"
                )
            object.__setattr__(
                self,
                "fresh_output_prerequisite",
                _freeze_mapping(normalized_prerequisite),
            )
        if self.max_calls_per_node is not None:
            if (
                not isinstance(self.max_calls_per_node, int)
                or isinstance(self.max_calls_per_node, bool)
                or self.max_calls_per_node < 1
            ):
                raise ExecutionContractError(
                    "adapter max_calls_per_node must be a positive integer"
                )
        if self.max_calls_per_task is not None:
            if (
                not isinstance(self.max_calls_per_task, int)
                or isinstance(self.max_calls_per_task, bool)
                or self.max_calls_per_task < 1
            ):
                raise ExecutionContractError(
                    "adapter max_calls_per_task must be a positive integer"
                )
        if self.kind == "workflow_control" and self.node_selectable:
            raise ExecutionContractError(
                "workflow_control adapters are internal and cannot be node-selectable"
            )

    def effective_max_attempts(self, *, tool_retry_attempts: int) -> int:
        """Narrow gateway retry according to the trusted recovery contract."""

        attempts = max(1, int(tool_retry_attempts))
        if self.side_effect and self.recovery_mode == "manual_reconciliation":
            return 1
        return attempts

    def snapshot(self) -> dict[str, Any]:
        """Return canonical runtime-relevant metadata."""

        return {
            "id": self.id,
            "version": self.version,
            "kind": self.kind,
            "target": self.target,
            "node_selectable": self.node_selectable,
            "required_capabilities": list(self.required_capabilities),
            "side_effect": self.side_effect,
            "recovery_mode": self.recovery_mode,
            "input_schema": _thaw(self.input_schema),
            "output_schema": _thaw(self.output_schema),
            "max_calls_per_node": self.max_calls_per_node,
            "max_calls_per_task": self.max_calls_per_task,
            "fresh_output_prerequisite": (
                _thaw(self.fresh_output_prerequisite)
                if self.fresh_output_prerequisite is not None
                else None
            ),
        }


class OperationAdapterRegistry:
    """Closed lookup for trusted RuntimeOperation adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, OperationAdapter] = {}

    def register(self, adapter: OperationAdapter) -> None:
        if adapter.id in self._adapters:
            raise ExecutionContractError(f"duplicate Operation adapter {adapter.id!r}")
        self._adapters[adapter.id] = adapter

    def resolve(self, adapter_id: str) -> OperationAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise ExecutionContractError(f"unknown Operation adapter {adapter_id!r}") from exc

    def resolve_node_adapter(self, adapter_id: str) -> OperationAdapter:
        adapter = self.resolve(adapter_id)
        if not adapter.node_selectable:
            raise ExecutionContractError(
                f"Operation adapter {adapter_id!r} is not selectable by Workflow nodes"
            )
        return adapter

    def selectable_ids(self) -> frozenset[str]:
        return frozenset(
            adapter.id for adapter in self._adapters.values() if adapter.node_selectable
        )


@dataclass(frozen=True, slots=True)
class CompletionValidator:
    """Versioned semantic completion predicate."""

    id: str
    version: str
    validate: Callable[[Any], bool]
    explain: Callable[[Any], str | None] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ExecutionContractError("completion validator id must be non-empty")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ExecutionContractError("completion validator version must be non-empty")
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "version", self.version.strip())
        if not callable(self.validate):
            raise ExecutionContractError("completion validator must be callable")
        if self.explain is not None and not callable(self.explain):
            raise ExecutionContractError("completion validator explain must be callable")

    def rejection_reason(self, value: Any) -> str | None:
        """Return repair feedback when the semantic predicate rejects a value."""

        if self.validate(value):
            return None
        if self.explain is None:
            return "semantic completion predicate returned false"
        return self.explain(value) or "semantic completion predicate returned false"

    def snapshot(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version}


class CompletionValidatorRegistry:
    """Closed lookup for trusted completion validators."""

    def __init__(self) -> None:
        self._validators: dict[str, CompletionValidator] = {}

    def register(self, validator: CompletionValidator) -> None:
        if validator.id in self._validators:
            raise ExecutionContractError(f"duplicate completion validator {validator.id!r}")
        self._validators[validator.id] = validator

    def resolve(self, validator_id: str) -> CompletionValidator:
        try:
            return self._validators[validator_id]
        except KeyError as exc:
            raise ExecutionContractError(f"unknown completion validator {validator_id!r}") from exc

    def ids(self) -> frozenset[str]:
        return frozenset(self._validators)


@dataclass(frozen=True, slots=True)
class ExecutionContract:
    """Canonical dependency snapshot pinned to one selected Workflow run."""

    snapshot: Mapping[str, Any]
    fingerprint: str


def build_execution_contract(
    *,
    workflow: Workflow,
    adapters: OperationAdapterRegistry,
    validators: CompletionValidatorRegistry,
    output_contract: Mapping[str, Any],
    capability_ceiling: Iterable[str],
    limits: Mapping[str, int],
    protocol_version: str,
    task_graph_components: PinnedComponentRegistry | None = None,
) -> ExecutionContract:
    """Resolve and fingerprint every dependency that can change run behavior."""

    if not isinstance(protocol_version, str) or not protocol_version.strip():
        raise ExecutionContractError("protocol_version must be non-empty")
    ceiling = frozenset(str(item) for item in capability_ceiling)
    selected_adapters: dict[str, OperationAdapter] = {}
    selected_validators: dict[str, CompletionValidator] = {}
    task_graph_nodes: list[dict[str, Any]] = []

    for node in workflow.nodes:
        if node.execution == "operation":
            if node.operation is None:  # protected by definition validation
                raise ExecutionContractError(f"operation Node {node.id!r} has no adapter")
            adapter = adapters.resolve_node_adapter(node.operation)
            missing = set(adapter.required_capabilities) - ceiling
            if missing:
                joined = ", ".join(sorted(missing))
                raise ExecutionContractError(
                    f"Operation adapter {adapter.id!r} exceeds capability ceiling: {joined}"
                )
            selected_adapters[adapter.id] = adapter
        elif node.execution == "autonomous":
            for adapter_id in node.capability_tools or ():
                adapter = adapters.resolve_node_adapter(adapter_id)
                missing = set(adapter.required_capabilities) - ceiling
                if missing:
                    joined = ", ".join(sorted(missing))
                    raise ExecutionContractError(
                        f"Operation adapter {adapter.id!r} exceeds capability ceiling: {joined}"
                    )
                selected_adapters[adapter.id] = adapter
        else:
            config = node.task_graph
            if config is None:
                raise ExecutionContractError(
                    f"task_graph Node {node.id!r} has no normalized Task Graph config"
                )
            if task_graph_components is None:
                raise ExecutionContractError(
                    "Task Graph execution requires task_graph_components registry"
                )
            bindings: dict[str, list[dict[str, Any]] | dict[str, Any]] = {}
            for field_name, kind, component_id in (
                ("planner", "planner", config.planner),
                ("graph_policy", "graph_policy", config.graph_policy),
                ("context_builder", "context_builder", config.context_builder),
                ("goal_verifier", "goal_verifier", config.goal_verifier),
            ):
                try:
                    component = task_graph_components.resolve(component_id, kind=kind)  # type: ignore[arg-type]
                except ComponentRegistryError as exc:
                    raise ExecutionContractError(str(exc)) from exc
                bindings[field_name] = component.snapshot()
            for field_name, kind, component_ids in (
                ("task_validators", "task_verifier", config.task_validators),
                ("group_validators", "group_verifier", config.group_validators),
                ("criterion_validators", "criterion_verifier", config.criterion_validators),
                ("parent_inline_components", "parent_inline", config.parent_inline_components),
                ("human_task_contracts", "human_contract", config.human_task_contracts),
            ):
                snapshots: list[dict[str, Any]] = []
                for component_id in component_ids:
                    try:
                        component = task_graph_components.resolve(
                            component_id, kind=kind  # type: ignore[arg-type]
                        )
                    except ComponentRegistryError as exc:
                        raise ExecutionContractError(str(exc)) from exc
                    snapshots.append(component.snapshot())
                bindings[field_name] = snapshots
            for adapter_id in config.operation_adapters:
                adapter = adapters.resolve_node_adapter(adapter_id)
                missing = set(adapter.required_capabilities) - ceiling
                if missing:
                    joined = ", ".join(sorted(missing))
                    raise ExecutionContractError(
                        f"Operation adapter {adapter.id!r} exceeds capability ceiling: {joined}"
                    )
                selected_adapters[adapter.id] = adapter
            task_graph_nodes.append(
                {
                    "node_id": node.id,
                    "bindings": bindings,
                    "limits": {
                        "max_tasks": config.limits.max_tasks,
                        "max_graph_depth": config.limits.max_graph_depth,
                        "max_replans": config.limits.max_replans,
                        "max_concurrency": config.limits.max_concurrency,
                        "max_child_runs": config.limits.max_child_runs,
                    },
                    "output_schema": {
                        "id": node.completion_output_schema_id,
                        "version": node.completion_output_schema_version,
                        "fingerprint": node.completion_output_schema_fingerprint,
                        "schema": _thaw(node.completion_output_schema),
                    },
                }
            )
        if node.completion_validator is not None:
            validator = validators.resolve(node.completion_validator)
            selected_validators[validator.id] = validator

    for adapter in tuple(selected_adapters.values()):
        prerequisite = adapter.fresh_output_prerequisite
        if prerequisite is None:
            continue
        issuer_id = str(prerequisite["issuer_adapter"])
        if issuer_id not in selected_adapters:
            raise ExecutionContractError(
                f"Operation adapter {adapter.id!r} requires issuer adapter "
                f"{issuer_id!r} in the selected Workflow"
            )

    normalized_limits: dict[str, int] = {}
    for key, value in limits.items():
        if not isinstance(key, str) or not key.strip():
            raise ExecutionContractError("runtime limit names must be non-empty")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ExecutionContractError(f"runtime limit {key!r} must be a positive integer")
        normalized_limits[key] = value

    snapshot: dict[str, Any] = {
        "workflow": workflow_to_dict(workflow),
        "definition_fingerprint": workflow.definition_fingerprint,
        "adapters": [selected_adapters[key].snapshot() for key in sorted(selected_adapters)],
        "validators": [selected_validators[key].snapshot() for key in sorted(selected_validators)],
        "output_contract": _thaw(output_contract),
        "capability_ceiling": sorted(ceiling),
        "limits": {key: normalized_limits[key] for key in sorted(normalized_limits)},
        "protocol_version": protocol_version.strip(),
    }
    if task_graph_nodes:
        snapshot["task_graph"] = {
            "protocol_version": "task-graph-v1",
            "nodes": sorted(task_graph_nodes, key=lambda item: item["node_id"]),
        }
    return ExecutionContract(
        snapshot=cast(Mapping[str, Any], _freeze(snapshot)),
        fingerprint=compute_fingerprint(snapshot),
    )


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionContractError("adapter schemas must be mappings")
    return cast(Mapping[str, Any], _freeze(dict(value)))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "AdapterKind",
    "CompletionValidator",
    "CompletionValidatorRegistry",
    "ExecutionContract",
    "ExecutionContractError",
    "OperationAdapter",
    "OperationAdapterRegistry",
    "RecoveryMode",
    "build_execution_contract",
]
