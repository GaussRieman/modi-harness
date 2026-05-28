"""Tests for WorkspaceManager."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from modi_harness.workspace import (
    WorkspaceManager,
    WorkspacePathError,
    WorkspaceRunMissingError,
)


def _make_manager(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(workspace_root=tmp_path / "ws")


def test_create_run_makes_all_subdirs(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    base = tmp_path / "ws" / "r1"
    for sub in ("input", "state", "references", "artifacts", "drafts", "logs"):
        assert (base / sub).is_dir()


def test_save_input_writes_with_trust(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    ref = wm.save_input("r1", "task.json", b'{"hello":1}', trust="trusted")
    assert ref["kind"] == "input"
    assert ref["trust_level"] == "trusted"
    assert (tmp_path / "ws" / "r1" / "input" / "task.json").read_bytes() == b'{"hello":1}'


def test_save_state_is_atomic(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    state = {"run_id": "r1", "step_count": 1}
    wm.save_state("r1", state)
    on_disk = json.loads((tmp_path / "ws" / "r1" / "state" / "state.json").read_text())
    assert on_disk == state


def test_snapshot_state_writes_per_step(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    wm.snapshot_state("r1", 0, {"step_count": 0})
    wm.snapshot_state("r1", 1, {"step_count": 1})
    snapshots = sorted((tmp_path / "ws" / "r1" / "state" / "snapshots").iterdir())
    assert [s.name for s in snapshots] == ["00000000.json", "00000001.json"]


def test_save_artifact_returns_ref(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    ref = wm.save_artifact("r1", "out.txt", b"hi", trust="untrusted", mime_type="text/plain")
    assert ref["kind"] == "artifact"
    assert ref["trust_level"] == "untrusted"
    assert ref["mime_type"] == "text/plain"
    assert ref["artifact_id"]


def test_save_draft(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    ref = wm.save_draft("r1", "draft.json", {"x": 1})
    on_disk = json.loads((tmp_path / "ws" / "r1" / "drafts" / "draft.json").read_text())
    assert on_disk == {"x": 1}
    assert ref["kind"] == "draft"


def test_append_log(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    wm.append_log("r1", "trace", '{"event":"x"}')
    wm.append_log("r1", "trace", '{"event":"y"}')
    log_path = tmp_path / "ws" / "r1" / "logs" / "trace.jsonl"
    assert log_path.read_text().splitlines() == ['{"event":"x"}', '{"event":"y"}']


def test_write_payload_returns_ref_inside_run(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    ref = wm.write_payload("r1", b'{"big": "blob"}')
    assert ref.startswith("logs/payloads/")
    full = tmp_path / "ws" / "r1" / ref
    assert full.read_bytes() == b'{"big": "blob"}'


def test_index_workspace(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    wm.save_input("r1", "task.json", b"{}", trust="trusted")
    wm.save_artifact("r1", "out.txt", b"hi", trust="untrusted")
    wm.save_draft("r1", "d.json", {"k": 1})
    index = wm.index_workspace("r1")
    kinds = {ref["kind"] for ref in index}
    assert {"input", "artifact", "draft"}.issubset(kinds)


def test_path_traversal_rejected(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    with pytest.raises(WorkspacePathError):
        wm.save_input("r1", "../escape.txt", b"x", trust="trusted")
    with pytest.raises(WorkspacePathError):
        wm.save_artifact("r1", "/abs/path", b"x", trust="trusted")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    # Create a symlink inside input/ that points outside the workspace.
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "ws" / "r1" / "input" / "link"
    link.symlink_to(outside)
    with pytest.raises(WorkspacePathError):
        wm.save_input("r1", "link/x", b"y", trust="trusted")


def test_missing_run_raises(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    with pytest.raises(WorkspaceRunMissingError):
        wm.save_input("nope", "a", b"b", trust="trusted")


def test_run_lock(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    with wm.acquire_run_lock("r1"):
        assert (tmp_path / "ws" / "r1" / ".lock").exists()
    # Lock file is removed on release.
    assert not (tmp_path / "ws" / "r1" / ".lock").exists()


def test_two_runs_independent_locks(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("a")
    wm.create_run("b")
    with wm.acquire_run_lock("a"):
        with wm.acquire_run_lock("b"):
            assert os.path.exists(tmp_path / "ws" / "a" / ".lock")
            assert os.path.exists(tmp_path / "ws" / "b" / ".lock")
