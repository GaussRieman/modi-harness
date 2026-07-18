"""Durable root snapshot compare-and-swap tests."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from modi_harness.checkpoint import (
    InMemoryRootCheckpointStore,
    RootRunSnapshot,
    RootStoreConflict,
    RootStoreError,
    SqliteRootCheckpointStore,
)
from modi_harness.long_task import AuditEvent


def _snapshot(*, revision: int = 0) -> RootRunSnapshot:
    return RootRunSnapshot(
        root_run_id="root-1",
        thread_id="thread-1",
        revision=revision,
        workflow_state={"status": "running", "revision": revision},
    )


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_root_store_create_load_and_cas(kind: str, tmp_path: Path) -> None:
    store = (
        InMemoryRootCheckpointStore()
        if kind == "memory"
        else SqliteRootCheckpointStore(tmp_path / "checkpoint.sqlite")
    )
    created = store.create(_snapshot())
    event = AuditEvent("event-1", "root_updated", 1, {"status": "waiting"})
    committed = store.compare_and_swap(
        "root-1",
        expected_revision=0,
        snapshot=replace(
            created,
            revision=1,
            workflow_state={"status": "waiting", "revision": 1},
        ),
        event=event,
    )

    assert store.load("root-1") == committed
    assert store.load_by_thread("thread-1") == committed
    assert committed.last_event == event
    with pytest.raises(RootStoreConflict, match="stale root revision"):
        store.compare_and_swap(
            "root-1",
            expected_revision=0,
            snapshot=replace(committed, revision=1),
            event=event,
        )


def test_sqlite_root_store_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.sqlite"
    first = SqliteRootCheckpointStore(path)
    first.create(_snapshot())
    first.close()

    restored = SqliteRootCheckpointStore(path)
    assert restored.load_by_thread("thread-1") == _snapshot()


def test_root_store_rejects_identity_revision_and_duplicate_thread() -> None:
    store = InMemoryRootCheckpointStore()
    store.create(_snapshot())
    with pytest.raises(RootStoreConflict):
        store.create(_snapshot())
    with pytest.raises(RootStoreError, match="increment revision"):
        store.compare_and_swap(
            "root-1",
            expected_revision=0,
            snapshot=replace(_snapshot(), revision=2),
            event=AuditEvent("event", "bad", 2),
        )
    with pytest.raises(RootStoreError, match="identity"):
        store.compare_and_swap(
            "root-1",
            expected_revision=0,
            snapshot=replace(_snapshot(), revision=1, thread_id="changed"),
            event=AuditEvent("event", "bad", 1),
        )


def test_sqlite_root_store_rejects_corrupt_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.sqlite"
    store = SqliteRootCheckpointStore(path)
    store.create(_snapshot())
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE modi_root_runs SET snapshot_json = ? WHERE root_run_id = ?",
            ("not-json", "root-1"),
        )
    with pytest.raises(RootStoreError):
        store.load("root-1")
