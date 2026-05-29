"""Memory Store implementation.

Records are stored as Markdown with YAML frontmatter. Each scope owns its own
directory; lookups are scope-ordered (conversation -> project -> agent -> user).
Selection for context is rule-based (no embeddings in V0.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .._utils import parse_frontmatter, now_iso
from ..types import MemoryIndex, MemoryLevel, MemoryRecord, MemoryScope
from .errors import (
    MemoryBodyTooLargeError,
    MemoryIdInvalidError,
    MemoryNotFoundError,
)

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_BODY_LIMIT_BYTES = 4 * 1024
_INDEX_FILENAME = "MEMORY.md"


@dataclass(frozen=True)
class MemoryPaths:
    """Per-scope filesystem roots."""

    user: Path
    agent: Path
    project: Path
    conversation: Path

    def for_scope(self, scope: MemoryScope) -> Path:
        return getattr(self, scope)


_SCOPE_ORDER: tuple[MemoryScope, ...] = ("conversation", "project", "agent", "user")


class MemoryStore:
    """Reads and writes typed memory records across scopes."""

    def __init__(self, paths: MemoryPaths) -> None:
        self._paths = paths

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def write_record(self, record: dict[str, Any]) -> MemoryRecord:
        rec_id = record.get("id") or ""
        if not _ID_PATTERN.match(rec_id):
            raise MemoryIdInvalidError(f"invalid id: {rec_id!r}")

        body = record.get("body", "")
        if len(body.encode("utf-8")) > _BODY_LIMIT_BYTES:
            raise MemoryBodyTooLargeError(
                f"body exceeds {_BODY_LIMIT_BYTES} bytes; move content to workspace"
            )

        scope: MemoryScope = record["scope"]
        scope_dir = self._paths.for_scope(scope)
        scope_dir.mkdir(parents=True, exist_ok=True)

        now = now_iso()
        full = MemoryRecord(
            id=rec_id,
            scope=scope,
            type=record["type"],
            name=record.get("name", ""),
            description=record.get("description", ""),
            body=body,
            tags=list(record.get("tags") or []),
            source_run_id=record.get("source_run_id"),
            created_at=record.get("created_at", now),
            updated_at=now,
            expires_at=record.get("expires_at"),
            metadata=dict(record.get("metadata") or {}),
        )

        path = scope_dir / f"{rec_id}.md"
        path.write_text(_to_markdown(full), encoding="utf-8")
        self._update_index_after_write(scope, full)
        return full

    def read_record(self, record_id: str) -> MemoryRecord:
        for scope in _SCOPE_ORDER:
            path = self._paths.for_scope(scope) / f"{record_id}.md"
            if path.exists():
                return _from_markdown(path.read_text(encoding="utf-8"), scope)
        raise MemoryNotFoundError(record_id)

    def update_record(self, record_id: str, patch: dict[str, Any]) -> MemoryRecord:
        existing = self.read_record(record_id)
        merged = dict(existing)
        for k, v in patch.items():
            if k in ("id", "scope", "created_at"):
                continue  # immutable
            merged[k] = v
        return self.write_record(merged)

    def delete_record(self, record_id: str) -> None:
        for scope in _SCOPE_ORDER:
            path = self._paths.for_scope(scope) / f"{record_id}.md"
            if path.exists():
                path.unlink()
                self._rebuild_index(scope)
                return
        raise MemoryNotFoundError(record_id)

    # ------------------------------------------------------------------
    # index / search
    # ------------------------------------------------------------------

    def load_index(self, scopes: Iterable[MemoryScope]) -> MemoryIndex:
        records: list[MemoryRecord] = []
        for scope in scopes:
            scope_dir = self._paths.for_scope(scope)
            if not scope_dir.is_dir():
                continue
            for path in sorted(scope_dir.iterdir()):
                if path.name == _INDEX_FILENAME or path.suffix != ".md":
                    continue
                records.append(_from_markdown(path.read_text(encoding="utf-8"), scope))

        by_scope: dict[str, list[str]] = {}
        by_type: dict[str, list[str]] = {}
        by_tag: dict[str, list[str]] = {}
        for r in records:
            by_scope.setdefault(r["scope"], []).append(r["id"])
            by_type.setdefault(r["type"], []).append(r["id"])
            for tag in r["tags"]:
                by_tag.setdefault(tag, []).append(r["id"])
        return MemoryIndex(records=records, by_scope=by_scope, by_type=by_type, by_tag=by_tag)

    def search(
        self,
        query: str | None = None,
        scopes: Iterable[MemoryScope] | None = None,
        types: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        active_scopes = set(scopes) if scopes is not None else set(_SCOPE_ORDER)
        idx = self.load_index(active_scopes)
        results: list[MemoryRecord] = []
        types_set = set(types) if types is not None else None
        tags_set = set(tags) if tags is not None else None
        for r in idx["records"]:
            if types_set is not None and r["type"] not in types_set:
                continue
            if tags_set is not None and not (set(r["tags"]) & tags_set):
                continue
            if query and query.lower() not in (
                r["name"].lower() + " " + r["body"].lower() + " " + r["description"].lower()
            ):
                continue
            results.append(r)
            if limit is not None and len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------------
    # selection for Context Manager
    # ------------------------------------------------------------------

    _LEVEL_CONFIG: dict[str, tuple[list[str], int]] = {
        "minimal": (["feedback"], 500),
        "moderate": (["feedback", "user", "project"], 1500),
        "full": (["feedback", "user", "project", "reference"], 3000),
    }

    def select_for_context(
        self,
        task: dict[str, Any],
        agent_name: str,
        scopes: Iterable[MemoryScope],
        budget: int | None = None,
        level: MemoryLevel = "moderate",
    ) -> list[MemoryRecord]:
        """Apply selection priority: feedback -> user -> project (tag-matched) -> reference.

        The ``level`` parameter controls which memory types are included and
        provides a default token budget:
          - "minimal"  — only feedback, 500 tokens
          - "moderate" — feedback + user + project, 1500 tokens
          - "full"     — all types, 3000 tokens

        An explicit ``budget`` overrides the level's default.
        """
        del agent_name  # reserved for agent-scope filtering once write paths use it
        allowed_types, default_budget = self._LEVEL_CONFIG[level]
        effective_budget = budget if budget is not None else default_budget

        idx = self.load_index(scopes)
        records = idx["records"]

        feedback = [r for r in records if r["type"] == "feedback"] if "feedback" in allowed_types else []
        user = [r for r in records if r["type"] == "user"] if "user" in allowed_types else []
        task_tags = set((task or {}).get("tags") or [])
        project = (
            [
                r for r in records if r["type"] == "project" and (not task_tags or set(r["tags"]) & task_tags)
            ]
            if "project" in allowed_types
            else []
        )
        ref_names = set((task or {}).get("reference_keys") or [])
        reference = (
            [r for r in records if r["type"] == "reference" and r["name"] in ref_names]
            if "reference" in allowed_types
            else []
        )

        ordered = feedback + user + project + reference

        # Budget by approximate token count (1 token ≈ 4 bytes).
        out: list[MemoryRecord] = []
        used = 0
        for r in ordered:
            tokens = max(1, len(r["body"].encode("utf-8")) // 4)
            if used + tokens > effective_budget:
                continue
            out.append(r)
            used += tokens
        return out

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _update_index_after_write(self, scope: MemoryScope, record: MemoryRecord) -> None:
        self._rebuild_index(scope)

    def _rebuild_index(self, scope: MemoryScope) -> None:
        scope_dir = self._paths.for_scope(scope)
        if not scope_dir.is_dir():
            return
        lines: list[str] = []
        for path in sorted(scope_dir.iterdir()):
            if path.name == _INDEX_FILENAME or path.suffix != ".md":
                continue
            r = _from_markdown(path.read_text(encoding="utf-8"), scope)
            lines.append(f"- [{r['id']}]({r['id']}.md) — {r['description']}")
        (scope_dir / _INDEX_FILENAME).write_text("\n".join(lines) + ("\n" if lines else ""))


def _to_markdown(record: MemoryRecord) -> str:
    import yaml

    fm: dict[str, Any] = {
        "id": record["id"],
        "scope": record["scope"],
        "type": record["type"],
        "name": record["name"],
        "description": record["description"],
        "tags": record["tags"],
        "source_run_id": record["source_run_id"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "expires_at": record["expires_at"],
        "metadata": record["metadata"],
    }
    return f"---\n{yaml.safe_dump(fm, sort_keys=True, allow_unicode=True)}---\n{record['body']}"


def _from_markdown(text: str, scope: MemoryScope) -> MemoryRecord:
    fm, body = parse_frontmatter(text)
    return MemoryRecord(
        id=fm["id"],
        scope=scope,
        type=fm["type"],
        name=fm.get("name", ""),
        description=fm.get("description", ""),
        body=body,
        tags=list(fm.get("tags") or []),
        source_run_id=fm.get("source_run_id"),
        created_at=fm.get("created_at", ""),
        updated_at=fm.get("updated_at", ""),
        expires_at=fm.get("expires_at"),
        metadata=dict(fm.get("metadata") or {}),
    )
