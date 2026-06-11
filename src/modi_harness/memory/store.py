"""Memory Store implementation.

Records are stored as Markdown with YAML frontmatter. Each scope owns its own
directory; lookups are scope-ordered (conversation -> project -> agent -> user).
Selection for context is rule-based (no embeddings in V0.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .._utils import parse_frontmatter, now_iso
from ..types import MemoryCandidate, MemoryIndex, MemoryLevel, MemoryRecord, MemoryScope, SelectedMemory
from .admission import admit_candidates, annotate_selected
from .errors import (
    MemoryBodyTooLargeError,
    MemoryIdInvalidError,
    MemoryNotFoundError,
)
from .scope import MemoryScopeKeys, keyed_scope_path
from .retriever import rank_records

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

    def __init__(self, paths: MemoryPaths, *, project_horizon_days: int | None = None) -> None:
        self._paths = paths
        self._project_horizon_days = project_horizon_days

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def write_record(
        self,
        record: dict[str, Any],
        *,
        scope_keys: MemoryScopeKeys | None = None,
    ) -> MemoryRecord:
        rec_id = record.get("id") or ""
        if not _ID_PATTERN.match(rec_id):
            raise MemoryIdInvalidError(f"invalid id: {rec_id!r}")

        body = record.get("body", "")
        if len(body.encode("utf-8")) > _BODY_LIMIT_BYTES:
            raise MemoryBodyTooLargeError(
                f"body exceeds {_BODY_LIMIT_BYTES} bytes; move content to workspace"
            )

        scope: MemoryScope = record["scope"]
        scope_dir = self._scope_dir_for_write(scope, scope_keys)
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
            updated_at=record.get("updated_at", now),
            expires_at=record.get("expires_at"),
            metadata=dict(record.get("metadata") or {}),
        )

        path = scope_dir / f"{rec_id}.md"
        path.write_text(_to_markdown(full), encoding="utf-8")
        self._update_index_after_write(scope, full, scope_dir)
        return full

    def read_record(
        self,
        record_id: str,
        *,
        scope_keys: MemoryScopeKeys | None = None,
        scopes: Iterable[MemoryScope] | None = None,
    ) -> MemoryRecord:
        for scope in scopes or _SCOPE_ORDER:
            for scope_dir in self._scope_dirs(scope, scope_keys):
                path = scope_dir / f"{record_id}.md"
                if path.exists():
                    return _from_markdown(path.read_text(encoding="utf-8"), scope)
        raise MemoryNotFoundError(record_id)

    def update_record(
        self,
        record_id: str,
        patch: dict[str, Any],
        *,
        scope_keys: MemoryScopeKeys | None = None,
    ) -> MemoryRecord:
        existing = self.read_record(record_id, scope_keys=scope_keys)
        merged = dict(existing)
        for k, v in patch.items():
            if k in ("id", "scope", "created_at"):
                continue  # immutable
            merged[k] = v
        merged["updated_at"] = now_iso()
        return self.write_record(merged, scope_keys=scope_keys)

    def delete_record(
        self,
        record_id: str,
        *,
        scope_keys: MemoryScopeKeys | None = None,
    ) -> None:
        for scope in _SCOPE_ORDER:
            for scope_dir in self._scope_dirs(scope, scope_keys):
                path = scope_dir / f"{record_id}.md"
                if path.exists():
                    path.unlink()
                    self._rebuild_index(scope, scope_dir)
                    return
        raise MemoryNotFoundError(record_id)

    # ------------------------------------------------------------------
    # index / search
    # ------------------------------------------------------------------

    def load_index(
        self,
        scopes: Iterable[MemoryScope],
        *,
        scope_keys: MemoryScopeKeys | None = None,
        include_expired: bool = False,
        include_superseded: bool = False,
    ) -> MemoryIndex:
        records: list[MemoryRecord] = []
        seen: set[tuple[str, str]] = set()
        now = _utc_now()
        for scope in scopes:
            for scope_dir in self._scope_dirs(scope, scope_keys):
                if not scope_dir.is_dir():
                    continue
                for path in sorted(scope_dir.iterdir()):
                    if path.name == _INDEX_FILENAME or path.suffix != ".md":
                        continue
                    record = _from_markdown(path.read_text(encoding="utf-8"), scope)
                    if not self._is_active_record(
                        record,
                        now=now,
                        include_expired=include_expired,
                        include_superseded=include_superseded,
                    ):
                        continue
                    key = (scope, record["id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(record)

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
        scope_keys: MemoryScopeKeys | None = None,
        include_expired: bool = False,
        include_superseded: bool = False,
    ) -> list[MemoryRecord]:
        candidates = self.search_candidates(
            query=query,
            scopes=scopes,
            types=types,
            tags=tags,
            limit=limit,
            scope_keys=scope_keys,
            include_expired=include_expired,
            include_superseded=include_superseded,
        )
        return [c["record"] for c in candidates]

    def search_candidates(
        self,
        query: str | None = None,
        scopes: Iterable[MemoryScope] | None = None,
        types: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
        limit: int | None = None,
        scope_keys: MemoryScopeKeys | None = None,
        include_expired: bool = False,
        include_superseded: bool = False,
    ) -> list[MemoryCandidate]:
        active_scopes = set(scopes) if scopes is not None else set(_SCOPE_ORDER)
        idx = self.load_index(
            active_scopes,
            scope_keys=scope_keys,
            include_expired=include_expired,
            include_superseded=include_superseded,
        )
        types_set = set(types) if types is not None else None
        tags_set = set(tags) if tags is not None else None
        candidates = rank_records(idx["records"], query=query, types=types_set, tags=tags_set)
        return candidates[:limit] if limit is not None else candidates

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
        scope_keys: MemoryScopeKeys | None = None,
        include_expired: bool = False,
        include_superseded: bool = False,
    ) -> list[MemoryRecord]:
        return [
            annotate_selected(selected)  # type: ignore[list-item]
            for selected in self.select_candidates_for_context(
                task=task,
                agent_name=agent_name,
                scopes=scopes,
                budget=budget,
                level=level,
                scope_keys=scope_keys,
                include_expired=include_expired,
                include_superseded=include_superseded,
            )
        ]

    def select_candidates_for_context(
        self,
        task: dict[str, Any],
        agent_name: str,
        scopes: Iterable[MemoryScope],
        budget: int | None = None,
        level: MemoryLevel = "moderate",
        scope_keys: MemoryScopeKeys | None = None,
        include_expired: bool = False,
        include_superseded: bool = False,
    ) -> list[SelectedMemory]:
        candidates, effective_budget = self.recall_candidates_for_context(
            task=task,
            agent_name=agent_name,
            scopes=scopes,
            budget=budget,
            level=level,
            scope_keys=scope_keys,
            include_expired=include_expired,
            include_superseded=include_superseded,
        )
        admitted = admit_candidates(candidates)

        # Budget by approximate token count (1 token ≈ 4 bytes).
        out: list[SelectedMemory] = []
        used = 0
        for selected in admitted:
            r = selected["record"]
            tokens = max(1, len(r["body"].encode("utf-8")) // 4)
            if used + tokens > effective_budget:
                continue
            out.append(selected)
            used += tokens
        return out

    def recall_candidates_for_context(
        self,
        task: dict[str, Any],
        agent_name: str,
        scopes: Iterable[MemoryScope],
        budget: int | None = None,
        level: MemoryLevel = "moderate",
        scope_keys: MemoryScopeKeys | None = None,
        include_expired: bool = False,
        include_superseded: bool = False,
    ) -> tuple[list[MemoryCandidate], int]:
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

        idx = self.load_index(
            scopes,
            scope_keys=scope_keys,
            include_expired=include_expired,
            include_superseded=include_superseded,
        )
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

        candidates = (
            rank_records(feedback)
            + rank_records(user)
            + rank_records(project)
            + rank_records(reference)
        )
        return candidates, effective_budget

    def rebuild_index(
        self,
        scope: MemoryScope,
        *,
        scope_keys: MemoryScopeKeys | None = None,
    ) -> None:
        for scope_dir in self._scope_dirs(scope, scope_keys):
            self._rebuild_index(scope, scope_dir)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _update_index_after_write(
        self,
        scope: MemoryScope,
        record: MemoryRecord,
        scope_dir: Path,
    ) -> None:
        del record
        self._rebuild_index(scope, scope_dir)

    def _rebuild_index(self, scope: MemoryScope, scope_dir: Path) -> None:
        if not scope_dir.is_dir():
            return
        lines: list[str] = []
        for path in sorted(scope_dir.iterdir()):
            if path.name == _INDEX_FILENAME or path.suffix != ".md":
                continue
            r = _from_markdown(path.read_text(encoding="utf-8"), scope)
            lines.append(f"- [{r['id']}]({r['id']}.md) — {r['description']}")
        (scope_dir / _INDEX_FILENAME).write_text("\n".join(lines) + ("\n" if lines else ""))

    def _scope_dir_for_write(
        self,
        scope: MemoryScope,
        scope_keys: MemoryScopeKeys | None,
    ) -> Path:
        return keyed_scope_path(self._paths.for_scope(scope), scope, scope_keys)

    def _scope_dirs(
        self,
        scope: MemoryScope,
        scope_keys: MemoryScopeKeys | None,
    ) -> list[Path]:
        legacy = self._paths.for_scope(scope)
        keyed = keyed_scope_path(legacy, scope, scope_keys)
        if keyed == legacy:
            return [legacy]
        return [keyed, legacy]

    def _is_active_record(
        self,
        record: MemoryRecord,
        *,
        now: datetime,
        include_expired: bool,
        include_superseded: bool,
    ) -> bool:
        if not include_expired and _is_expired(record, now):
            return False
        if not include_expired and self._is_beyond_project_horizon(record, now):
            return False
        if not include_superseded and _is_superseded(record):
            return False
        return True

    def _is_beyond_project_horizon(self, record: MemoryRecord, now: datetime) -> bool:
        if record["scope"] != "project" or self._project_horizon_days is None:
            return False
        stamp = _parse_iso(record.get("updated_at") or record.get("created_at") or "")
        if stamp is None:
            return False
        return stamp < now - timedelta(days=self._project_horizon_days)


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(record: MemoryRecord, now: datetime) -> bool:
    expires_at = record.get("expires_at")
    if not expires_at:
        return False
    parsed = _parse_iso(str(expires_at))
    return False if parsed is None else parsed <= now


def _is_superseded(record: MemoryRecord) -> bool:
    metadata = record.get("metadata") or {}
    return bool(metadata.get("superseded_by"))


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
