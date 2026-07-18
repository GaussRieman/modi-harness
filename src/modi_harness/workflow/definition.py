"""Parse, validate, and fingerprint Workflow definitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from types import MappingProxyType
from typing import Any, cast

import yaml  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]

from .schema_registry import SchemaDefinition, SchemaRegistry, SchemaRegistryError
from .types import (
    WORKFLOW_COMPLETE,
    WORKFLOW_TERMINALS,
    WORKFLOW_WAIT,
    CompletionReview,
    Node,
    TaskGraphLimits,
    TaskGraphNodeConfig,
    Workflow,
)

_WORKFLOW_FIELDS = frozenset({"id", "description", "input_schema", "start_node", "nodes"})
_NODE_COMMON_FIELDS = frozenset({"id", "execution", "inputs", "completion", "transitions"})
_NODE_OPERATION_FIELDS = _NODE_COMMON_FIELDS | {"operation"}
_NODE_AUTONOMOUS_FIELDS = _NODE_COMMON_FIELDS | {
    "goal",
    "capabilities",
    "limits",
}
_NODE_TASK_GRAPH_FIELDS = _NODE_COMMON_FIELDS | {
    "planner",
    "graph_policy",
    "context_builder",
    "task_validators",
    "group_validators",
    "criterion_validators",
    "goal_verifier",
    "operation_adapters",
    "parent_inline_components",
    "human_task_contracts",
    "child_templates",
    "limits",
}
_TASK_GRAPH_REQUIRED_FIELDS = _NODE_TASK_GRAPH_FIELDS - _NODE_COMMON_FIELDS
_COMPLETION_FIELDS = frozenset(
    {"output_schema", "output_schema_id", "validator", "require", "review"}
)
_CAPABILITY_FIELDS = frozenset({"tools"})
_LIMIT_FIELDS = frozenset({"max_steps"})
_AUTONOMOUS_TRANSITIONS = frozenset({"completed", "failed"})
_TASK_GRAPH_TRANSITIONS = frozenset({"completed", "waiting", "failed"})
_TASK_GRAPH_LIMIT_FIELDS = frozenset(
    {
        "max_tasks",
        "max_graph_depth",
        "max_replans",
        "max_concurrency",
        "max_child_runs",
        "template_concurrency_limits",
    }
)
_TASK_GRAPH_REQUIRED_LIMIT_FIELDS = _TASK_GRAPH_LIMIT_FIELDS - {
    "template_concurrency_limits"
}
_SINGLE_SUBSCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_ARRAY_SUBSCHEMA_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_MAPPING_SUBSCHEMA_KEYS = frozenset(
    {"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"}
)

MAX_SCHEMA_BYTES = 64 * 1024
MAX_SCHEMA_DEPTH = 32
MAX_INSTANCE_BYTES = 1024 * 1024
MAX_INSTANCE_DEPTH = 64


class WorkflowDefinitionError(ValueError):
    """An author-provided Workflow definition is invalid."""


class WorkflowInstanceError(ValueError):
    """A Workflow input or Node output violates its validated schema."""


class _UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise WorkflowDefinitionError(
                f"duplicate YAML mapping key {key!r} at line {key_node.start_mark.line + 1}"
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def parse_workflow_yaml(
    text: str,
    *,
    source: str = "<workflow>",
    known_operations: set[str] | frozenset[str] | None = None,
    selectable_operations: set[str] | frozenset[str] | None = None,
    known_validators: set[str] | frozenset[str] | None = None,
    agent_tools: set[str] | frozenset[str] | None = None,
    schema_registry: SchemaRegistry | None = None,
) -> Workflow:
    """Safely parse one YAML document, rejecting duplicate keys."""

    try:
        raw = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except WorkflowDefinitionError as exc:
        raise WorkflowDefinitionError(f"{source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise WorkflowDefinitionError(f"{source}: invalid YAML: {exc}") from exc
    if raw is None:
        raise WorkflowDefinitionError(f"{source}: document cannot be empty")
    return parse_workflow(
        raw,
        source=source,
        known_operations=known_operations,
        selectable_operations=selectable_operations,
        known_validators=known_validators,
        agent_tools=agent_tools,
        schema_registry=schema_registry,
    )


def parse_workflow(
    raw: Mapping[str, Any],
    *,
    source: str = "<workflow>",
    known_operations: set[str] | frozenset[str] | None = None,
    selectable_operations: set[str] | frozenset[str] | None = None,
    known_validators: set[str] | frozenset[str] | None = None,
    agent_tools: set[str] | frozenset[str] | None = None,
    schema_registry: SchemaRegistry | None = None,
) -> Workflow:
    """Validate a raw mapping and return its immutable canonical projection."""

    root = _require_mapping(raw, source)
    _reject_unknown(root, _WORKFLOW_FIELDS, source)
    _require_fields(root, _WORKFLOW_FIELDS, source)

    workflow_id = _nonempty_string(root["id"], f"{source}.id")
    description = _nonempty_string(root["description"], f"{source}.description")
    input_schema = _normalize_schema(root["input_schema"], f"{source}.input_schema")
    start_node = _nonempty_string(root["start_node"], f"{source}.start_node")

    raw_nodes = root["nodes"]
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise WorkflowDefinitionError(f"{source}.nodes must be a non-empty list")

    nodes = [
        _parse_node(
            item,
            source=f"{source}.nodes[{index}]",
            known_operations=known_operations,
            selectable_operations=selectable_operations,
            known_validators=known_validators,
            agent_tools=agent_tools,
            schema_registry=schema_registry,
        )
        for index, item in enumerate(raw_nodes)
    ]
    node_ids = [node.id for node in nodes]
    duplicates = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
    if duplicates:
        raise WorkflowDefinitionError(
            f"{source}.nodes contains duplicate id(s): {', '.join(duplicates)}"
        )
    known_node_ids = frozenset(node_ids)
    if start_node not in known_node_ids:
        raise WorkflowDefinitionError(f"{source}.start_node references unknown node {start_node!r}")

    for node in nodes:
        _validate_node_references(node, known_node_ids, source)
    _validate_reachability(nodes, start_node, source)

    ordered_nodes = tuple(sorted(nodes, key=lambda item: item.id))
    canonical = {
        "id": workflow_id,
        "description": description,
        "input_schema": _thaw(input_schema),
        "start_node": start_node,
        "nodes": [_node_to_fingerprint_dict(node) for node in ordered_nodes],
    }
    fingerprint = hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()
    return Workflow(
        id=workflow_id,
        description=description,
        input_schema=input_schema,
        start_node=start_node,
        nodes=ordered_nodes,
        definition_fingerprint=fingerprint,
    )


def workflow_to_dict(workflow: Workflow) -> dict[str, Any]:
    """Return a mutable JSON-compatible canonical definition."""

    return {
        "id": workflow.id,
        "description": workflow.description,
        "input_schema": _thaw(workflow.input_schema),
        "start_node": workflow.start_node,
        "nodes": [_node_to_dict(node) for node in workflow.nodes],
    }


def validate_instance(
    schema: Mapping[str, Any],
    value: Any,
    *,
    context: str = "workflow value",
) -> None:
    """Validate one runtime value with the fixed resource limits."""

    _ensure_json_value(value, context)
    if _json_depth(value) > MAX_INSTANCE_DEPTH:
        raise WorkflowInstanceError(f"{context} exceeds maximum JSON depth {MAX_INSTANCE_DEPTH}")
    encoded = _canonical_json(value).encode("utf-8")
    if len(encoded) > MAX_INSTANCE_BYTES:
        raise WorkflowInstanceError(
            f"{context} exceeds maximum encoded size {MAX_INSTANCE_BYTES} bytes"
        )
    try:
        Draft202012Validator(_thaw(schema)).validate(value)
    except ValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path)
        location = f" at /{path}" if path else ""
        raise WorkflowInstanceError(f"{context}{location}: {exc.message}") from exc


def _parse_node(
    raw: Any,
    *,
    source: str,
    known_operations: set[str] | frozenset[str] | None,
    selectable_operations: set[str] | frozenset[str] | None,
    known_validators: set[str] | frozenset[str] | None,
    agent_tools: set[str] | frozenset[str] | None,
    schema_registry: SchemaRegistry | None,
) -> Node:
    data = _require_mapping(raw, source)
    execution = _nonempty_string(data.get("execution"), f"{source}.execution")
    if execution == "operation":
        allowed = _NODE_OPERATION_FIELDS
    elif execution == "autonomous":
        allowed = _NODE_AUTONOMOUS_FIELDS
    elif execution == "task_graph":
        allowed = _NODE_TASK_GRAPH_FIELDS
    else:
        raise WorkflowDefinitionError(
            f"{source}.execution must be 'operation', 'autonomous', or 'task_graph'"
        )
    _reject_unknown(data, allowed, source)

    node_id = _nonempty_string(data.get("id"), f"{source}.id")
    if node_id.startswith("$"):
        raise WorkflowDefinitionError(f"{source}.id cannot use the reserved '$' prefix")

    inputs = _normalize_inputs(data.get("inputs", {}), f"{source}.inputs")
    transitions = _normalize_transitions(data.get("transitions"), f"{source}.transitions")
    completion = _normalize_completion(
        data.get("completion", {}),
        f"{source}.completion",
        schema_registry=schema_registry,
        allow_schema_id=execution == "task_graph",
    )

    operation: str | None = None
    goal: str | None = None
    capability_tools: tuple[str, ...] | None = None
    max_steps: int | None = None
    task_graph: TaskGraphNodeConfig | None = None

    if execution == "operation":
        if completion[3] != "none":
            raise WorkflowDefinitionError(
                f"{source}.completion.review is supported only for autonomous Nodes"
            )
        operation = _nonempty_string(data.get("operation"), f"{source}.operation")
        if known_operations is not None and operation not in known_operations:
            raise WorkflowDefinitionError(
                f"{source}.operation references unknown operation {operation!r}"
            )
        if selectable_operations is not None and operation not in selectable_operations:
            raise WorkflowDefinitionError(
                f"{source}.operation {operation!r} is not selectable by Workflow nodes"
            )
        if "waiting" in transitions:
            raise WorkflowDefinitionError(
                f"{source}.transitions cannot declare 'waiting'; waiting does not transition"
            )
    elif execution == "autonomous":
        goal = _nonempty_string(data.get("goal"), f"{source}.goal")
        if completion[0] is None:
            raise WorkflowDefinitionError(
                f"{source}.completion requires output_schema"
            )
        if set(transitions) - _AUTONOMOUS_TRANSITIONS:
            unknown = sorted(set(transitions) - _AUTONOMOUS_TRANSITIONS)
            raise WorkflowDefinitionError(
                f"{source}.transitions has unsupported autonomous event(s): {', '.join(unknown)}"
            )
        if "completed" not in transitions:
            raise WorkflowDefinitionError(f"{source}.transitions must declare 'completed'")
        capability_tools = _normalize_capabilities(
            data.get("capabilities"),
            f"{source}.capabilities",
            agent_tools=agent_tools,
        )
        max_steps = _normalize_limits(data.get("limits"), f"{source}.limits")
    else:
        _require_fields(data, _TASK_GRAPH_REQUIRED_FIELDS | _NODE_COMMON_FIELDS, source)
        if completion[3] != "none":
            raise WorkflowDefinitionError(
                f"{source}.completion.review is not supported for task_graph Nodes"
            )
        if completion[4] is None:
            raise WorkflowDefinitionError(
                f"{source}.completion requires output_schema_id for task_graph Nodes"
            )
        if completion[1] is None:
            raise WorkflowDefinitionError(
                f"{source}.completion requires validator for task_graph Nodes"
            )
        unsupported = sorted(set(transitions) - _TASK_GRAPH_TRANSITIONS)
        if unsupported:
            raise WorkflowDefinitionError(
                f"{source}.transitions has unsupported task_graph event(s): "
                f"{', '.join(unsupported)}"
            )
        missing = sorted(_TASK_GRAPH_TRANSITIONS - set(transitions))
        if missing:
            raise WorkflowDefinitionError(
                f"{source}.transitions must declare task_graph event(s): {', '.join(missing)}"
            )
        if transitions["waiting"] != WORKFLOW_WAIT:
            raise WorkflowDefinitionError(
                f"{source}.transitions.waiting must target {WORKFLOW_WAIT}"
            )
        task_graph = _normalize_task_graph_config(
            data,
            source,
            known_operations=known_operations,
            selectable_operations=selectable_operations,
        )

    validator = completion[1]
    if validator is not None and known_validators is not None and validator not in known_validators:
        raise WorkflowDefinitionError(
            f"{source}.completion.validator references unknown validator {validator!r}"
        )

    return Node(
        id=node_id,
        execution=execution,  # type: ignore[arg-type]
        inputs=inputs,
        completion_output_schema=completion[0],
        completion_validator=validator,
        completion_required=completion[2],
        completion_review=completion[3],
        transitions=transitions,
        operation=operation,
        goal=goal,
        capability_tools=capability_tools,
        max_steps=max_steps,
        completion_output_schema_id=completion[4].id if completion[4] is not None else None,
        completion_output_schema_version=(
            completion[4].version if completion[4] is not None else None
        ),
        completion_output_schema_fingerprint=(
            completion[4].fingerprint if completion[4] is not None else None
        ),
        task_graph=task_graph,
    )


def _normalize_completion(
    raw: Any,
    source: str,
    *,
    schema_registry: SchemaRegistry | None,
    allow_schema_id: bool,
) -> tuple[
    Mapping[str, Any] | None,
    str | None,
    tuple[str, ...],
    CompletionReview,
    SchemaDefinition | None,
]:
    data = _require_mapping(raw, source)
    _reject_unknown(data, _COMPLETION_FIELDS, source)

    required = _string_list(data.get("require", []), f"{source}.require")
    validator_raw = data.get("validator")
    validator = (
        None if validator_raw is None else _nonempty_string(validator_raw, f"{source}.validator")
    )
    review = str(data.get("review") or "none").strip()
    if review not in {"none", "required"}:
        raise WorkflowDefinitionError(f"{source}.review must be 'none' or 'required'")

    schema_raw = data.get("output_schema")
    schema_id_raw = data.get("output_schema_id")
    if schema_raw is not None and schema_id_raw is not None:
        raise WorkflowDefinitionError(
            f"{source} cannot declare both output_schema and output_schema_id"
        )
    definition: SchemaDefinition | None = None
    if schema_id_raw is not None:
        if not allow_schema_id:
            raise WorkflowDefinitionError(
                f"{source}.output_schema_id is supported only for task_graph Nodes"
            )
        schema_id = _nonempty_string(schema_id_raw, f"{source}.output_schema_id")
        if schema_registry is None:
            raise WorkflowDefinitionError(
                f"{source}.output_schema_id requires schema_registry"
            )
        try:
            definition = schema_registry.resolve(schema_id)
        except SchemaRegistryError as exc:
            raise WorkflowDefinitionError(f"{source}: {exc}") from exc
        schema_raw = _thaw(definition.schema)
    if schema_raw is None and required:
        schema_raw = {"type": "object", "required": list(required)}
    elif schema_raw is not None and required:
        schema_mapping = dict(_require_mapping(schema_raw, f"{source}.output_schema"))
        if schema_mapping.get("type") != "object":
            raise WorkflowDefinitionError(f"{source}.require needs output_schema.type == 'object'")
        declared = _string_list(
            schema_mapping.get("required", []),
            f"{source}.output_schema.required",
        )
        schema_mapping["required"] = sorted(set(declared) | set(required))
        schema_raw = schema_mapping

    schema = (
        None if schema_raw is None else _normalize_schema(schema_raw, f"{source}.output_schema")
    )
    return schema, validator, required, cast(CompletionReview, review), definition


def _normalize_capabilities(
    raw: Any,
    source: str,
    *,
    agent_tools: set[str] | frozenset[str] | None,
) -> tuple[str, ...] | None:
    if raw is None:
        return None
    data = _require_mapping(raw, source)
    _reject_unknown(data, _CAPABILITY_FIELDS, source)
    _require_fields(data, _CAPABILITY_FIELDS, source)
    tools = _string_list(data["tools"], f"{source}.tools")
    if agent_tools is not None:
        widened = sorted(set(tools) - set(agent_tools))
        if widened:
            raise WorkflowDefinitionError(
                f"{source}.tools widens Agent capabilities: {', '.join(widened)}"
            )
    return tools


def _normalize_limits(raw: Any, source: str) -> int | None:
    if raw is None:
        return None
    data = _require_mapping(raw, source)
    _reject_unknown(data, _LIMIT_FIELDS, source)
    _require_fields(data, _LIMIT_FIELDS, source)
    value = data["max_steps"]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkflowDefinitionError(f"{source}.max_steps must be a positive integer")
    return cast(int, value)


def _normalize_task_graph_config(
    data: Mapping[str, Any],
    source: str,
    *,
    known_operations: set[str] | frozenset[str] | None,
    selectable_operations: set[str] | frozenset[str] | None,
) -> TaskGraphNodeConfig:
    operation_adapters = _sorted_string_list(
        data["operation_adapters"], f"{source}.operation_adapters"
    )
    if known_operations is not None:
        unknown = sorted(set(operation_adapters) - set(known_operations))
        if unknown:
            raise WorkflowDefinitionError(
                f"{source}.operation_adapters references unknown operation(s): "
                f"{', '.join(unknown)}"
            )
    if selectable_operations is not None:
        unavailable = sorted(set(operation_adapters) - set(selectable_operations))
        if unavailable:
            raise WorkflowDefinitionError(
                f"{source}.operation_adapters contains non-selectable operation(s): "
                f"{', '.join(unavailable)}"
            )
    child_templates = _sorted_string_list(
        data["child_templates"], f"{source}.child_templates"
    )
    return TaskGraphNodeConfig(
        planner=_nonempty_string(data["planner"], f"{source}.planner"),
        graph_policy=_nonempty_string(data["graph_policy"], f"{source}.graph_policy"),
        context_builder=_nonempty_string(
            data["context_builder"], f"{source}.context_builder"
        ),
        task_validators=_sorted_string_list(
            data["task_validators"], f"{source}.task_validators"
        ),
        group_validators=_sorted_string_list(
            data["group_validators"], f"{source}.group_validators"
        ),
        criterion_validators=_sorted_string_list(
            data["criterion_validators"], f"{source}.criterion_validators"
        ),
        goal_verifier=_nonempty_string(data["goal_verifier"], f"{source}.goal_verifier"),
        operation_adapters=operation_adapters,
        parent_inline_components=_sorted_string_list(
            data["parent_inline_components"], f"{source}.parent_inline_components"
        ),
        human_task_contracts=_sorted_string_list(
            data["human_task_contracts"], f"{source}.human_task_contracts"
        ),
        child_templates=child_templates,
        limits=_normalize_task_graph_limits(
            data["limits"],
            f"{source}.limits",
            child_templates=frozenset(child_templates),
        ),
    )


def _normalize_task_graph_limits(
    raw: Any,
    source: str,
    *,
    child_templates: frozenset[str],
) -> TaskGraphLimits:
    data = _require_mapping(raw, source)
    _reject_unknown(data, _TASK_GRAPH_LIMIT_FIELDS, source)
    _require_fields(data, _TASK_GRAPH_REQUIRED_LIMIT_FIELDS, source)
    values: dict[str, int] = {}
    for field_name in sorted(_TASK_GRAPH_REQUIRED_LIMIT_FIELDS):
        value = data[field_name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise WorkflowDefinitionError(
                f"{source}.{field_name} must be a positive integer"
            )
        values[field_name] = value
    raw_template_limits = data.get("template_concurrency_limits", {})
    template_limits = _require_mapping(
        raw_template_limits,
        f"{source}.template_concurrency_limits",
    )
    normalized_limits: list[tuple[str, int]] = []
    for raw_template_id, raw_limit in template_limits.items():
        template_id = _nonempty_string(
            raw_template_id,
            f"{source}.template_concurrency_limits key",
        )
        if template_id not in child_templates:
            raise WorkflowDefinitionError(
                f"{source}.template_concurrency_limits references unknown child "
                f"template {template_id!r}"
            )
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit <= 0:
            raise WorkflowDefinitionError(
                f"{source}.template_concurrency_limits.{template_id} must be a "
                "positive integer"
            )
        normalized_limits.append((template_id, raw_limit))
    return TaskGraphLimits(
        **values,
        template_concurrency_limits=tuple(sorted(normalized_limits)),
    )


def _normalize_inputs(raw: Any, source: str) -> Mapping[str, Any]:
    data = _require_mapping(raw, source)
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            raise WorkflowDefinitionError(f"{source} keys must be non-empty strings")
        _ensure_json_value(value, f"{source}.{key}")
        if isinstance(value, Mapping) and set(value) == {"$ref"}:
            _validate_input_ref(value["$ref"], f"{source}.{key}.$ref")
        normalized[key] = _freeze(value)
    return MappingProxyType(dict(sorted(normalized.items())))


def _normalize_transitions(raw: Any, source: str) -> Mapping[str, str]:
    data = _require_mapping(raw, source)
    if not data:
        raise WorkflowDefinitionError(f"{source} must be a non-empty mapping")
    normalized: dict[str, str] = {}
    for event, target in data.items():
        event_name = _nonempty_string(event, f"{source} event")
        normalized[event_name] = _nonempty_string(target, f"{source}.{event_name}")
    if normalized.get("failed") == WORKFLOW_COMPLETE:
        raise WorkflowDefinitionError(f"{source}.failed cannot target {WORKFLOW_COMPLETE}")
    return MappingProxyType(dict(sorted(normalized.items())))


def _normalize_schema(raw: Any, source: str) -> Mapping[str, Any]:
    data = dict(_require_mapping(raw, source))
    _ensure_json_value(data, source)
    if _json_depth(data) > MAX_SCHEMA_DEPTH:
        raise WorkflowDefinitionError(
            f"{source} exceeds maximum JSON Schema depth {MAX_SCHEMA_DEPTH}"
        )
    encoded = _canonical_json(data).encode("utf-8")
    if len(encoded) > MAX_SCHEMA_BYTES:
        raise WorkflowDefinitionError(
            f"{source} exceeds maximum JSON Schema size {MAX_SCHEMA_BYTES} bytes"
        )
    _validate_schema_keywords(data, source)
    try:
        Draft202012Validator.check_schema(data)
    except SchemaError as exc:
        raise WorkflowDefinitionError(f"{source}: invalid JSON Schema: {exc.message}") from exc
    _validate_ref_cycles(data, source)
    return cast(Mapping[str, Any], _freeze(data))


def _validate_schema_keywords(value: Any, source: str) -> None:
    if not isinstance(value, Mapping):
        return
    if "format" in value:
        raise WorkflowDefinitionError(f"{source}: JSON Schema 'format' is not supported")
    if "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not (ref == "#" or ref.startswith("#/")):
            raise WorkflowDefinitionError(
                f"{source}: JSON Schema $ref must be a local JSON Pointer"
            )
    for item in _iter_subschemas(value):
        _validate_schema_keywords(item, source)


def _validate_ref_cycles(schema: Mapping[str, Any], source: str) -> None:
    def walk(value: Any, stack: tuple[str, ...]) -> None:
        if not isinstance(value, Mapping):
            return
        ref = value.get("$ref")
        if isinstance(ref, str):
            if ref in stack:
                chain = " -> ".join((*stack, ref))
                raise WorkflowDefinitionError(
                    f"{source}: recursive JSON Schema $ref is not supported: {chain}"
                )
            target = _resolve_local_ref(schema, ref, source)
            walk(target, (*stack, ref))
        for item in _iter_subschemas(value):
            walk(item, stack)

    walk(schema, ())


def _iter_subschemas(schema: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    for key in _SINGLE_SUBSCHEMA_KEYS:
        value = schema.get(key)
        if isinstance(value, Mapping):
            yield value
    for key in _ARRAY_SUBSCHEMA_KEYS:
        value = schema.get(key)
        if isinstance(value, list):
            yield from (item for item in value if isinstance(item, Mapping))
    for key in _MAPPING_SUBSCHEMA_KEYS:
        value = schema.get(key)
        if isinstance(value, Mapping):
            yield from (item for item in value.values() if isinstance(item, Mapping))


def _resolve_local_ref(schema: Mapping[str, Any], ref: str, source: str) -> Any:
    if ref == "#":
        return schema
    current: Any = schema
    for raw_part in ref[2:].split("/"):
        if "~" in raw_part:
            index = 0
            while index < len(raw_part):
                if raw_part[index] == "~" and (
                    index + 1 >= len(raw_part) or raw_part[index + 1] not in "01"
                ):
                    raise WorkflowDefinitionError(
                        f"{source}: invalid JSON Pointer escape in $ref {ref!r}"
                    )
                index += 2 if raw_part[index] == "~" else 1
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise WorkflowDefinitionError(f"{source}: unresolved JSON Schema $ref {ref!r}")
    return current


def _validate_input_ref(value: Any, source: str) -> None:
    ref = _nonempty_string(value, source)
    if ref == "#/workflow/input" or ref.startswith("#/workflow/input/"):
        return
    if ref.startswith("#/nodes/") and "/output" in ref:
        parts = ref.split("/")
        if len(parts) >= 4 and parts[2] and parts[3] == "output":
            return
    raise WorkflowDefinitionError(
        f"{source} must reference #/workflow/input or #/nodes/<id>/output"
    )


def _validate_node_references(
    node: Node,
    known_node_ids: frozenset[str],
    source: str,
) -> None:
    for event, target in node.transitions.items():
        if target == WORKFLOW_WAIT:
            if node.execution == "task_graph" and event == "waiting":
                continue
        if target not in known_node_ids and target not in WORKFLOW_TERMINALS:
            raise WorkflowDefinitionError(
                f"{source} node {node.id!r} transition {event!r} "
                f"references unknown target {target!r}"
            )
    for value in node.inputs.values():
        if isinstance(value, Mapping) and set(value) == {"$ref"}:
            ref = value["$ref"]
            if isinstance(ref, str) and ref.startswith("#/nodes/"):
                referenced = ref.split("/")[2]
                if referenced not in known_node_ids:
                    raise WorkflowDefinitionError(
                        f"{source} node {node.id!r} input references unknown node {referenced!r}"
                    )
                if referenced == node.id:
                    raise WorkflowDefinitionError(
                        f"{source} node {node.id!r} cannot bind its own uncommitted output"
                    )


def _validate_reachability(nodes: Sequence[Node], start_node: str, source: str) -> None:
    by_id = {node.id: node for node in nodes}
    reachable: set[str] = set()
    pending = [start_node]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        for target in by_id[current].transitions.values():
            if target in by_id and target not in reachable:
                pending.append(target)
    unreachable = sorted(set(by_id) - reachable)
    if unreachable:
        raise WorkflowDefinitionError(
            f"{source}.nodes contains unreachable node(s): {', '.join(unreachable)}"
        )


def _node_to_dict(node: Node) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": node.id,
        "execution": node.execution,
        "inputs": _thaw(node.inputs),
        "completion": {},
        "transitions": dict(node.transitions),
    }
    completion: dict[str, Any] = {}
    if node.completion_output_schema_id is not None:
        completion["output_schema_id"] = node.completion_output_schema_id
    elif node.completion_output_schema is not None:
        completion["output_schema"] = _thaw(node.completion_output_schema)
    if node.completion_validator is not None:
        completion["validator"] = node.completion_validator
    if node.completion_required:
        completion["require"] = list(node.completion_required)
    if node.completion_review != "none":
        completion["review"] = node.completion_review
    result["completion"] = completion
    if node.execution == "operation":
        result["operation"] = node.operation
    elif node.execution == "autonomous":
        result["goal"] = node.goal
        if node.capability_tools is not None:
            result["capabilities"] = {"tools": list(node.capability_tools)}
        if node.max_steps is not None:
            result["limits"] = {"max_steps": node.max_steps}
    else:
        if node.task_graph is None:
            raise WorkflowDefinitionError(f"task_graph Node {node.id!r} has no config")
        config = node.task_graph
        result.update(
            {
                "planner": config.planner,
                "graph_policy": config.graph_policy,
                "context_builder": config.context_builder,
                "task_validators": list(config.task_validators),
                "group_validators": list(config.group_validators),
                "criterion_validators": list(config.criterion_validators),
                "goal_verifier": config.goal_verifier,
                "operation_adapters": list(config.operation_adapters),
                "parent_inline_components": list(config.parent_inline_components),
                "human_task_contracts": list(config.human_task_contracts),
                "child_templates": list(config.child_templates),
                "limits": {
                    "max_tasks": config.limits.max_tasks,
                    "max_graph_depth": config.limits.max_graph_depth,
                    "max_replans": config.limits.max_replans,
                    "max_concurrency": config.limits.max_concurrency,
                    "max_child_runs": config.limits.max_child_runs,
                    **(
                        {
                            "template_concurrency_limits": dict(
                                config.limits.template_concurrency_limits
                            )
                        }
                        if config.limits.template_concurrency_limits
                        else {}
                    ),
                },
            }
        )
    return result


def _node_to_fingerprint_dict(node: Node) -> dict[str, Any]:
    result = _node_to_dict(node)
    if node.completion_output_schema_id is not None:
        result["completion"]["resolved_output_schema"] = {
            "id": node.completion_output_schema_id,
            "version": node.completion_output_schema_version,
            "fingerprint": node.completion_output_schema_fingerprint,
            "schema": _thaw(node.completion_output_schema),
        }
    return result


def _require_mapping(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowDefinitionError(f"{source} must be a mapping")
    for key in value:
        if not isinstance(key, str):
            raise WorkflowDefinitionError(f"{source} keys must be strings")
    return value


def _require_fields(data: Mapping[str, Any], fields: frozenset[str], source: str) -> None:
    missing = sorted(fields - set(data))
    if missing:
        raise WorkflowDefinitionError(f"{source} missing required field(s): {', '.join(missing)}")


def _reject_unknown(data: Mapping[str, Any], allowed: frozenset[str], source: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise WorkflowDefinitionError(f"{source} has unknown field(s): {', '.join(unknown)}")


def _nonempty_string(value: Any, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDefinitionError(f"{source} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, source: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkflowDefinitionError(f"{source} must be a list")
    normalized = tuple(_nonempty_string(item, source) for item in value)
    if len(normalized) != len(set(normalized)):
        raise WorkflowDefinitionError(f"{source} cannot contain duplicates")
    return normalized


def _sorted_string_list(value: Any, source: str) -> tuple[str, ...]:
    return tuple(sorted(_string_list(value, source)))


def _ensure_json_value(value: Any, source: str) -> None:
    if value is None or isinstance(value, str | bool | int | float):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkflowDefinitionError(f"{source} JSON object keys must be strings")
            _ensure_json_value(item, source)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _ensure_json_value(item, source)
        return
    raise WorkflowDefinitionError(
        f"{source} must contain only JSON-compatible values, got {type(value).__name__}"
    )


def _json_depth(value: Any) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list | tuple):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _thaw(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


__all__ = [
    "MAX_INSTANCE_BYTES",
    "MAX_INSTANCE_DEPTH",
    "MAX_SCHEMA_BYTES",
    "MAX_SCHEMA_DEPTH",
    "WorkflowDefinitionError",
    "WorkflowInstanceError",
    "parse_workflow",
    "parse_workflow_yaml",
    "validate_instance",
    "workflow_to_dict",
]
