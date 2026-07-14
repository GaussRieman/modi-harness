"""Safe memory consolidation and index maintenance hooks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TypedDict

from ..types import MemoryScope
from .scope import MemoryScopeKeys
from .store import MemoryStore


class MemoryConsolidationReport(TypedDict):
    dry_run: bool
    duplicates: list[list[str]]
    expired: list[str]
    superseded: list[str]


class MemoryConsolidator:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def rebuild_indexes(
        self,
        *,
        scope_keys: MemoryScopeKeys | None = None,
        scopes: Iterable[MemoryScope] | None = None,
    ) -> None:
        for scope in scopes or ("user", "workspace", "agent", "thread"):
            self._store.rebuild_index(scope, scope_keys=scope_keys)

    def consolidate(
        self,
        *,
        scope_keys: MemoryScopeKeys | None = None,
        scopes: Iterable[MemoryScope] | None = None,
        dry_run: bool = True,
    ) -> MemoryConsolidationReport:
        all_records = self._store.load_index(
            scopes or ("user", "workspace", "agent", "thread"),
            scope_keys=scope_keys,
            include_expired=True,
            include_superseded=True,
        )["records"]

        by_signature: dict[tuple[str, str, tuple[str, ...]], list[str]] = defaultdict(list)
        active_ids = {r["id"] for r in self._store.search(scopes=scopes, scope_keys=scope_keys)}

        expired: list[str] = []
        superseded: list[str] = []
        for record in all_records:
            by_signature[
                (
                    record["type"],
                    record["body"].strip(),
                    tuple(sorted(record["tags"])),
                )
            ].append(record["id"])
            if record["id"] not in active_ids and not (record.get("metadata") or {}).get(
                "superseded_by"
            ):
                expired.append(record["id"])
            if (record.get("metadata") or {}).get("superseded_by"):
                superseded.append(record["id"])

        duplicates = [ids for ids in by_signature.values() if len(ids) > 1]
        return {
            "dry_run": dry_run,
            "duplicates": duplicates,
            "expired": sorted(set(expired)),
            "superseded": sorted(set(superseded)),
        }
