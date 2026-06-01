"""Tests for tools/builtin.py — workspace and memory builtin tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.tools.builtin import BUILTIN_TOOL_NAMES, get_builtin_specs


def test_builtin_tool_names_complete():
    assert BUILTIN_TOOL_NAMES == frozenset({
        "read_workspace_file",
        "list_workspace_dir",
        "save_artifact",
        "save_draft",
        "recall_memory",
        "save_memory",
    })


def test_get_builtin_specs_returns_six_entries():
    entries = get_builtin_specs()
    assert len(entries) == 6
    names = {spec["name"] for spec, _handler in entries}
    assert names == BUILTIN_TOOL_NAMES


def test_every_builtin_spec_has_kind_builtin():
    for spec, _ in get_builtin_specs():
        assert spec["kind"] == "builtin", f"{spec['name']} has kind={spec['kind']!r}"


def test_every_builtin_handler_is_callable():
    for _spec, handler in get_builtin_specs():
        assert callable(handler)


def test_risk_levels_match_spec_doc():
    expected = {
        "read_workspace_file": "L0",
        "list_workspace_dir": "L0",
        "save_artifact": "L1",
        "save_draft": "L1",
        "recall_memory": "L0",
        "save_memory": "L1",
    }
    actual = {spec["name"]: spec["risk_level"] for spec, _ in get_builtin_specs()}
    assert actual == expected


# ---------------------------------------------------------------------------
# _read_workspace_file
# ---------------------------------------------------------------------------

from dataclasses import dataclass

from modi_harness.tools.builtin import _read_workspace_file
from modi_harness.workspace import WorkspaceManager


@dataclass
class _FakeDeps:
    workspace: WorkspaceManager


def _state(run_id: str) -> dict:
    return {"run_id": run_id, "thread_id": "t-1"}


def test_read_workspace_file_text(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    wm.save_draft("run-1", "note.md", "hello")
    out = _read_workspace_file(
        arguments={"kind": "draft", "name": "note.md"},
        state=_state("run-1"),
        deps=_FakeDeps(workspace=wm),
    )
    assert out["content"] == "hello"
    assert out["kind"] == "draft"
    assert out["name"] == "note.md"


def test_read_workspace_file_missing_returns_error(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    out = _read_workspace_file(
        arguments={"kind": "draft", "name": "nope.md"},
        state=_state("run-1"),
        deps=_FakeDeps(workspace=wm),
    )
    assert "error" in out


def test_read_workspace_file_rejects_traversal(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    with pytest.raises(Exception):
        _read_workspace_file(
            arguments={"kind": "draft", "name": "../../../etc/passwd"},
            state=_state("run-1"),
            deps=_FakeDeps(workspace=wm),
        )


# ---------------------------------------------------------------------------
# _list_workspace_dir
# ---------------------------------------------------------------------------

from modi_harness.tools.builtin import _list_workspace_dir


def test_list_workspace_dir_lists_files(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    wm.save_draft("run-1", "a.md", "x")
    wm.save_draft("run-1", "b.md", "y")
    out = _list_workspace_dir(
        arguments={"kind": "draft"},
        state=_state("run-1"),
        deps=_FakeDeps(workspace=wm),
    )
    names = sorted(f["name"] for f in out["files"])
    assert names == ["a.md", "b.md"]


def test_list_workspace_dir_empty_returns_empty_list(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    out = _list_workspace_dir(
        arguments={"kind": "artifact"},
        state=_state("run-1"),
        deps=_FakeDeps(workspace=wm),
    )
    assert out["files"] == []


# ---------------------------------------------------------------------------
# _save_artifact
# ---------------------------------------------------------------------------

from modi_harness.tools.builtin import _save_artifact


def test_save_artifact_writes_file_and_returns_id(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    out = _save_artifact(
        arguments={"name": "report.md", "content": "# report\nhi", "mime_type": "text/markdown"},
        state=_state("run-1"),
        deps=_FakeDeps(workspace=wm),
    )
    assert out["artifact_id"]
    assert out["name"] == "report.md"
    assert out["size_bytes"] == len("# report\nhi".encode("utf-8"))

    # File is on disk.
    artifact_path = tmp_path / "ws" / "run-1" / "artifacts" / "report.md"
    assert artifact_path.exists()
    assert artifact_path.read_text() == "# report\nhi"


def test_save_artifact_rejects_traversal(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    with pytest.raises(Exception):
        _save_artifact(
            arguments={"name": "../escape.md", "content": "x"},
            state=_state("run-1"),
            deps=_FakeDeps(workspace=wm),
        )


# ---------------------------------------------------------------------------
# _save_draft
# ---------------------------------------------------------------------------

from modi_harness.tools.builtin import _save_draft


def test_save_draft_writes_file(tmp_path: Path) -> None:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("run-1")
    out = _save_draft(
        arguments={"name": "outline.md", "content": "# outline"},
        state=_state("run-1"),
        deps=_FakeDeps(workspace=wm),
    )
    assert out["name"] == "outline.md"
    p = tmp_path / "ws" / "run-1" / "drafts" / "outline.md"
    assert p.exists()
    assert p.read_text() == "# outline"


# ---------------------------------------------------------------------------
# _recall_memory
# ---------------------------------------------------------------------------

from modi_harness.tools.builtin import _recall_memory
from modi_harness.memory import MemoryPaths, MemoryStore


@dataclass
class _MemDeps:
    memory: MemoryStore


def test_recall_memory_returns_matching_records(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        project=tmp_path / "project",
        conversation=tmp_path / "conv",
    )
    store = MemoryStore(paths)
    store.write_record({
        "id": "rec-1",
        "scope": "agent",
        "type": "feedback",
        "name": "n",
        "description": "d",
        "body": "user prefers concise responses",
        "tags": ["style"],
    })
    out = _recall_memory(
        arguments={"scopes": ["agent"], "tags": ["style"]},
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )
    assert len(out["records"]) == 1
    assert out["records"][0]["id"] == "rec-1"


def test_recall_memory_clamps_limit(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        project=tmp_path / "project",
        conversation=tmp_path / "conv",
    )
    store = MemoryStore(paths)
    out = _recall_memory(
        arguments={"limit": 999},  # spec rejects via schema; handler also clamps defensively
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )
    # Empty store, just verify it didn't crash and returned a list.
    assert out["records"] == []
