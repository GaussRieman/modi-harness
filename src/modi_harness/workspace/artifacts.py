"""Immutable content-addressed blob staging for Task Graph artifacts."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from .._utils import new_ulid
from .errors import WorkspaceError, WorkspacePathError


class ArtifactStoreError(WorkspaceError):
    """A staged or sealed Task Graph blob failed integrity checks."""


@dataclass(frozen=True, slots=True)
class StagedBlobRef:
    attempt_id: str
    staging_path: str
    content_hash: str
    size_bytes: int
    mime_type: str | None
    trust_level: Literal["trusted", "untrusted"]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class SealedBlobRef:
    uri: str
    content_hash: str
    size_bytes: int
    mime_type: str | None
    trust_level: Literal["trusted", "untrusted"]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


class TaskArtifactStore:
    """Stages Attempt bytes and seals them under immutable SHA-256 addresses."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()
        self._staging = self._root / "staging"
        self._blobs = self._root / "blobs" / "sha256"
        self._staging.mkdir(parents=True, exist_ok=True)
        self._blobs.mkdir(parents=True, exist_ok=True)

    def stage(
        self,
        attempt_id: str,
        data: bytes,
        *,
        mime_type: str | None = None,
        trust: Literal["trusted", "untrusted"] = "untrusted",
        metadata: Mapping[str, Any] | None = None,
    ) -> StagedBlobRef:
        safe_attempt = _safe_segment(attempt_id, "attempt_id")
        if not isinstance(data, bytes):
            raise ArtifactStoreError("Task Artifact data must be bytes")
        attempt_dir = self._staging / safe_attempt
        attempt_dir.mkdir(parents=True, exist_ok=True)
        path = attempt_dir / f"{new_ulid()}.stage"
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return StagedBlobRef(
            attempt_id=safe_attempt,
            staging_path=str(path),
            content_hash=_digest(data),
            size_bytes=len(data),
            mime_type=mime_type,
            trust_level=trust,
            metadata=metadata or {},
        )

    def seal(self, staged: StagedBlobRef) -> SealedBlobRef:
        path = Path(staged.staging_path).resolve()
        attempt_dir = (self._staging / _safe_segment(staged.attempt_id, "attempt_id")).resolve()
        if not _is_within(path, attempt_dir):
            raise WorkspacePathError("staged blob resolves outside its Attempt partition")
        data = _read(path)
        _verify(data, staged.content_hash, staged.size_bytes)
        final = self._blob_path(staged.content_hash)
        final.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, final)
        except FileExistsError:
            _verify(_read(final), staged.content_hash, staged.size_bytes)
        finally:
            path.unlink(missing_ok=True)
        final.chmod(0o444)
        return SealedBlobRef(
            uri=f"blob://sha256/{staged.content_hash}",
            content_hash=staged.content_hash,
            size_bytes=staged.size_bytes,
            mime_type=staged.mime_type,
            trust_level=staged.trust_level,
            metadata=staged.metadata,
        )

    def read_verified(self, sealed: SealedBlobRef) -> bytes:
        prefix = "blob://sha256/"
        if not sealed.uri.startswith(prefix):
            raise ArtifactStoreError("unsupported Task Artifact URI")
        uri_hash = sealed.uri.removeprefix(prefix)
        if uri_hash != sealed.content_hash:
            raise ArtifactStoreError("Task Artifact URI/hash mismatch")
        data = _read(self._blob_path(uri_hash))
        _verify(data, sealed.content_hash, sealed.size_bytes)
        return data

    def read_uri_verified(self, uri: str) -> bytes:
        """Read a sealed blob when its content-addressed URI is the durable reference."""

        prefix = "blob://sha256/"
        if not uri.startswith(prefix):
            raise ArtifactStoreError("unsupported Task Artifact URI")
        content_hash = uri.removeprefix(prefix)
        data = _read(self._blob_path(content_hash))
        _verify(data, content_hash, len(data))
        return data

    def _blob_path(self, content_hash: str) -> Path:
        if len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash):
            raise ArtifactStoreError("content_hash must be lowercase SHA-256 hex")
        return self._blobs / content_hash[:2] / content_hash[2:]


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ArtifactStoreError(f"Task Artifact bytes unavailable: {exc}") from exc


def _verify(data: bytes, expected_hash: str, expected_size: int) -> None:
    if len(data) != expected_size:
        raise ArtifactStoreError("Task Artifact size mismatch")
    if _digest(data) != expected_hash:
        raise ArtifactStoreError("Task Artifact hash mismatch")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_segment(value: str, source: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise WorkspacePathError(f"{source} must be a safe non-empty path segment")
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1:
        raise WorkspacePathError(f"{source} must be a safe non-empty path segment")
    return value


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


__all__ = [
    "ArtifactStoreError",
    "SealedBlobRef",
    "StagedBlobRef",
    "TaskArtifactStore",
]
