"""Build a LangGraph checkpointer from Modi Settings.

Backends:

- ``memory``  — :class:`langgraph.checkpoint.memory.MemorySaver`. Tests only.
- ``sqlite``  — :class:`langgraph.checkpoint.sqlite.SqliteSaver`. Default.
                Single-host. WAL mode under the hood.
- ``postgres``— :class:`langgraph.checkpoint.postgres.PostgresSaver`. Opt-in
                via ``MODI_CHECKPOINT_BACKEND=postgres``; lazy-imported so the
                psycopg dependency is not required for sqlite users.
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from ..config.settings import Settings
from ..long_task.child import (
    ChildCheckpointStore,
    InMemoryChildCheckpointStore,
    SqliteChildCheckpointStore,
)
from .errors import CheckpointConfigError
from .root import InMemoryRootCheckpointStore, RootCheckpointStore, SqliteRootCheckpointStore


def build_checkpointer(settings: Settings) -> BaseCheckpointSaver[Any]:
    backend = settings.checkpoint.backend
    if backend == "memory":
        return MemorySaver()
    if backend == "sqlite":
        path = Path(settings.checkpoint.sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver
    if backend == "postgres":
        dsn = settings.checkpoint.postgres_dsn
        if not dsn:
            raise CheckpointConfigError(
                "MODI_CHECKPOINT_POSTGRES_DSN must be set when backend=postgres"
            )
        try:
            postgres = importlib.import_module("langgraph.checkpoint.postgres")
        except ImportError as exc:  # pragma: no cover
            raise CheckpointConfigError(
                "langgraph-checkpoint-postgres is not installed; "
                "install it or switch MODI_CHECKPOINT_BACKEND."
            ) from exc
        saver = postgres.PostgresSaver.from_conn_string(dsn)
        saver.setup()
        return saver  # type: ignore[no-any-return]
    raise CheckpointConfigError(f"unknown checkpoint backend: {backend!r}")


def build_root_checkpoint_store(settings: Settings) -> RootCheckpointStore:
    """Build the durable CAS store required by Task-Graph-enabled Workflows."""

    backend = settings.checkpoint.backend
    if backend == "memory":
        return InMemoryRootCheckpointStore()
    if backend == "sqlite":
        return SqliteRootCheckpointStore(settings.checkpoint.sqlite_path)
    if backend == "postgres":
        raise CheckpointConfigError(
            "Task Graph root CAS does not support the postgres backend in V1"
        )
    raise CheckpointConfigError(f"unknown checkpoint backend: {backend!r}")


def build_child_checkpoint_store(settings: Settings) -> ChildCheckpointStore:
    """Build the durable store for independently recoverable child Workflows."""

    backend = settings.checkpoint.backend
    if backend == "memory":
        return InMemoryChildCheckpointStore()
    if backend == "sqlite":
        return SqliteChildCheckpointStore(settings.checkpoint.sqlite_path)
    if backend == "postgres":
        raise CheckpointConfigError(
            "Task Graph child checkpoint storage does not support the postgres backend in V1"
        )
    raise CheckpointConfigError(f"unknown checkpoint backend: {backend!r}")
