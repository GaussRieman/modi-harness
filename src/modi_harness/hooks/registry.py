"""Hook registry: load HookSpec from settings.json files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..types import HookSpec


_DEFAULT_HOOK: dict[str, Any] = {
    "matcher": None,
    "timeout_seconds": 10,
    "blocking": True,
    "pass_payload": "stdin",
    "capture": "stdout",
    "on_failure": "warn",
}


class HookRegistry:
    """Loads, validates, indexes hooks by event."""

    def __init__(self, hooks: list[HookSpec]) -> None:
        self._hooks: list[HookSpec] = list(hooks)
        self._by_event: dict[str, list[HookSpec]] = {}
        for h in self._hooks:
            self._by_event.setdefault(h["event"], []).append(h)

    @classmethod
    def from_files(
        cls,
        user_settings: Path | str | None,
        project_settings: Path | str | None,
    ) -> HookRegistry:
        # Load per source; dedupe across sources only (project beats user on
        # event+matcher collision). Within a single source, all entries are kept
        # so a config can declare multiple chained hooks for the same event.
        user_hooks = _load(user_settings)
        project_hooks = _load(project_settings)
        return cls(_merge_sources(user_hooks, project_hooks))

    def for_event(self, event: str) -> list[HookSpec]:
        return self._by_event.get(event, [])

    def all(self) -> list[HookSpec]:
        return list(self._hooks)


def _load(source: Path | str | None) -> list[HookSpec]:
    if source is None:
        return []
    p = Path(source)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [_normalize(raw) for raw in data.get("hooks", [])]


def _merge_sources(user: list[HookSpec], project: list[HookSpec]) -> list[HookSpec]:
    """Project overrides user on (event, matcher). Within each source, order kept."""
    def _key(h: HookSpec) -> tuple[str, str]:
        return (h["event"], json.dumps(h["matcher"], sort_keys=True))

    project_keys = {_key(h) for h in project}
    out: list[HookSpec] = [h for h in user if _key(h) not in project_keys]
    out.extend(project)
    return out


def _normalize(raw: dict[str, Any]) -> HookSpec:
    if "event" not in raw:
        raise ValueError("hook spec missing required 'event'")
    if "command" not in raw:
        raise ValueError(f"hook for event {raw['event']} missing 'command'")
    merged: dict[str, Any] = dict(_DEFAULT_HOOK)
    merged.update(raw)
    return HookSpec(  # type: ignore[typeddict-item]
        event=merged["event"],
        matcher=merged["matcher"],
        command=merged["command"],
        timeout_seconds=int(merged["timeout_seconds"]),
        blocking=bool(merged["blocking"]),
        pass_payload=merged["pass_payload"],
        capture=merged["capture"],
        on_failure=merged["on_failure"],
    )
