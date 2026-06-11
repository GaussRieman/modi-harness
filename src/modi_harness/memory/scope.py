"""Scope-key helpers for partitioned memory storage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..types import MemoryScope


_KEY_SEGMENT_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass(frozen=True)
class MemoryScopeKeys:
    """Physical partition keys for each logical memory scope."""

    user_key: str = "default"
    agent_name: str = ""
    project_key: str = ""
    thread_id: str = ""

    def for_run(self, *, agent_name: str | None, thread_id: str | None) -> "MemoryScopeKeys":
        """Return scope keys specialized to the current agent/thread."""
        return MemoryScopeKeys(
            user_key=self.user_key,
            agent_name=agent_name or self.agent_name,
            project_key=self.project_key,
            thread_id=thread_id or self.thread_id,
        )

    def key_for_scope(self, scope: MemoryScope) -> str:
        if scope == "user":
            return self.user_key
        if scope == "agent":
            return self.agent_name
        if scope == "project":
            return self.project_key
        if scope == "conversation":
            return self.thread_id
        return ""


def keyed_scope_path(base: Path, scope: MemoryScope, scope_keys: MemoryScopeKeys | None) -> Path:
    """Return the keyed directory for a scope, or the legacy base when no key exists."""
    if scope_keys is None:
        return base
    key = safe_scope_key(scope_keys.key_for_scope(scope))
    if not key:
        return base
    return base / key


def safe_scope_key(value: str) -> str:
    """Make a stable, single-path-segment scope key."""
    return _KEY_SEGMENT_PATTERN.sub("_", str(value).strip()).strip("._-")


__all__ = ["MemoryScopeKeys", "keyed_scope_path", "safe_scope_key"]
