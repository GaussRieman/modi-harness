"""Content-addressed Task Artifact staging tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from modi_harness.workspace import ArtifactStoreError, TaskArtifactStore, WorkspacePathError


def test_stage_seal_and_read_verified(tmp_path: Path) -> None:
    store = TaskArtifactStore(tmp_path / "task-artifacts")
    staged = store.stage(
        "attempt-1",
        b"result",
        mime_type="text/plain",
        metadata={"type": "report"},
    )
    sealed = store.seal(staged)

    assert sealed.uri.startswith("blob://sha256/")
    assert sealed.metadata["type"] == "report"
    assert store.read_verified(sealed) == b"result"
    assert not Path(staged.staging_path).exists()


def test_seal_rejects_staging_toctou(tmp_path: Path) -> None:
    store = TaskArtifactStore(tmp_path / "task-artifacts")
    staged = store.stage("attempt-1", b"original")
    Path(staged.staging_path).write_bytes(b"changed")

    with pytest.raises(ArtifactStoreError, match=r"size mismatch|hash mismatch"):
        store.seal(staged)


def test_same_content_is_create_if_absent_and_never_overwritten(tmp_path: Path) -> None:
    store = TaskArtifactStore(tmp_path / "task-artifacts")
    first = store.seal(store.stage("attempt-1", b"same"))
    second = store.seal(store.stage("attempt-2", b"same"))

    assert first.uri == second.uri
    assert store.read_verified(first) == b"same"


def test_read_rejects_uri_hash_or_byte_tamper(tmp_path: Path) -> None:
    store = TaskArtifactStore(tmp_path / "task-artifacts")
    sealed = store.seal(store.stage("attempt-1", b"safe"))
    with pytest.raises(ArtifactStoreError, match="URI/hash mismatch"):
        store.read_verified(replace(sealed, uri="blob://sha256/" + "0" * 64))

    blob_hash = sealed.content_hash
    blob_path = tmp_path / "task-artifacts" / "blobs" / "sha256" / blob_hash[:2] / blob_hash[2:]
    blob_path.chmod(0o644)
    blob_path.write_bytes(b"evil")
    with pytest.raises(ArtifactStoreError, match="hash mismatch"):
        store.read_verified(sealed)


@pytest.mark.parametrize("attempt_id", ["../escape", "/absolute", "a/b", ".."])
def test_stage_rejects_unsafe_attempt_partition(tmp_path: Path, attempt_id: str) -> None:
    store = TaskArtifactStore(tmp_path / "task-artifacts")
    with pytest.raises(WorkspacePathError):
        store.stage(attempt_id, b"data")
