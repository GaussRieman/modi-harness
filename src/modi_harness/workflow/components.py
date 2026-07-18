"""Pinned planner, verifier, and coordination components for Task Graph runs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from .._utils import compute_fingerprint, new_ulid

ComponentKind = Literal[
    "planner",
    "graph_policy",
    "context_builder",
    "task_verifier",
    "group_verifier",
    "criterion_verifier",
    "goal_verifier",
    "parent_inline",
    "human_contract",
]
ComponentOutcome = Literal[
    "passed",
    "repairable",
    "repairable_gap",
    "needs_replan",
    "ambiguous",
    "impossible",
    "terminal",
]
InvocationStatus = Literal["prepared", "completed", "failed"]


class ComponentRegistryError(ValueError):
    """A pinned Task Graph component is invalid or unavailable."""


@dataclass(frozen=True, slots=True)
class PinnedComponent:
    """One callable or declarative component whose behavior is contract-pinned."""

    id: str
    version: str
    kind: ComponentKind
    implementation_digest: str
    protocol_version: str
    input_schema_id: str
    output_schema_id: str
    supported_outcomes: tuple[ComponentOutcome, ...]
    configuration: Mapping[str, Any]
    implementation: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "version",
            "implementation_digest",
            "protocol_version",
            "input_schema_id",
            "output_schema_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ComponentRegistryError(f"component {field_name} must be non-empty")
            object.__setattr__(self, field_name, value.strip())
        if self.kind not in {
            "planner",
            "graph_policy",
            "context_builder",
            "task_verifier",
            "group_verifier",
            "criterion_verifier",
            "goal_verifier",
            "parent_inline",
            "human_contract",
        }:
            raise ComponentRegistryError(f"unsupported component kind {self.kind!r}")
        outcomes = tuple(dict.fromkeys(self.supported_outcomes))
        if not outcomes:
            raise ComponentRegistryError("component supported_outcomes cannot be empty")
        if any(item not in _OUTCOMES for item in outcomes):
            raise ComponentRegistryError("component has unsupported outcome")
        object.__setattr__(self, "supported_outcomes", outcomes)
        object.__setattr__(self, "configuration", _freeze(dict(self.configuration)))
        if self.kind != "human_contract" and not callable(self.implementation):
            raise ComponentRegistryError(
                f"{self.kind} component {self.id!r} requires a callable implementation"
            )

    @property
    def fingerprint(self) -> str:
        return compute_fingerprint(self._snapshot_payload())

    def snapshot(self) -> dict[str, Any]:
        payload = self._snapshot_payload()
        return {**payload, "fingerprint": compute_fingerprint(payload)}

    def _snapshot_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "kind": self.kind,
            "implementation_digest": self.implementation_digest,
            "protocol_version": self.protocol_version,
            "input_schema_id": self.input_schema_id,
            "output_schema_id": self.output_schema_id,
            "supported_outcomes": list(self.supported_outcomes),
            "configuration": _thaw(self.configuration),
        }


_OUTCOMES = frozenset(
    {
        "passed",
        "repairable",
        "repairable_gap",
        "needs_replan",
        "ambiguous",
        "impossible",
        "terminal",
    }
)


class PinnedComponentRegistry:
    """Closed registry that can rehydrate only exact component snapshots."""

    def __init__(self) -> None:
        self._components: dict[str, PinnedComponent] = {}

    def register(self, component: PinnedComponent) -> None:
        if component.id in self._components:
            raise ComponentRegistryError(f"duplicate component {component.id!r}")
        self._components[component.id] = component

    def resolve(self, component_id: str, *, kind: ComponentKind | None = None) -> PinnedComponent:
        try:
            component = self._components[component_id]
        except KeyError as exc:
            raise ComponentRegistryError(f"unknown component {component_id!r}") from exc
        if kind is not None and component.kind != kind:
            raise ComponentRegistryError(
                f"component {component_id!r} has kind {component.kind!r}, expected {kind!r}"
            )
        return component

    def resolve_pinned(self, snapshot: Mapping[str, Any]) -> PinnedComponent:
        component_id = _required_string(snapshot, "id")
        component = self.resolve(component_id)
        expected = _thaw(snapshot)
        actual = component.snapshot()
        if expected != actual:
            raise ComponentRegistryError(
                f"pinned component {component_id!r} is unavailable at the recorded digest"
            )
        return component

    def ids(self) -> frozenset[str]:
        return frozenset(self._components)


@dataclass(frozen=True, slots=True)
class ComponentInvocationRecord:
    """Durable replay record for one Planner/Verifier/inline call."""

    id: str
    root_run_id: str
    component_id: str
    component_fingerprint: str
    idempotency_key: str
    input_hash: str
    status: InvocationStatus
    output: Any | None = None
    error: str | None = None

    @classmethod
    def prepared(
        cls,
        *,
        root_run_id: str,
        component: PinnedComponent,
        idempotency_key: str,
        inputs: Any,
    ) -> ComponentInvocationRecord:
        return cls(
            id=new_ulid(),
            root_run_id=root_run_id,
            component_id=component.id,
            component_fingerprint=component.fingerprint,
            idempotency_key=idempotency_key,
            input_hash=compute_fingerprint(inputs),
            status="prepared",
        )


def _required_string(value: Mapping[str, Any], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ComponentRegistryError(f"component snapshot field {key!r} must be non-empty")
    return raw.strip()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "ComponentInvocationRecord",
    "ComponentKind",
    "ComponentOutcome",
    "ComponentRegistryError",
    "InvocationStatus",
    "PinnedComponent",
    "PinnedComponentRegistry",
]
