"""Static child Workflow template declarations and pinned snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .._utils import canonical_json, compute_fingerprint


class ChildTemplateError(ValueError):
    """A child template declaration or pinned resolution is invalid."""


@dataclass(frozen=True, slots=True)
class ChildTemplateLimits:
    max_steps: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        for field_name in ("max_steps", "timeout_seconds"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ChildTemplateError(
                    f"child template limit {field_name} must be a positive integer"
                )


@dataclass(frozen=True, slots=True)
class ChildTemplateRef:
    id: str
    agent_name: str
    workflow_id: str
    limits: ChildTemplateLimits

    def __post_init__(self) -> None:
        for field_name in ("id", "agent_name", "workflow_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ChildTemplateError(f"child template {field_name} must be non-empty")
            object.__setattr__(self, field_name, value.strip())

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "workflow_id": self.workflow_id,
            "limits": {
                "max_steps": self.limits.max_steps,
                "timeout_seconds": self.limits.timeout_seconds,
            },
        }


@dataclass(frozen=True, slots=True)
class PinnedChildTemplate:
    id: str
    snapshot: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ChildTemplateError("pinned child template id must be non-empty")
        normalized = _freeze(self.snapshot)
        if not isinstance(normalized, Mapping):
            raise ChildTemplateError("pinned child template snapshot must be a mapping")
        expected = compute_fingerprint(_thaw(normalized))
        if self.fingerprint != expected:
            raise ChildTemplateError("pinned child template fingerprint does not match snapshot")
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "snapshot", normalized)

    @classmethod
    def from_snapshot(cls, template_id: str, snapshot: Mapping[str, Any]) -> PinnedChildTemplate:
        payload = _thaw(snapshot)
        return cls(
            id=template_id,
            snapshot=payload,
            fingerprint=compute_fingerprint(payload),
        )


@dataclass(frozen=True, slots=True)
class ResolvedChildTemplate:
    pinned: PinnedChildTemplate
    agent: Any
    workflow: Any
    execution_contract: Any


class PinnedChildTemplateRegistry:
    """Closed registry of fully resolved child execution templates."""

    def __init__(self) -> None:
        self._templates: dict[str, PinnedChildTemplate] = {}
        self._executables: dict[str, ResolvedChildTemplate] = {}

    def register(
        self,
        template: PinnedChildTemplate,
        executable: ResolvedChildTemplate | None = None,
    ) -> None:
        if template.id in self._templates:
            raise ChildTemplateError(f"duplicate child template {template.id!r}")
        if executable is not None and executable.pinned.fingerprint != template.fingerprint:
            raise ChildTemplateError("child executable binding does not match pinned template")
        self._templates[template.id] = template
        if executable is not None:
            self._executables[template.id] = executable

    def resolve(self, template_id: str) -> PinnedChildTemplate:
        try:
            return self._templates[template_id]
        except KeyError as exc:
            raise ChildTemplateError(f"unknown child template {template_id!r}") from exc

    def ids(self) -> frozenset[str]:
        return frozenset(self._templates)

    def resolve_pinned(self, snapshot: Mapping[str, Any]) -> PinnedChildTemplate:
        template_id = snapshot.get("id")
        fingerprint = snapshot.get("fingerprint")
        definition = snapshot.get("definition")
        if (
            not isinstance(template_id, str)
            or not isinstance(fingerprint, str)
            or not isinstance(definition, Mapping)
        ):
            raise ChildTemplateError("pinned child template snapshot is malformed")
        current = self.resolve(template_id)
        if current.fingerprint != fingerprint or _thaw(current.snapshot) != _thaw(definition):
            raise ChildTemplateError(
                f"pinned child template {template_id!r} is unavailable or changed"
            )
        return current

    def resolve_executable(self, snapshot: Mapping[str, Any]) -> ResolvedChildTemplate:
        pinned = self.resolve_pinned(snapshot)
        try:
            executable = self._executables[pinned.id]
        except KeyError as exc:
            raise ChildTemplateError(
                f"pinned child template {pinned.id!r} has no executable binding"
            ) from exc
        if executable.pinned.fingerprint != pinned.fingerprint:
            raise ChildTemplateError("child executable binding changed after resolution")
        return executable


def resolve_child_template_registry(
    *,
    parent_agent: Any,
    agents: Mapping[str, Any],
    adapters: Any,
    parent_capability_ceiling: set[str],
    visible_adapter_ids: Mapping[str, set[str]] | None = None,
) -> PinnedChildTemplateRegistry:
    """Resolve every static child reference into an executable pinned snapshot."""

    from ..workflow.contract import CompletionValidatorRegistry, build_execution_contract
    from ..workflow.definition import workflow_to_dict

    registry = PinnedChildTemplateRegistry()
    for template in parent_agent.child_templates:
        child_agent = agents.get(template.agent_name)
        if child_agent is None:
            raise ChildTemplateError(f"unknown child Agent {template.agent_name!r}")
        child_workflow = next(
            (
                workflow
                for workflow in child_agent.workflows
                if workflow.id == template.workflow_id
            ),
            None,
        )
        if child_workflow is None:
            raise ChildTemplateError(
                f"unknown child Workflow {template.workflow_id!r} "
                f"for Agent {template.agent_name!r}"
            )
        if any(node.execution == "task_graph" for node in child_workflow.nodes):
            raise ChildTemplateError(
                f"child Workflow {template.workflow_id!r} contains task_graph; "
                "child recursion is not supported in V1"
            )
        child_adapters = (
            set(visible_adapter_ids.get(child_agent.name, ()))
            if visible_adapter_ids is not None
            else _agent_adapter_ids(child_agent)
        )
        selected_adapters, required_capabilities = _workflow_authority(
            child_workflow,
            adapters,
        )
        unbound = selected_adapters - child_adapters
        if unbound:
            raise ChildTemplateError(
                f"child Workflow {template.workflow_id!r} uses unavailable adapters: "
                f"{', '.join(sorted(unbound))}"
            )
        expansion = required_capabilities - parent_capability_ceiling
        if expansion:
            raise ChildTemplateError(
                f"child template {template.id!r} expands root authority: "
                f"{', '.join(sorted(expansion))}"
            )
        effective_capabilities = required_capabilities & parent_capability_ceiling
        validators = CompletionValidatorRegistry()
        for validator in child_agent.completion_validators:
            validators.register(validator)
        child_contract = build_execution_contract(
            workflow=child_workflow,
            adapters=adapters,
            validators=validators,
            output_contract=child_agent.output_contract or {"free_form": True},
            capability_ceiling=effective_capabilities,
            limits={
                "max_transitions": max(1, len(child_workflow.nodes) * 4),
                "max_steps": template.limits.max_steps,
                "timeout_seconds": template.limits.timeout_seconds,
            },
            protocol_version="workflow-v1",
        )
        agent_snapshot = _agent_snapshot(child_agent)
        permission_snapshot = _permission_profiles_snapshot(
            parent_agent.permission_profile,
            child_agent.permission_profile,
        )
        payload = {
            "template": template.snapshot(),
            "child_agent": {
                "definition": agent_snapshot,
                "fingerprint": compute_fingerprint(agent_snapshot),
            },
            "child_workflow": {
                "definition": workflow_to_dict(child_workflow),
                "fingerprint": child_workflow.definition_fingerprint,
            },
            "authority": {
                "parent_capability_ceiling": sorted(parent_capability_ceiling),
                "child_visible_adapters": sorted(child_adapters),
                "workflow_adapters": sorted(selected_adapters),
                "workflow_required_capabilities": sorted(required_capabilities),
                "effective_capability_ceiling": sorted(effective_capabilities),
                "permission_profile": permission_snapshot,
                "safety_constraints": sorted(
                    set(parent_agent.safety_constraints) | set(child_agent.safety_constraints)
                ),
            },
            "child_execution_contract": {
                "snapshot": _thaw(child_contract.snapshot),
                "fingerprint": child_contract.fingerprint,
            },
        }
        try:
            canonical_json(payload)
        except TypeError as exc:
            raise ChildTemplateError(
                f"child template {template.id!r} snapshot is not JSON serializable"
            ) from exc
        pinned = PinnedChildTemplate.from_snapshot(template.id, payload)
        registry.register(
            pinned,
            ResolvedChildTemplate(
                pinned=pinned,
                agent=child_agent,
                workflow=child_workflow,
                execution_contract=child_contract,
            ),
        )
    return registry


def _agent_adapter_ids(agent: Any) -> set[str]:
    declared = {str(binding.spec["name"]) for binding in agent.tools}
    declared.update(str(item) for item in agent.metadata.get("_declared_tools", ()))
    declared.update(str(item) for item in agent.metadata.get("_frontmatter_tools", ()))
    return declared


def _workflow_authority(workflow: Any, adapters: Any) -> tuple[set[str], set[str]]:
    selected: set[str] = set()
    for node in workflow.nodes:
        if node.execution == "operation" and node.operation is not None:
            selected.add(node.operation)
        elif node.execution == "autonomous":
            selected.update(node.capability_tools or ())
    required: set[str] = set()
    for adapter_id in selected:
        try:
            adapter = adapters.resolve_node_adapter(adapter_id)
        except ValueError as exc:
            raise ChildTemplateError(str(exc)) from exc
        required.update(adapter.required_capabilities)
    return selected, required


def _agent_snapshot(agent: Any) -> dict[str, Any]:
    model = None
    if agent.model_override is not None:
        model = {
            "provider": agent.model_override.provider,
            "name": agent.model_override.name,
            "base_url": agent.model_override.base_url,
            "api_key_configured": agent.model_override.api_key is not None,
            "extra": _snapshot_value(agent.model_override.extra, redact_secrets=True),
        }
    metadata = {
        str(key): value
        for key, value in agent.metadata.items()
        if str(key) != "package"
    }
    return {
        "name": agent.name,
        "description": agent.description,
        "instruction": agent.instruction,
        "workflows": [
            _workflow_snapshot(workflow)
            for workflow in sorted(agent.workflows, key=lambda item: item.id)
        ],
        "tools": [
            _snapshot_value(binding.spec)
            for binding in sorted(agent.tools, key=lambda item: item.spec["name"])
        ],
        "declared_tools": sorted(_agent_adapter_ids(agent)),
        "skills": [
            {"name": skill.name, "profile": _snapshot_value(skill.profile)}
            for skill in sorted(agent.skills, key=lambda item: item.name)
        ],
        "output_contract": _snapshot_value(agent.output_contract),
        "permission_profile": _snapshot_value(agent.permission_profile),
        "safety_constraints": list(agent.safety_constraints),
        "model_override": model,
        "metadata": _snapshot_value(metadata, redact_secrets=True),
        "task_protocol": {
            "mode": agent.task_protocol.mode,
            "review": agent.task_protocol.review,
            "min_items": agent.task_protocol.min_items,
            "max_items": agent.task_protocol.max_items,
        },
        "interaction_protocol": {"startup": agent.interaction_protocol.startup},
    }


def _workflow_snapshot(workflow: Any) -> dict[str, Any]:
    from ..workflow.definition import workflow_to_dict

    return workflow_to_dict(workflow)


def _permission_profiles_snapshot(parent: Any, child: Any) -> dict[str, Any]:
    parent_value = _normalized_permission_profile(parent)
    child_value = _normalized_permission_profile(child)
    parent_mode = parent_value["mode"]
    child_mode = child_value["mode"]
    mode = None
    if parent_mode is not None and child_mode is not None:
        mode_order = {"trust": 0, "auto": 1, "preview": 2}
        mode = max((parent_mode, child_mode), key=lambda item: mode_order[item])
    return {
        "parent": parent_value,
        "child": child_value,
        "static_intersection": {
            "mode": mode,
            "preauthorized": sorted(
                set(parent_value["preauthorized"]) & set(child_value["preauthorized"])
            ),
            "deny": sorted(set(parent_value["deny"]) | set(child_value["deny"])),
            "review_required": sorted(
                set(parent_value["review_required"])
                | set(child_value["review_required"])
            ),
        },
    }


def _normalized_permission_profile(value: Any) -> dict[str, Any]:
    raw = value or {
        "mode": None,
        "preauthorized": [],
        "deny": [],
        "review_required": [],
    }
    return {
        "mode": raw.get("mode"),
        "preauthorized": sorted(set(raw.get("preauthorized") or ())),
        "deny": sorted(set(raw.get("deny") or ())),
        "review_required": sorted(set(raw.get("review_required") or ())),
    }


_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)


def _snapshot_value(
    value: Any,
    *,
    field_name: str | None = None,
    redact_secrets: bool = False,
) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _snapshot_value(
            asdict(value),
            field_name=field_name,
            redact_secrets=redact_secrets,
        )
    if hasattr(value, "model_dump"):
        return _snapshot_value(
            value.model_dump(),
            field_name=field_name,
            redact_secrets=redact_secrets,
        )
    if isinstance(value, Mapping):
        snapshot: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key)
            lowered = normalized_key.lower()
            secret_field = lowered in _SECRET_FIELD_NAMES or lowered.endswith(
                ("_api_key", "_credential", "_password", "_secret", "_token")
            )
            if redact_secrets and secret_field and not isinstance(item, Mapping):
                snapshot[normalized_key] = "[redacted]"
                continue
            snapshot[normalized_key] = _snapshot_value(
                item,
                field_name=normalized_key,
                redact_secrets=redact_secrets,
            )
        return snapshot
    if isinstance(value, list | tuple):
        return [
            _snapshot_value(
                item,
                field_name=field_name,
                redact_secrets=redact_secrets,
            )
            for item in value
        ]
    if isinstance(value, set | frozenset):
        normalized = [
            _snapshot_value(
                item,
                field_name=field_name,
                redact_secrets=redact_secrets,
            )
            for item in value
        ]
        return sorted(normalized, key=lambda item: compute_fingerprint(item))
    if isinstance(value, Path):
        return value.name
    if field_name in {"path", "source_path"} and isinstance(value, str):
        return Path(value).name
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ChildTemplateError(
        f"unsupported child template snapshot value {type(value).__name__}"
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "ChildTemplateError",
    "ChildTemplateLimits",
    "ChildTemplateRef",
    "PinnedChildTemplate",
    "PinnedChildTemplateRegistry",
    "ResolvedChildTemplate",
    "resolve_child_template_registry",
]
