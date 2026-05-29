"""Workspace Manager implementation.

Each run owns a directory ``<workspace_root>/<run_id>/`` containing six
sub-directories. All writes resolve under this directory; symlink escape and
``..`` traversal are rejected.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from .._utils import new_ulid
from ..types import WorkspaceKind, WorkspaceRef
from .errors import WorkspacePathError, WorkspaceRunMissingError

_SUBDIRS: tuple[WorkspaceKind, ...] = (
    "input",
    "state",
    "reference",
    "artifact",
    "draft",
    "log",
)

# Directory names use plurals (per architecture/07-workspace-manager.md).
_DIRNAME: dict[WorkspaceKind, str] = {
    "input": "input",
    "state": "state",
    "reference": "references",
    "artifact": "artifacts",
    "draft": "drafts",
    "log": "logs",
}


class WorkspaceManager:
    """Owns the on-disk layout of run-scoped storage."""

    def __init__(self, workspace_root: Path | str) -> None:
        self._root = Path(workspace_root)

    # ------------------------------------------------------------------
    # run lifecycle
    # ------------------------------------------------------------------

    def create_run(self, run_id: str) -> Path:
        run_dir = self._run_dir(run_id, must_exist=False)
        run_dir.mkdir(parents=True, exist_ok=True)
        for kind in _SUBDIRS:
            (run_dir / _DIRNAME[kind]).mkdir(exist_ok=True)
        return run_dir

    def create_child_run(self, parent_run_id: str, child_run_id: str) -> Path:
        """Create a child run workspace under the parent's ``sub/`` directory.

        Layout: ``<root>/<parent>/sub/<child>/{input,artifacts,drafts,logs}``.
        Path traversal checks treat the child as its own root once created.
        """
        parent_dir = self._run_dir(parent_run_id, must_exist=False)
        sub_root = parent_dir / "sub"
        sub_root.mkdir(parents=True, exist_ok=True)
        child_dir = sub_root / child_run_id
        child_dir.mkdir(parents=True, exist_ok=True)
        for kind in _SUBDIRS:
            (child_dir / _DIRNAME[kind]).mkdir(exist_ok=True)
        return child_dir

    def run_exists(self, run_id: str) -> bool:
        return self._run_dir(run_id, must_exist=False).is_dir()

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def save_input(
        self,
        run_id: str,
        name: str,
        data: bytes,
        *,
        trust: Literal["trusted", "untrusted"],
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceRef:
        return self._write_bytes(run_id, "input", name, data, trust, mime_type, metadata)

    def save_artifact(
        self,
        run_id: str,
        name: str,
        data: bytes,
        *,
        trust: Literal["trusted", "untrusted"],
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceRef:
        artifact_id = new_ulid()
        ref = self._write_bytes(run_id, "artifact", name, data, trust, mime_type, metadata)
        ref["artifact_id"] = artifact_id
        return ref

    def save_draft(
        self,
        run_id: str,
        name: str,
        data: dict[str, Any] | bytes | str,
    ) -> WorkspaceRef:
        if isinstance(data, dict):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            mime = "application/json"
        elif isinstance(data, str):
            payload = data.encode("utf-8")
            mime = "text/plain"
        else:
            payload = data
            mime = None
        return self._write_bytes(run_id, "draft", name, payload, "trusted", mime, None)

    def append_log(self, run_id: str, kind: str, line: str) -> Path:
        path = self._safe_join(run_id, "log", f"{kind}.jsonl")
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            if not line.endswith("\n"):
                fh.write("\n")
        return path

    def write_payload(self, run_id: str, blob: bytes) -> str:
        """Write a large trace payload under logs/payloads/. Returns the relative path."""
        rel = Path("logs") / "payloads" / f"{new_ulid()}.bin"
        full = self._safe_join(run_id, *rel.parts)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(blob)
        return str(rel)

    # ------------------------------------------------------------------
    # index
    # ------------------------------------------------------------------

    def index_workspace(self, run_id: str) -> list[WorkspaceRef]:
        run_dir = self._run_dir(run_id)
        out: list[WorkspaceRef] = []
        for kind in _SUBDIRS:
            sub = run_dir / _DIRNAME[kind]
            if not sub.is_dir():
                continue
            for entry in sorted(sub.rglob("*")):
                if not entry.is_file():
                    continue
                # state.json and state/snapshots/* are state kind.
                out.append(
                    WorkspaceRef(
                        run_id=run_id,
                        kind=kind,
                        path=str(entry),
                        artifact_id=None,
                        mime_type=None,
                        trust_level="trusted",
                        size_bytes=entry.stat().st_size,
                        metadata={},
                    )
                )
        return out

    # ------------------------------------------------------------------
    # locking
    # ------------------------------------------------------------------

    @contextmanager
    def acquire_run_lock(self, run_id: str) -> Iterator[None]:
        run_dir = self._run_dir(run_id)
        lock = run_dir / ".lock"
        # Best-effort exclusive create. A full advisory lock would require fcntl;
        # for V0.1 in-process callers, file existence is sufficient.
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # path resolution
    # ------------------------------------------------------------------

    def _run_dir(self, run_id: str, *, must_exist: bool = True) -> Path:
        run_dir = (self._root / run_id).resolve()
        if must_exist and not run_dir.is_dir():
            raise WorkspaceRunMissingError(f"workspace for run '{run_id}' does not exist")
        return run_dir

    def _safe_join(self, run_id: str, kind: WorkspaceKind | str, *parts: str) -> Path:
        if isinstance(kind, str) and kind in _DIRNAME:
            sub = _DIRNAME[kind]  # type: ignore[index]
        else:
            sub = str(kind)

        run_dir = self._run_dir(run_id)
        target = (run_dir / sub).joinpath(*parts)

        # Reject absolute components and explicit `..` segments. Traversal that
        # happens to resolve to a sibling subdir under the same run is still an
        # intent we don't honor.
        for part in parts:
            pp = Path(part)
            if pp.is_absolute():
                raise WorkspacePathError(f"absolute path component not allowed: {part!r}")
            if ".." in pp.parts:
                raise WorkspacePathError(f"parent traversal not allowed: {part!r}")

        try:
            resolved = target.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise WorkspacePathError(str(exc)) from exc

        if not _is_within(resolved, run_dir):
            raise WorkspacePathError(
                f"target {resolved} resolves outside run workspace {run_dir}"
            )
        return resolved

    def _write_bytes(
        self,
        run_id: str,
        kind: WorkspaceKind,
        name: str,
        data: bytes,
        trust: Literal["trusted", "untrusted"],
        mime_type: str | None,
        metadata: dict[str, Any] | None,
    ) -> WorkspaceRef:
        path = self._safe_join(run_id, kind, *Path(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_bytes(path, data)
        return WorkspaceRef(
            run_id=run_id,
            kind=kind,
            path=str(path),
            artifact_id=None,
            mime_type=mime_type,
            trust_level=trust,
            size_bytes=len(data),
            metadata=metadata or {},
        )

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent.resolve())
    except ValueError:
        return False
    return True
