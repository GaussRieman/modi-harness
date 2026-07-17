"""Unit tests for build_checkpointer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from modi_harness.checkpoint import (
    CheckpointConfigError,
    InMemoryRootCheckpointStore,
    SqliteRootCheckpointStore,
    build_checkpointer,
    build_root_checkpoint_store,
)
from modi_harness.config import Settings


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.startswith("MODI_"):
            monkeypatch.delenv(k, raising=False)


def test_memory_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_CHECKPOINT_BACKEND", "memory")
    s = Settings(_env_file=None)
    cp = build_checkpointer(s)
    assert isinstance(cp, MemorySaver)
    assert isinstance(build_root_checkpoint_store(s), InMemoryRootCheckpointStore)


def test_sqlite_backend_creates_parent_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear(monkeypatch)
    db = tmp_path / "nested" / "checkpoint.sqlite"
    monkeypatch.setenv("MODI_CHECKPOINT_BACKEND", "sqlite")
    monkeypatch.setenv("MODI_CHECKPOINT_SQLITE_PATH", str(db))
    s = Settings(_env_file=None)
    cp = build_checkpointer(s)
    assert isinstance(cp, SqliteSaver)
    root = build_root_checkpoint_store(s)
    assert isinstance(root, SqliteRootCheckpointStore)
    assert db.parent.exists()


def test_postgres_requires_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_CHECKPOINT_BACKEND", "postgres")
    s = Settings(_env_file=None)
    with pytest.raises(CheckpointConfigError, match="POSTGRES_DSN"):
        build_checkpointer(s)
    with pytest.raises(CheckpointConfigError, match="does not support"):
        build_root_checkpoint_store(s)


def test_unknown_backend_rejected_by_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("MODI_CHECKPOINT_BACKEND", "redis")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
