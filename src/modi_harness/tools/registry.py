"""Tool registry: stores ToolSpec + handler + optional dry-run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..types import ToolSpec

ToolHandler = Callable[..., Any]


_DEFAULTS: dict[str, Any] = {
    "output_schema": None,
    "permission_scope": "",
    "allowed_agents": [],
    "allowed_skills": [],
    "timeout_seconds": 30,
    "retry": None,
    "idempotent": False,
    "dry_run_supported": False,
    "tags": [],
    "kind": "regular",
    "subagent_target": None,
}


@dataclass
class _Entry:
    spec: ToolSpec
    handler: ToolHandler
    dry_run: ToolHandler | None


class ToolRegistry:
    """Stores registered tools by name."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def register_tool(
        self,
        spec: dict[str, Any],
        handler: ToolHandler,
        *,
        dry_run: ToolHandler | None = None,
    ) -> None:
        normalized = self._with_defaults(spec)
        if dry_run is not None:
            normalized["dry_run_supported"] = True
        self._entries[normalized["name"]] = _Entry(spec=normalized, handler=handler, dry_run=dry_run)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._entries[name].spec
        except KeyError as exc:
            raise KeyError(name) from exc

    def get_entry(self, name: str) -> _Entry:
        return self._entries[name]

    def has(self, name: str) -> bool:
        return name in self._entries

    def names(self) -> list[str]:
        return list(self._entries.keys())

    @staticmethod
    def _with_defaults(spec: dict[str, Any]) -> ToolSpec:
        merged: dict[str, Any] = dict(_DEFAULTS)
        merged.update(spec)
        return ToolSpec(**merged)  # type: ignore[typeddict-item]
