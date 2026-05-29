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

import sqlite3
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from ..config.settings import Settings
from .errors import CheckpointConfigError


def build_checkpointer(settings: Settings) -> BaseCheckpointSaver:
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
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:  # pragma: no cover
            raise CheckpointConfigError(
                "langgraph-checkpoint-postgres is not installed; "
                "install it or switch MODI_CHECKPOINT_BACKEND."
            ) from exc
        saver = PostgresSaver.from_conn_string(dsn)
        saver.setup()
        return saver
    raise CheckpointConfigError(f"unknown checkpoint backend: {backend!r}")
