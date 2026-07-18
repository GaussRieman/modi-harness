"""Immutable, content-addressed child execution context manifests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from .._utils import compute_fingerprint
from .types import DependencyRef, IntentVersion, TaskRun


class ContextManifestError(ValueError):
    """A child context would be incomplete, mutable, or over-authorized."""


@dataclass(frozen=True, slots=True)
class DependencyContext:
    ref: DependencyRef
    result_summary: str
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.result_summary.strip():
            raise ContextManifestError("dependency result_summary must be non-empty")
        object.__setattr__(self, "result_summary", self.result_summary.strip())
        object.__setattr__(self, "artifact_refs", _normalized_refs(self.artifact_refs))
        object.__setattr__(self, "evidence_refs", _normalized_refs(self.evidence_refs))


@dataclass(frozen=True, slots=True)
class ManifestAuthority:
    adapters: tuple[str, ...]
    capabilities: tuple[str, ...]
    readable_scopes: tuple[str, ...]
    writable_scopes: tuple[str, ...]
    permission_profile: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapters", _normalized_refs(self.adapters))
        object.__setattr__(self, "capabilities", _normalized_refs(self.capabilities))
        object.__setattr__(self, "readable_scopes", _normalized_refs(self.readable_scopes))
        object.__setattr__(self, "writable_scopes", _normalized_refs(self.writable_scopes))
        object.__setattr__(
            self,
            "permission_profile",
            _freeze_mapping(_permission_profile(_plain(self.permission_profile))),
        )


@dataclass(frozen=True, slots=True)
class ManifestBudgets:
    max_steps: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        for field_name in ("max_steps", "timeout_seconds"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ContextManifestError(
                    f"ContextManifest budget {field_name} must be a positive integer"
                )


@dataclass(frozen=True, slots=True)
class ContextManifest:
    context_id: str
    root_run_id: str
    parent_run_id: str
    parent_node_id: str
    parent_node_attempt: int
    task_attempt_id: str
    child_run_id: str
    template_id: str
    template_fingerprint: str
    child_workflow_fingerprint: str
    child_execution_contract_fingerprint: str
    intent: Mapping[str, Any]
    task: Mapping[str, Any]
    dependencies: tuple[DependencyContext, ...]
    inputs: Mapping[str, Any]
    authority: ManifestAuthority
    budgets: ManifestBudgets
    fingerprint: str = ""
    schema_version: str = "context-manifest-v1"

    def __post_init__(self) -> None:
        for field_name in (
            "context_id",
            "root_run_id",
            "parent_run_id",
            "parent_node_id",
            "task_attempt_id",
            "child_run_id",
            "template_id",
            "template_fingerprint",
            "child_workflow_fingerprint",
            "child_execution_contract_fingerprint",
            "schema_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ContextManifestError(f"ContextManifest {field_name} must be non-empty")
            object.__setattr__(self, field_name, value.strip())
        if self.context_id != f"context/{self.task_attempt_id}":
            raise ContextManifestError("ContextManifest context_id must bind the Task Attempt")
        if self.parent_run_id != self.root_run_id:
            raise ContextManifestError("V1 ContextManifest parent run must be the root run")
        if (
            not isinstance(self.parent_node_attempt, int)
            or isinstance(self.parent_node_attempt, bool)
            or self.parent_node_attempt < 1
        ):
            raise ContextManifestError("parent_node_attempt must be a positive integer")
        intent = _plain(self.intent)
        task = _plain(self.task)
        inputs = _plain(self.inputs)
        _require_exact_fields(
            intent,
            "intent",
            {
                "intent_id",
                "version",
                "binding_hash",
                "goal",
                "desired_outcome",
                "relevant_criteria",
            },
        )
        _require_exact_fields(
            task,
            "task",
            {
                "ref",
                "goal",
                "completion_contract",
                "constraints",
                "non_goals",
                "assumptions",
            },
        )
        _require_exact_fields(
            inputs,
            "inputs",
            {"artifact_refs", "evidence_refs", "memory_refs"},
        )
        normalized_inputs = {
            key: list(_input_refs(inputs[key], key))
            for key in ("artifact_refs", "evidence_refs", "memory_refs")
        }
        object.__setattr__(self, "intent", _freeze_mapping(intent))
        object.__setattr__(self, "task", _freeze_mapping(task))
        object.__setattr__(
            self,
            "dependencies",
            tuple(sorted(self.dependencies, key=lambda item: item.ref.key)),
        )
        if len({item.ref.key for item in self.dependencies}) != len(self.dependencies):
            raise ContextManifestError("ContextManifest dependency refs must be unique")
        object.__setattr__(self, "inputs", _freeze_mapping(normalized_inputs))
        expected = compute_fingerprint(self._payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ContextManifestError("ContextManifest fingerprint does not match content")
        object.__setattr__(self, "fingerprint", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "context_id": self.context_id,
            "root_run_id": self.root_run_id,
            "parent_run_id": self.parent_run_id,
            "parent_node_id": self.parent_node_id,
            "parent_node_attempt": self.parent_node_attempt,
            "task_attempt_id": self.task_attempt_id,
            "child_run_id": self.child_run_id,
            "template_id": self.template_id,
            "template_fingerprint": self.template_fingerprint,
            "child_workflow_fingerprint": self.child_workflow_fingerprint,
            "child_execution_contract_fingerprint": (self.child_execution_contract_fingerprint),
            "intent": _plain(self.intent),
            "task": _plain(self.task),
            "dependencies": [_plain(item) for item in self.dependencies],
            "inputs": _plain(self.inputs),
            "authority": _plain(self.authority),
            "budgets": _plain(self.budgets),
        }

    def snapshot(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}

    @classmethod
    def from_snapshot(cls, raw: Mapping[str, Any]) -> ContextManifest:
        _require_exact_fields(
            raw,
            "manifest",
            {
                "schema_version",
                "context_id",
                "root_run_id",
                "parent_run_id",
                "parent_node_id",
                "parent_node_attempt",
                "task_attempt_id",
                "child_run_id",
                "template_id",
                "template_fingerprint",
                "child_workflow_fingerprint",
                "child_execution_contract_fingerprint",
                "intent",
                "task",
                "dependencies",
                "inputs",
                "authority",
                "budgets",
                "fingerprint",
            },
        )
        authority = _mapping(raw.get("authority"), "authority")
        budgets = _mapping(raw.get("budgets"), "budgets")
        _require_exact_fields(
            authority,
            "authority",
            {
                "adapters",
                "capabilities",
                "readable_scopes",
                "writable_scopes",
                "permission_profile",
            },
        )
        permission_profile = _mapping(
            authority.get("permission_profile"),
            "authority.permission_profile",
        )
        _require_exact_fields(
            permission_profile,
            "authority.permission_profile",
            {"mode", "preauthorized", "deny", "review_required"},
        )
        _require_exact_fields(budgets, "budgets", {"max_steps", "timeout_seconds"})
        dependency_items = _items(raw, "dependencies")
        dependency_refs: list[Mapping[str, Any]] = []
        for item in dependency_items:
            _require_exact_fields(
                item,
                "dependency",
                {"ref", "result_summary", "artifact_refs", "evidence_refs"},
            )
            ref = _mapping(item.get("ref"), "dependency.ref")
            _require_exact_fields(ref, "dependency.ref", {"kind", "id", "revision"})
            dependency_refs.append(ref)
        return cls(
            context_id=_string(raw, "context_id"),
            root_run_id=_string(raw, "root_run_id"),
            parent_run_id=_string(raw, "parent_run_id"),
            parent_node_id=_string(raw, "parent_node_id"),
            parent_node_attempt=_integer(raw, "parent_node_attempt"),
            task_attempt_id=_string(raw, "task_attempt_id"),
            child_run_id=_string(raw, "child_run_id"),
            template_id=_string(raw, "template_id"),
            template_fingerprint=_string(raw, "template_fingerprint"),
            child_workflow_fingerprint=_string(raw, "child_workflow_fingerprint"),
            child_execution_contract_fingerprint=_string(
                raw, "child_execution_contract_fingerprint"
            ),
            intent=_mapping(raw.get("intent"), "intent"),
            task=_mapping(raw.get("task"), "task"),
            dependencies=tuple(
                DependencyContext(
                    ref=DependencyRef(
                        kind=cast(Any, _string(ref, "kind")),
                        id=_string(ref, "id"),
                        revision=_integer(ref, "revision"),
                    ),
                    result_summary=_string(item, "result_summary"),
                    artifact_refs=tuple(item.get("artifact_refs", ())),
                    evidence_refs=tuple(item.get("evidence_refs", ())),
                )
                for item, ref in zip(dependency_items, dependency_refs, strict=True)
            ),
            inputs=_mapping(raw.get("inputs"), "inputs"),
            authority=ManifestAuthority(
                adapters=tuple(authority.get("adapters", ())),
                capabilities=tuple(authority.get("capabilities", ())),
                readable_scopes=tuple(authority.get("readable_scopes", ())),
                writable_scopes=tuple(authority.get("writable_scopes", ())),
                permission_profile=permission_profile,
            ),
            budgets=ManifestBudgets(
                max_steps=_integer(budgets, "max_steps"),
                timeout_seconds=_integer(budgets, "timeout_seconds"),
            ),
            fingerprint=_string(raw, "fingerprint"),
            schema_version=_string(raw, "schema_version"),
        )


def build_context_manifest(
    *,
    root_run_id: str,
    parent_run_id: str,
    parent_node_id: str,
    parent_node_attempt: int,
    task_attempt_id: str,
    child_run_id: str,
    template_id: str,
    template_fingerprint: str,
    child_workflow_fingerprint: str,
    child_execution_contract_fingerprint: str,
    intent: IntentVersion,
    task: TaskRun,
    dependencies: Iterable[DependencyContext],
    artifact_refs: Iterable[str] = (),
    evidence_refs: Iterable[str] = (),
    memory_refs: Iterable[str] = (),
    parent_adapters: Iterable[str],
    template_adapters: Iterable[str],
    workflow_adapters: Iterable[str],
    task_adapters: Iterable[str],
    current_policy_adapters: Iterable[str],
    parent_capabilities: Iterable[str],
    template_capabilities: Iterable[str],
    workflow_capabilities: Iterable[str],
    current_policy_capabilities: Iterable[str],
    readable_scope_sets: Iterable[Iterable[str]],
    writable_scope_sets: Iterable[Iterable[str]],
    parent_permission_profile: Mapping[str, Any] | None,
    template_permission_profile: Mapping[str, Any] | None,
    current_permission_mode: str,
    max_steps: int,
    timeout_seconds: int,
) -> ContextManifest:
    if intent.status != "confirmed":
        raise ContextManifestError("ContextManifest requires a confirmed Intent")
    if task.intent_version != intent.version:
        raise ContextManifestError("Task and Intent versions do not match")
    criteria = {item.id: item for item in intent.success_criteria}
    missing_criteria = set(task.supports) - set(criteria)
    if missing_criteria:
        raise ContextManifestError(
            f"Task references unknown Intent criteria: {', '.join(sorted(missing_criteria))}"
        )
    dependency_values = tuple(dependencies)
    expected_dependencies = {item.key for item in task.depends_on}
    actual_dependencies = {item.ref.key for item in dependency_values}
    if len(actual_dependencies) != len(dependency_values):
        raise ContextManifestError("ContextManifest dependency refs must be unique")
    if actual_dependencies != expected_dependencies:
        raise ContextManifestError("ContextManifest must contain exactly direct dependencies")
    adapters = _intersection(
        parent_adapters,
        template_adapters,
        workflow_adapters,
        task_adapters,
        current_policy_adapters,
    )
    capabilities = _intersection(
        parent_capabilities,
        template_capabilities,
        workflow_capabilities,
        current_policy_capabilities,
    )
    readable_scopes = _intersection(*tuple(readable_scope_sets))
    writable_scopes = _intersection(*tuple(writable_scope_sets))
    permission_profile = _intersect_permission_profiles(
        parent_permission_profile,
        template_permission_profile,
        current_permission_mode,
    )
    return ContextManifest(
        context_id=f"context/{task_attempt_id}",
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        parent_node_id=parent_node_id,
        parent_node_attempt=parent_node_attempt,
        task_attempt_id=task_attempt_id,
        child_run_id=child_run_id,
        template_id=template_id,
        template_fingerprint=template_fingerprint,
        child_workflow_fingerprint=child_workflow_fingerprint,
        child_execution_contract_fingerprint=child_execution_contract_fingerprint,
        intent={
            "intent_id": intent.intent_id,
            "version": intent.version,
            "binding_hash": task.intent_binding_hash,
            "goal": intent.goal,
            "desired_outcome": intent.desired_outcome,
            "relevant_criteria": [_plain(criteria[item]) for item in sorted(task.supports)],
        },
        task={
            "ref": _plain(task.ref),
            "goal": task.goal,
            "completion_contract": _plain(task.completion_contract),
            "constraints": list(intent.constraints),
            "non_goals": list(intent.non_goals),
            "assumptions": list(intent.assumptions),
        },
        dependencies=dependency_values,
        inputs={
            "artifact_refs": list(_normalized_refs(artifact_refs)),
            "evidence_refs": list(_normalized_refs(evidence_refs)),
            "memory_refs": list(_normalized_refs(memory_refs)),
        },
        authority=ManifestAuthority(
            adapters=adapters,
            capabilities=capabilities,
            readable_scopes=readable_scopes,
            writable_scopes=writable_scopes,
            permission_profile=permission_profile,
        ),
        budgets=ManifestBudgets(max_steps=max_steps, timeout_seconds=timeout_seconds),
    )


def _intersection(*values: Iterable[str]) -> tuple[str, ...]:
    sets = [set(_normalized_refs(value)) for value in values]
    if not sets:
        return ()
    result = sets[0]
    for item in sets[1:]:
        result &= item
    return tuple(sorted(result))


def _intersect_permission_profiles(
    parent: Mapping[str, Any] | None,
    template: Mapping[str, Any] | None,
    current_mode: str,
) -> dict[str, Any]:
    if current_mode not in {"trust", "auto", "preview"}:
        raise ContextManifestError(f"invalid current permission mode {current_mode!r}")
    parent_value = _permission_profile(parent)
    template_value = _permission_profile(template)
    modes = [current_mode]
    for value in (parent_value["mode"], template_value["mode"]):
        if value is not None:
            if value not in {"trust", "auto", "preview"}:
                raise ContextManifestError(f"invalid permission mode {value!r}")
            modes.append(value)
    order = {"trust": 0, "auto": 1, "preview": 2}
    return {
        "mode": max(modes, key=lambda item: order[item]),
        "preauthorized": sorted(
            set(parent_value["preauthorized"]) & set(template_value["preauthorized"])
        ),
        "deny": sorted(set(parent_value["deny"]) | set(template_value["deny"])),
        "review_required": sorted(
            set(parent_value["review_required"]) | set(template_value["review_required"])
        ),
    }


def _permission_profile(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = value or {}
    return {
        "mode": raw.get("mode"),
        "preauthorized": _normalized_refs(raw.get("preauthorized") or ()),
        "deny": _normalized_refs(raw.get("deny") or ()),
        "review_required": _normalized_refs(raw.get("review_required") or ()),
    }


def _normalized_refs(values: Iterable[str]) -> tuple[str, ...]:
    raw = tuple(values)
    if any(not isinstance(item, str) for item in raw):
        raise ContextManifestError("ContextManifest refs must be strings")
    normalized = tuple(sorted({item.strip() for item in raw if item.strip()}))
    if any("\x00" in item for item in normalized):
        raise ContextManifestError("ContextManifest refs cannot contain NUL bytes")
    if any(Path(item).is_absolute() for item in normalized):
        raise ContextManifestError("ContextManifest refs cannot contain absolute host paths")
    return normalized


def _input_refs(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple | list):
        raise ContextManifestError(f"ContextManifest inputs.{field_name} must be an array")
    return _normalized_refs(cast(Iterable[str], value))


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, tuple | list):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContextManifestError(f"{source} must be a mapping")
    return cast(Mapping[str, Any], value)


def _require_exact_fields(
    value: Any,
    source: str,
    expected: set[str],
) -> None:
    if not isinstance(value, Mapping):
        raise ContextManifestError(f"ContextManifest {source} must be a mapping")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise ContextManifestError(
            f"ContextManifest {source} has invalid fields: {'; '.join(details)}"
        )


def _items(raw: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    value = raw.get(key, ())
    if not isinstance(value, tuple | list):
        raise ContextManifestError(f"{key} must be an array")
    return tuple(_mapping(item, key) for item in value)


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextManifestError(f"{key} must be a non-empty string")
    return value


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContextManifestError(f"{key} must be an integer")
    return value


__all__ = [
    "ContextManifest",
    "ContextManifestError",
    "DependencyContext",
    "ManifestAuthority",
    "ManifestBudgets",
    "build_context_manifest",
]
