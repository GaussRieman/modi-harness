"""Versioned structured-schema registry for Workflow execution contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .._utils import compute_fingerprint


class SchemaRegistryError(ValueError):
    """A structured schema cannot be registered or resolved."""


@dataclass(frozen=True, slots=True)
class SchemaDefinition:
    """One immutable named JSON Schema definition."""

    id: str
    version: str
    schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        schema_id = _nonempty(self.id, "schema id")
        version = _nonempty(self.version, "schema version")
        if not isinstance(self.schema, Mapping):
            raise SchemaRegistryError("schema must be a mapping")
        object.__setattr__(self, "id", schema_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "schema", _freeze(dict(self.schema)))

    @property
    def fingerprint(self) -> str:
        return compute_fingerprint(self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "schema": _thaw(self.schema),
        }


class SchemaRegistry:
    """Closed lookup for immutable structured schemas."""

    def __init__(self) -> None:
        self._definitions: dict[str, SchemaDefinition] = {}

    def register(self, definition: SchemaDefinition) -> None:
        if definition.id in self._definitions:
            raise SchemaRegistryError(f"duplicate schema {definition.id!r}")
        self._definitions[definition.id] = definition

    def resolve(self, schema_id: str) -> SchemaDefinition:
        try:
            return self._definitions[schema_id]
        except KeyError as exc:
            raise SchemaRegistryError(f"unknown schema {schema_id!r}") from exc

    def ids(self) -> frozenset[str]:
        return frozenset(self._definitions)


def _nonempty(value: object, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaRegistryError(f"{source} must be non-empty")
    return value.strip()


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


__all__ = ["SchemaDefinition", "SchemaRegistry", "SchemaRegistryError"]
