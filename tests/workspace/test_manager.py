"""Tests for WorkspaceManager."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from modi_harness.types import WorkspaceRef
from modi_harness.workspace import (
    WorkspaceManager,
    WorkspacePathError,
    WorkspaceRunMissingError,
)


def _make_manager(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(workspace_root=tmp_path / "ws")


def test_create_run_makes_only_run_root(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    base = tmp_path / "ws" / "r1"
    assert base.is_dir()
    for sub in ("input", "state", "references", "artifacts", "drafts", "logs"):
        assert not (base / sub).exists()


def test_save_input_writes_with_trust(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")
    ref = wm.save_input("r1", "task.json", b'{"hello":1}', trust="trusted")
    assert ref["kind"] == "input"
    assert ref["trust_level"] == "trusted"
    assert (tmp_path / "ws" / "r1" / "input" / "task.json").read_bytes() == b'{"hello":1}'
    assert not (tmp_path / "ws" / "r1" / "drafts").exists()


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


def test_read_log_returns_run_local_lines_or_empty(tmp_path: Path) -> None:
    wm = _make_manager(tmp_path)
    wm.create_run("r1")

    assert wm.read_log("r1", "steering") == ()
    wm.append_log("r1", "steering", '{"feedback":"focus"}')

    assert wm.read_log("r1", "steering") == ('{"feedback":"focus"}',)


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
    (tmp_path / "ws" / "r1" / "input").mkdir()
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


def test_child_workspace_is_partitioned_and_restart_stable(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.create_run("parent")
    authorized = manager.save_input(
        "parent",
        "allowed.txt",
        b"allowed",
        trust="trusted",
    )
    hidden = manager.save_input(
        "parent",
        "hidden.txt",
        b"hidden",
        trust="trusted",
    )
    child = manager.for_child(
        "parent",
        "child-1",
        authorized_refs=(authorized,),
    )
    child.create()
    child.save_draft("child-1", "result.json", {"ok": True})

    path = tmp_path / "ws" / "parent" / "sub" / "child-1" / "drafts" / "result.json"
    assert json.loads(path.read_text()) == {"ok": True}
    assert {item["run_id"] for item in child.index_workspace("child-1")} == {"child-1"}
    assert child.read_authorized_ref(authorized) == b"allowed"
    with pytest.raises(WorkspacePathError, match="not authorized"):
        child.read_authorized_ref(hidden)
    with pytest.raises(WorkspacePathError, match="another run"):
        child.save_draft("parent", "escape.json", {})

    restored = WorkspaceManager(tmp_path / "ws").for_child("parent", "child-1")
    assert restored.index_workspace("child-1")[0]["path"] == str(path)


def test_child_workspace_cannot_authorize_sibling_child_refs(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.create_run("parent")
    sibling = manager.for_child("parent", "sibling")
    sibling.create()
    sibling_ref = sibling.save_input("sibling", "secret.txt", b"secret", trust="trusted")
    forged_parent_ref = WorkspaceRef(**{**sibling_ref, "run_id": "parent"})

    with pytest.raises(WorkspacePathError, match="outside the parent run"):
        manager.for_child(
            "parent",
            "child-1",
            authorized_refs=(forged_parent_ref,),
        )


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", ".", ".."])
def test_run_identity_traversal_is_rejected(tmp_path: Path, run_id: str) -> None:
    manager = _make_manager(tmp_path)
    with pytest.raises(WorkspacePathError, match="safe non-empty path segment"):
        manager.create_run(run_id)


def test_child_workspace_symlink_escape_is_rejected(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.create_run("parent")
    child = manager.for_child("parent", "child-1")
    child.create()
    outside = tmp_path / "outside-child"
    outside.mkdir()
    input_dir = tmp_path / "ws" / "parent" / "sub" / "child-1" / "input"
    (input_dir / "link").symlink_to(outside)

    with pytest.raises(WorkspacePathError):
        child.save_input("child-1", "link/escape.txt", b"x", trust="trusted")


def test_child_workspace_index_excludes_symlinks(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.create_run("parent")
    child = manager.for_child("parent", "child-1")
    child.create()
    regular = child.save_input("child-1", "regular.txt", b"inside", trust="trusted")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "nested.txt").write_text("outside nested")
    input_dir = tmp_path / "ws" / "parent" / "sub" / "child-1" / "input"
    (input_dir / "file-link").symlink_to(outside_file)
    (input_dir / "dir-link").symlink_to(outside_dir)

    index = child.index_workspace("child-1")

    assert [item["path"] for item in index] == [regular["path"]]
    assert all(not Path(item["path"]).is_symlink() for item in index)


def test_child_workspace_requires_existing_parent_run(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with pytest.raises(WorkspaceRunMissingError):
        manager.for_child("missing-parent", "child-1").create()
