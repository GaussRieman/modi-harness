"""Durable child Workflow identity and checkpoint aggregates."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote

from .._utils import compute_fingerprint
from ..types import WorkspaceRef
from ..workspace import ChildWorkspace, WorkspaceManager
from .context import ContextManifest

ChildRunStatus = Literal[
    "created",
    "running",
    "waiting",
    "completed",
    "failed",
    "cancelled",
    "orphaned",
    "reconciliation_required",
]
_CHILD_RUN_STATUSES = frozenset(
    {
        "created",
        "running",
        "waiting",
        "completed",
        "failed",
        "cancelled",
        "orphaned",
        "reconciliation_required",
    }
)


class ChildRunError(RuntimeError):
    """A child binding or checkpoint transition is invalid."""


class ChildCheckpointConflict(ChildRunError):
    """A child checkpoint create or compare-and-swap lost a race."""


@dataclass(frozen=True, slots=True)
class ChildRunBinding:
    root_run_id: str
    parent_run_id: str
    parent_node_id: str
    parent_node_attempt: int
    parent_attempt_id: str
    child_run_id: str
    template_id: str
    template_fingerprint: str
    dispatch_key: str
    context_manifest_ref: str
    context_manifest_fingerprint: str
    parent_execution_contract_fingerprint: str
    child_workflow_fingerprint: str
    child_execution_contract_fingerprint: str
    lease_epoch: int
    lease_token: str
    checkpoint_ns: str
    workspace_partition: str
    fingerprint: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "root_run_id",
            "parent_run_id",
            "parent_node_id",
            "parent_attempt_id",
            "child_run_id",
            "template_id",
            "template_fingerprint",
            "dispatch_key",
            "context_manifest_ref",
            "context_manifest_fingerprint",
            "parent_execution_contract_fingerprint",
            "child_workflow_fingerprint",
            "child_execution_contract_fingerprint",
            "lease_token",
            "checkpoint_ns",
            "workspace_partition",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ChildRunError(f"ChildRun binding {field_name} must be non-empty")
            object.__setattr__(self, field_name, value.strip())
        for field_name in ("parent_node_attempt", "lease_epoch"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ChildRunError(f"ChildRun binding {field_name} must be positive")
        if self.parent_run_id != self.root_run_id:
            raise ChildRunError("V1 child parent run must be the root run")
        if Path(self.context_manifest_ref).is_absolute():
            raise ChildRunError("ContextManifest ref cannot be an absolute host path")
        expected_ns = child_checkpoint_namespace(
            root_run_id=self.root_run_id,
            parent_node_id=self.parent_node_id,
            parent_node_attempt=self.parent_node_attempt,
            parent_attempt_id=self.parent_attempt_id,
            child_run_id=self.child_run_id,
        )
        if self.checkpoint_ns != expected_ns:
            raise ChildRunError("ChildRun checkpoint namespace does not match its binding")
        expected_workspace = child_workspace_partition(self.parent_run_id, self.child_run_id)
        if self.workspace_partition != expected_workspace:
            raise ChildRunError("ChildRun workspace partition does not match its binding")
        expected = compute_fingerprint(self._payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ChildRunError("ChildRun binding fingerprint does not match content")
        object.__setattr__(self, "fingerprint", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "root_run_id": self.root_run_id,
            "parent_run_id": self.parent_run_id,
            "parent_node_id": self.parent_node_id,
            "parent_node_attempt": self.parent_node_attempt,
            "parent_attempt_id": self.parent_attempt_id,
            "child_run_id": self.child_run_id,
            "template_id": self.template_id,
            "template_fingerprint": self.template_fingerprint,
            "dispatch_key": self.dispatch_key,
            "context_manifest_ref": self.context_manifest_ref,
            "context_manifest_fingerprint": self.context_manifest_fingerprint,
            "parent_execution_contract_fingerprint": (self.parent_execution_contract_fingerprint),
            "child_workflow_fingerprint": self.child_workflow_fingerprint,
            "child_execution_contract_fingerprint": (self.child_execution_contract_fingerprint),
            "lease_epoch": self.lease_epoch,
            "lease_token": self.lease_token,
            "checkpoint_ns": self.checkpoint_ns,
            "workspace_partition": self.workspace_partition,
        }

    def snapshot(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}

    @classmethod
    def from_snapshot(cls, raw: Mapping[str, Any]) -> ChildRunBinding:
        return cls(
            root_run_id=_string(raw, "root_run_id"),
            parent_run_id=_string(raw, "parent_run_id"),
            parent_node_id=_string(raw, "parent_node_id"),
            parent_node_attempt=_integer(raw, "parent_node_attempt"),
            parent_attempt_id=_string(raw, "parent_attempt_id"),
            child_run_id=_string(raw, "child_run_id"),
            template_id=_string(raw, "template_id"),
            template_fingerprint=_string(raw, "template_fingerprint"),
            dispatch_key=_string(raw, "dispatch_key"),
            context_manifest_ref=_string(raw, "context_manifest_ref"),
            context_manifest_fingerprint=_string(raw, "context_manifest_fingerprint"),
            parent_execution_contract_fingerprint=_string(
                raw, "parent_execution_contract_fingerprint"
            ),
            child_workflow_fingerprint=_string(raw, "child_workflow_fingerprint"),
            child_execution_contract_fingerprint=_string(
                raw, "child_execution_contract_fingerprint"
            ),
            lease_epoch=_integer(raw, "lease_epoch"),
            lease_token=_string(raw, "lease_token"),
            checkpoint_ns=_string(raw, "checkpoint_ns"),
            workspace_partition=_string(raw, "workspace_partition"),
            fingerprint=_string(raw, "fingerprint"),
        )


@dataclass(frozen=True, slots=True)
class ChildAuditEvent:
    event_id: str
    event_type: str
    child_revision: int
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.event_id or not self.event_type:
            raise ChildRunError("Child audit event identity must be non-empty")
        if (
            not isinstance(self.child_revision, int)
            or isinstance(self.child_revision, bool)
            or self.child_revision < 0
        ):
            raise ChildRunError("Child audit revision cannot be negative")
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class ChildRunSnapshot:
    binding: ChildRunBinding
    revision: int
    status: ChildRunStatus
    context_manifest: Mapping[str, Any]
    workflow_state: Mapping[str, Any]
    launch_handle_id: str | None = None
    last_event: ChildAuditEvent | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 0
        ):
            raise ChildRunError("Child snapshot revision must be a non-negative integer")
        if self.status not in _CHILD_RUN_STATUSES:
            raise ChildRunError(f"unsupported child status {self.status!r}")
        object.__setattr__(self, "context_manifest", _freeze_mapping(self.context_manifest))
        object.__setattr__(self, "workflow_state", _freeze_mapping(self.workflow_state))
        manifest = ContextManifest.from_snapshot(self.context_manifest)
        if manifest.fingerprint != self.binding.context_manifest_fingerprint:
            raise ChildRunError("Child snapshot ContextManifest fingerprint mismatch")
        if (
            manifest.root_run_id != self.binding.root_run_id
            or manifest.parent_run_id != self.binding.parent_run_id
            or manifest.parent_node_id != self.binding.parent_node_id
            or manifest.parent_node_attempt != self.binding.parent_node_attempt
        ):
            raise ChildRunError("Child snapshot parent binding mismatch")
        if manifest.task_attempt_id != self.binding.parent_attempt_id:
            raise ChildRunError("Child snapshot ContextManifest Attempt mismatch")
        if manifest.child_run_id != self.binding.child_run_id:
            raise ChildRunError("Child snapshot ContextManifest child run mismatch")
        if manifest.template_fingerprint != self.binding.template_fingerprint:
            raise ChildRunError("Child snapshot template fingerprint mismatch")
        if manifest.template_id != self.binding.template_id:
            raise ChildRunError("Child snapshot template ID mismatch")
        if (
            manifest.child_workflow_fingerprint != self.binding.child_workflow_fingerprint
            or manifest.child_execution_contract_fingerprint
            != self.binding.child_execution_contract_fingerprint
        ):
            raise ChildRunError("Child snapshot Workflow binding mismatch")

    def snapshot(self) -> dict[str, Any]:
        return {
            "binding": self.binding.snapshot(),
            "revision": self.revision,
            "status": self.status,
            "context_manifest": _plain(self.context_manifest),
            "workflow_state": _plain(self.workflow_state),
            "launch_handle_id": self.launch_handle_id,
            "last_event": None if self.last_event is None else _plain(self.last_event),
        }

    @classmethod
    def from_snapshot(cls, raw: Mapping[str, Any]) -> ChildRunSnapshot:
        event_raw = raw.get("last_event")
        event = None
        if event_raw is not None:
            event_value = _mapping(event_raw, "last_event")
            event = ChildAuditEvent(
                event_id=_string(event_value, "event_id"),
                event_type=_string(event_value, "event_type"),
                child_revision=_integer(event_value, "child_revision"),
                payload=_mapping(event_value.get("payload", {}), "last_event.payload"),
            )
        return cls(
            binding=ChildRunBinding.from_snapshot(_mapping(raw.get("binding"), "binding")),
            revision=_integer(raw, "revision"),
            status=cast(ChildRunStatus, _string(raw, "status")),
            context_manifest=_mapping(raw.get("context_manifest"), "context_manifest"),
            workflow_state=_mapping(raw.get("workflow_state", {}), "workflow_state"),
            launch_handle_id=cast(str | None, raw.get("launch_handle_id")),
            last_event=event,
        )


class ChildCheckpointStore(Protocol):
    def load(self, checkpoint_ns: str) -> ChildRunSnapshot | None: ...

    def load_by_child_run_id(self, child_run_id: str) -> ChildRunSnapshot | None: ...

    def list_by_root(self, root_run_id: str) -> tuple[ChildRunSnapshot, ...]: ...

    def create_or_load(self, snapshot: ChildRunSnapshot) -> ChildRunSnapshot: ...

    def compare_and_swap(
        self,
        checkpoint_ns: str,
        *,
        expected_revision: int,
        snapshot: ChildRunSnapshot,
        event: ChildAuditEvent,
    ) -> ChildRunSnapshot: ...


class InMemoryChildCheckpointStore:
    def __init__(self) -> None:
        self._children: dict[str, ChildRunSnapshot] = {}
        self._run_ids: dict[str, str] = {}
        self._lock = RLock()

    def load(self, checkpoint_ns: str) -> ChildRunSnapshot | None:
        with self._lock:
            return self._children.get(checkpoint_ns)

    def load_by_child_run_id(self, child_run_id: str) -> ChildRunSnapshot | None:
        with self._lock:
            checkpoint_ns = self._run_ids.get(child_run_id)
            return None if checkpoint_ns is None else self._children[checkpoint_ns]

    def list_by_root(self, root_run_id: str) -> tuple[ChildRunSnapshot, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._children.values()
                        if item.binding.root_run_id == root_run_id
                    ),
                    key=lambda item: item.binding.checkpoint_ns,
                )
            )

    def create_or_load(self, snapshot: ChildRunSnapshot) -> ChildRunSnapshot:
        if snapshot.revision != 0 or snapshot.status != "created":
            raise ChildRunError("initial child checkpoint must be created at revision 0")
        key = snapshot.binding.checkpoint_ns
        with self._lock:
            existing = self._children.get(key)
            if existing is not None:
                _validate_idempotent_create(existing, snapshot)
                return existing
            collision = self._run_ids.get(snapshot.binding.child_run_id)
            if collision is not None and collision != key:
                raise ChildCheckpointConflict("child run ID already has another namespace")
            self._children[key] = snapshot
            self._run_ids[snapshot.binding.child_run_id] = key
            return snapshot

    def compare_and_swap(
        self,
        checkpoint_ns: str,
        *,
        expected_revision: int,
        snapshot: ChildRunSnapshot,
        event: ChildAuditEvent,
    ) -> ChildRunSnapshot:
        with self._lock:
            current = self._children.get(checkpoint_ns)
            if current is None:
                raise ChildRunError(f"unknown child checkpoint {checkpoint_ns!r}")
            committed = _validate_commit(current, expected_revision, snapshot, event)
            self._children[checkpoint_ns] = committed
            return committed


class SqliteChildCheckpointStore:
    """SQLite child checkpoint store with one row per deterministic namespace."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._lock = RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS modi_child_runs (
                checkpoint_ns TEXT PRIMARY KEY,
                child_run_id TEXT NOT NULL UNIQUE,
                root_run_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_modi_child_runs_root ON modi_child_runs(root_run_id)"
        )
        self._conn.commit()

    def load(self, checkpoint_ns: str) -> ChildRunSnapshot | None:
        return self._load_one("checkpoint_ns", checkpoint_ns)

    def load_by_child_run_id(self, child_run_id: str) -> ChildRunSnapshot | None:
        return self._load_one("child_run_id", child_run_id)

    def list_by_root(self, root_run_id: str) -> tuple[ChildRunSnapshot, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot_json FROM modi_child_runs WHERE root_run_id = ? "
                "ORDER BY checkpoint_ns",
                (root_run_id,),
            ).fetchall()
        return tuple(ChildRunSnapshot.from_snapshot(json.loads(str(row[0]))) for row in rows)

    def create_or_load(self, snapshot: ChildRunSnapshot) -> ChildRunSnapshot:
        if snapshot.revision != 0 or snapshot.status != "created":
            raise ChildRunError("initial child checkpoint must be created at revision 0")
        binding = snapshot.binding
        payload = _encode(snapshot)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT snapshot_json FROM modi_child_runs WHERE checkpoint_ns = ?",
                    (binding.checkpoint_ns,),
                ).fetchone()
                if row is not None:
                    existing = ChildRunSnapshot.from_snapshot(json.loads(str(row[0])))
                    _validate_idempotent_create(existing, snapshot)
                    self._conn.commit()
                    return existing
                self._conn.execute(
                    "INSERT INTO modi_child_runs("
                    "checkpoint_ns, child_run_id, root_run_id, revision, snapshot_json"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        binding.checkpoint_ns,
                        binding.child_run_id,
                        binding.root_run_id,
                        snapshot.revision,
                        payload,
                    ),
                )
                self._conn.commit()
                return snapshot
            except ChildRunError:
                self._conn.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise ChildCheckpointConflict("child checkpoint identity already exists") from exc
            except (json.JSONDecodeError, TypeError, ValueError, sqlite3.Error) as exc:
                self._conn.rollback()
                raise ChildRunError(str(exc)) from exc

    def compare_and_swap(
        self,
        checkpoint_ns: str,
        *,
        expected_revision: int,
        snapshot: ChildRunSnapshot,
        event: ChildAuditEvent,
    ) -> ChildRunSnapshot:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT snapshot_json FROM modi_child_runs WHERE checkpoint_ns = ?",
                    (checkpoint_ns,),
                ).fetchone()
                if row is None:
                    raise ChildRunError(f"unknown child checkpoint {checkpoint_ns!r}")
                current = ChildRunSnapshot.from_snapshot(json.loads(str(row[0])))
                committed = _validate_commit(current, expected_revision, snapshot, event)
                cursor = self._conn.execute(
                    "UPDATE modi_child_runs SET revision = ?, snapshot_json = ? "
                    "WHERE checkpoint_ns = ? AND revision = ?",
                    (
                        committed.revision,
                        _encode(committed),
                        checkpoint_ns,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ChildCheckpointConflict("stale child revision")
                self._conn.commit()
                return committed
            except (ChildCheckpointConflict, ChildRunError):
                self._conn.rollback()
                raise
            except (json.JSONDecodeError, TypeError, ValueError, sqlite3.Error) as exc:
                self._conn.rollback()
                raise ChildRunError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _load_one(self, field: str, value: str) -> ChildRunSnapshot | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT snapshot_json FROM modi_child_runs WHERE {field} = ?",
                (value,),
            ).fetchone()
        if row is None:
            return None
        try:
            return ChildRunSnapshot.from_snapshot(json.loads(str(row[0])))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ChildRunError(str(exc)) from exc


def initial_child_snapshot(
    binding: ChildRunBinding,
    manifest: ContextManifest,
) -> ChildRunSnapshot:
    if manifest.fingerprint != binding.context_manifest_fingerprint:
        raise ChildRunError("ChildRun binding does not match ContextManifest fingerprint")
    return ChildRunSnapshot(
        binding=binding,
        revision=0,
        status="created",
        context_manifest=manifest.snapshot(),
        workflow_state={},
    )


def prepare_child_run(
    *,
    checkpoints: ChildCheckpointStore,
    workspace: WorkspaceManager,
    binding: ChildRunBinding,
    manifest: ContextManifest,
    authorized_refs: Iterable[WorkspaceRef] = (),
) -> tuple[ChildRunSnapshot, ChildWorkspace]:
    partition = workspace.for_child(
        binding.parent_run_id,
        binding.child_run_id,
        authorized_refs=authorized_refs,
    )
    partition.create()
    snapshot = checkpoints.create_or_load(initial_child_snapshot(binding, manifest))
    return snapshot, partition


def child_checkpoint_namespace(
    *,
    root_run_id: str,
    parent_node_id: str,
    parent_node_attempt: int,
    parent_attempt_id: str,
    child_run_id: str,
) -> str:
    if parent_node_attempt < 1:
        raise ChildRunError("parent_node_attempt must be positive")
    return (
        f"roots/{_segment(root_run_id)}/nodes/{_segment(parent_node_id)}/"
        f"{parent_node_attempt}/attempts/{_segment(parent_attempt_id)}/"
        f"children/{_segment(child_run_id)}/workflow"
    )


def child_workspace_partition(parent_run_id: str, child_run_id: str) -> str:
    return f"runs/{_segment(parent_run_id)}/sub/{_segment(child_run_id)}"


def _validate_idempotent_create(
    existing: ChildRunSnapshot,
    requested: ChildRunSnapshot,
) -> None:
    if existing.binding.fingerprint != requested.binding.fingerprint:
        raise ChildCheckpointConflict("child checkpoint binding mismatch")
    if existing.context_manifest != requested.context_manifest:
        raise ChildCheckpointConflict("child checkpoint ContextManifest mismatch")


def _validate_commit(
    current: ChildRunSnapshot,
    expected_revision: int,
    snapshot: ChildRunSnapshot,
    event: ChildAuditEvent,
) -> ChildRunSnapshot:
    if current.revision != expected_revision:
        raise ChildCheckpointConflict(
            f"stale child revision {expected_revision}; current is {current.revision}"
        )
    if snapshot.binding.fingerprint != current.binding.fingerprint:
        raise ChildRunError("child binding cannot change during commit")
    if snapshot.context_manifest != current.context_manifest:
        raise ChildRunError("ContextManifest cannot change during child commit")
    if snapshot.revision != expected_revision + 1:
        raise ChildRunError("child CAS must increment revision exactly once")
    if event.child_revision != snapshot.revision:
        raise ChildRunError("child event revision must match committed revision")
    return ChildRunSnapshot(
        binding=snapshot.binding,
        revision=snapshot.revision,
        status=snapshot.status,
        context_manifest=snapshot.context_manifest,
        workflow_state=snapshot.workflow_state,
        launch_handle_id=snapshot.launch_handle_id,
        last_event=event,
    )


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ChildRunError("checkpoint identity segments must be non-empty")
    return quote(value.strip(), safe="")


def _encode(snapshot: ChildRunSnapshot) -> str:
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
    if isinstance(value, tuple | list):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ChildRunError(f"{source} must be a mapping")
    return cast(Mapping[str, Any], value)


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ChildRunError(f"{key} must be a non-empty string")
    return value


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ChildRunError(f"{key} must be an integer")
    return value


__all__ = [
    "ChildAuditEvent",
    "ChildCheckpointConflict",
    "ChildCheckpointStore",
    "ChildRunBinding",
    "ChildRunError",
    "ChildRunSnapshot",
    "ChildRunStatus",
    "InMemoryChildCheckpointStore",
    "SqliteChildCheckpointStore",
    "child_checkpoint_namespace",
    "child_workspace_partition",
    "initial_child_snapshot",
    "prepare_child_run",
]
