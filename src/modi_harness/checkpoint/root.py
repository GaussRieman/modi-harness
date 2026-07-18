"""Durable compare-and-swap storage for Task-Graph-enabled root runs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol, cast

from ..long_task import AuditEvent, LongTaskState, long_task_state_from_snapshot
from .errors import CheckpointError


class RootStoreError(CheckpointError):
    """A root snapshot cannot be persisted or decoded."""


class RootStoreConflict(RootStoreError):
    """A root create or revision compare-and-swap lost a race."""


@dataclass(frozen=True, slots=True)
class RootRunSnapshot:
    """One authoritative root Workflow and Task Graph aggregate snapshot."""

    root_run_id: str
    thread_id: str
    revision: int
    workflow_state: Mapping[str, Any]
    long_task_state: LongTaskState | None = None
    last_event: AuditEvent | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_state", _freeze_mapping(self.workflow_state))

    def snapshot(self) -> dict[str, Any]:
        return {
            "root_run_id": self.root_run_id,
            "thread_id": self.thread_id,
            "revision": self.revision,
            "workflow_state": _plain(self.workflow_state),
            "long_task_state": (
                None if self.long_task_state is None else self.long_task_state.snapshot()
            ),
            "last_event": None if self.last_event is None else _plain(self.last_event),
        }

    @classmethod
    def from_snapshot(cls, raw: Mapping[str, Any]) -> RootRunSnapshot:
        event_raw = raw.get("last_event")
        event = None
        if event_raw is not None:
            value = _mapping(event_raw, "last_event")
            event = AuditEvent(
                event_id=_string(value, "event_id"),
                event_type=_string(value, "event_type"),
                root_revision=_integer(value, "root_revision"),
                payload=_mapping(value.get("payload", {}), "last_event.payload"),
            )
        long_raw = raw.get("long_task_state")
        return cls(
            root_run_id=_string(raw, "root_run_id"),
            thread_id=_string(raw, "thread_id"),
            revision=_integer(raw, "revision"),
            workflow_state=_mapping(raw.get("workflow_state"), "workflow_state"),
            long_task_state=(
                None
                if long_raw is None
                else long_task_state_from_snapshot(_mapping(long_raw, "long_task_state"))
            ),
            last_event=event,
        )


class RootCheckpointStore(Protocol):
    def load(self, root_run_id: str) -> RootRunSnapshot | None: ...

    def load_by_thread(self, thread_id: str) -> RootRunSnapshot | None: ...

    def create(self, snapshot: RootRunSnapshot) -> RootRunSnapshot: ...

    def compare_and_swap(
        self,
        root_run_id: str,
        *,
        expected_revision: int,
        snapshot: RootRunSnapshot,
        event: AuditEvent,
    ) -> RootRunSnapshot: ...


class InMemoryRootCheckpointStore:
    """Reference CAS implementation used by tests and in-process sessions."""

    def __init__(self) -> None:
        self._roots: dict[str, RootRunSnapshot] = {}
        self._threads: dict[str, str] = {}
        self._lock = RLock()

    def load(self, root_run_id: str) -> RootRunSnapshot | None:
        with self._lock:
            return self._roots.get(root_run_id)

    def load_by_thread(self, thread_id: str) -> RootRunSnapshot | None:
        with self._lock:
            root_run_id = self._threads.get(thread_id)
            return None if root_run_id is None else self._roots[root_run_id]

    def create(self, snapshot: RootRunSnapshot) -> RootRunSnapshot:
        with self._lock:
            if snapshot.revision != 0:
                raise RootStoreError("root snapshot creation requires revision 0")
            if snapshot.root_run_id in self._roots or snapshot.thread_id in self._threads:
                raise RootStoreConflict("root run or thread already exists")
            self._roots[snapshot.root_run_id] = snapshot
            self._threads[snapshot.thread_id] = snapshot.root_run_id
            return snapshot

    def compare_and_swap(
        self,
        root_run_id: str,
        *,
        expected_revision: int,
        snapshot: RootRunSnapshot,
        event: AuditEvent,
    ) -> RootRunSnapshot:
        with self._lock:
            current = self._roots.get(root_run_id)
            if current is None:
                raise RootStoreError(f"unknown root run {root_run_id!r}")
            committed = _validate_commit(current, expected_revision, snapshot, event)
            self._roots[root_run_id] = committed
            self._threads[committed.thread_id] = root_run_id
            return committed


class SqliteRootCheckpointStore:
    """Single-machine SQLite root aggregate store with expected-revision CAS."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._lock = RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS modi_root_runs (
                root_run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL UNIQUE,
                revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def load(self, root_run_id: str) -> RootRunSnapshot | None:
        return self._load_one("root_run_id", root_run_id)

    def load_by_thread(self, thread_id: str) -> RootRunSnapshot | None:
        return self._load_one("thread_id", thread_id)

    def create(self, snapshot: RootRunSnapshot) -> RootRunSnapshot:
        if snapshot.revision != 0:
            raise RootStoreError("root snapshot creation requires revision 0")
        payload = _encode(snapshot)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "INSERT INTO modi_root_runs(root_run_id, thread_id, revision, snapshot_json) "
                    "VALUES (?, ?, ?, ?)",
                    (snapshot.root_run_id, snapshot.thread_id, snapshot.revision, payload),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise RootStoreConflict("root run or thread already exists") from exc
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise RootStoreError(str(exc)) from exc
        return snapshot

    def compare_and_swap(
        self,
        root_run_id: str,
        *,
        expected_revision: int,
        snapshot: RootRunSnapshot,
        event: AuditEvent,
    ) -> RootRunSnapshot:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT snapshot_json FROM modi_root_runs WHERE root_run_id = ?",
                    (root_run_id,),
                ).fetchone()
                if row is None:
                    raise RootStoreError(f"unknown root run {root_run_id!r}")
                current = RootRunSnapshot.from_snapshot(json.loads(str(row[0])))
                committed = _validate_commit(current, expected_revision, snapshot, event)
                cursor = self._conn.execute(
                    "UPDATE modi_root_runs SET thread_id = ?, revision = ?, snapshot_json = ? "
                    "WHERE root_run_id = ? AND revision = ?",
                    (
                        committed.thread_id,
                        committed.revision,
                        _encode(committed),
                        root_run_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RootStoreConflict("stale root revision")
                self._conn.commit()
                return committed
            except (RootStoreConflict, RootStoreError):
                self._conn.rollback()
                raise
            except (json.JSONDecodeError, TypeError, ValueError, sqlite3.Error) as exc:
                self._conn.rollback()
                raise RootStoreError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _load_one(self, field: str, value: str) -> RootRunSnapshot | None:
        query = f"SELECT snapshot_json FROM modi_root_runs WHERE {field} = ?"
        with self._lock:
            row = self._conn.execute(query, (value,)).fetchone()
        if row is None:
            return None
        try:
            return RootRunSnapshot.from_snapshot(json.loads(str(row[0])))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RootStoreError(str(exc)) from exc


def _validate_commit(
    current: RootRunSnapshot,
    expected_revision: int,
    snapshot: RootRunSnapshot,
    event: AuditEvent,
) -> RootRunSnapshot:
    if current.revision != expected_revision:
        raise RootStoreConflict(
            f"stale root revision {expected_revision}; current is {current.revision}"
        )
    if snapshot.root_run_id != current.root_run_id or snapshot.thread_id != current.thread_id:
        raise RootStoreError("root/thread identity cannot change during commit")
    if snapshot.revision != expected_revision + 1:
        raise RootStoreError("root CAS must increment revision exactly once")
    if event.root_revision != snapshot.revision:
        raise RootStoreError("audit event revision must match committed root revision")
    return RootRunSnapshot(
        root_run_id=snapshot.root_run_id,
        thread_id=snapshot.thread_id,
        revision=snapshot.revision,
        workflow_state=snapshot.workflow_state,
        long_task_state=snapshot.long_task_state,
        last_event=event,
    )


def _encode(snapshot: RootRunSnapshot) -> str:
    return json.dumps(
        snapshot.snapshot(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RootStoreError(f"{source} must be a mapping")
    return cast(Mapping[str, Any], value)


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise RootStoreError(f"{key} must be a non-empty string")
    return value


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RootStoreError(f"{key} must be an integer")
    return value


__all__ = [
    "InMemoryRootCheckpointStore",
    "RootCheckpointStore",
    "RootRunSnapshot",
    "RootStoreConflict",
    "RootStoreError",
    "SqliteRootCheckpointStore",
]
