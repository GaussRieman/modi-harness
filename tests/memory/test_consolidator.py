"""Tests for safe memory consolidation hooks."""

from __future__ import annotations

from pathlib import Path

from modi_harness.memory import MemoryConsolidator, MemoryPaths, MemoryScopeKeys, MemoryStore


def _paths(tmp_path: Path) -> MemoryPaths:
    return MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        project=tmp_path / "project",
        conversation=tmp_path / "conversation",
    )


def _record(record_id: str, *, body: str, scope: str = "user", **overrides) -> dict:
    base = {
        "id": record_id,
        "scope": scope,
        "type": "feedback" if scope != "project" else "project",
        "name": record_id,
        "description": "desc",
        "body": body,
        "tags": [],
    }
    base.update(overrides)
    return base


def test_rebuild_indexes_for_keyed_scope(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    keys = MemoryScopeKeys(user_key="u")
    store.write_record(_record("a", body="a"), scope_keys=keys)
    index = tmp_path / "user" / "u" / "MEMORY.md"
    index.unlink()

    MemoryConsolidator(store).rebuild_indexes(scope_keys=keys, scopes=["user"])

    assert index.exists()
    assert "[a](a.md)" in index.read_text()


def test_consolidate_dry_run_reports_duplicates_and_expired(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    keys = MemoryScopeKeys(user_key="u")
    store.write_record(_record("a", body="same", tags=["x"], scope="user"), scope_keys=keys)
    store.write_record(_record("b", body="same", tags=["x"], scope="user"), scope_keys=keys)
    store.write_record(
        _record(
            "old",
            body="old",
            scope="project",
            updated_at="2000-01-01T00:00:00.000Z",
            expires_at="2000-01-02T00:00:00.000Z",
        ),
        scope_keys=keys,
    )

    report = MemoryConsolidator(store).consolidate(scope_keys=keys, dry_run=True)

    assert report["dry_run"] is True
    assert report["duplicates"]
    assert "old" in report["expired"]
    assert store.read_record("a", scope_keys=keys)["body"] == "same"
