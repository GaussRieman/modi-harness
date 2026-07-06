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
        "propose_memory",
        "save_memory",
        "transition_stage",
    })


def test_get_builtin_specs_returns_expected_entries():
    entries = get_builtin_specs()
    assert len(entries) == 8
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
        "propose_memory": "L1",
        "save_memory": "L1",
        "transition_stage": "L0",
    }
    actual = {spec["name"]: spec["risk_level"] for spec, _ in get_builtin_specs()}
    assert actual == expected


def test_memory_builtin_scope_enums_are_canonical():
    specs = {spec["name"]: spec for spec, _handler in get_builtin_specs()}

    recall_scopes = set(
        specs["recall_memory"]["input_schema"]["properties"]["scopes"]["items"]["enum"]
    )
    save_scopes = set(specs["save_memory"]["input_schema"]["properties"]["scope"]["enum"])
    propose_scopes = set(specs["propose_memory"]["input_schema"]["properties"]["scope"]["enum"])

    assert recall_scopes == {"user", "workspace", "agent", "thread"}
    assert save_scopes == {"thread", "agent"}
    assert propose_scopes == {"user", "workspace", "agent", "thread"}


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
from modi_harness.memory import MemoryPaths, MemoryScopeKeys, MemoryStore


@dataclass
class _MemDeps:
    memory: MemoryStore


def test_recall_memory_returns_matching_records(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        workspace=tmp_path / "workspace",
        thread=tmp_path / "thread",
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


def test_recall_memory_accepts_workspace_scope(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        workspace=tmp_path / "workspace",
        thread=tmp_path / "thread",
    )
    store = MemoryStore(paths)
    store.write_record({
        "id": "rec-w",
        "scope": "workspace",
        "type": "reference",
        "name": "n",
        "description": "d",
        "body": "workspace rule",
        "tags": ["scope"],
    })

    out = _recall_memory(
        arguments={"scopes": ["workspace"], "tags": ["scope"]},
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )

    assert len(out["records"]) == 1
    assert out["records"][0]["scope"] == "workspace"


def test_recall_memory_clamps_limit(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        workspace=tmp_path / "workspace",
        thread=tmp_path / "thread",
    )
    store = MemoryStore(paths)
    out = _recall_memory(
        arguments={"limit": 999},  # spec rejects via schema; handler also clamps defensively
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )
    # Empty store, just verify it didn't crash and returned a list.
    assert out["records"] == []


# ---------------------------------------------------------------------------
# _save_memory
# ---------------------------------------------------------------------------

from modi_harness.tools.builtin import _save_memory


def test_save_memory_writes_record_in_agent_scope(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user", agent=tmp_path / "agent",
        workspace=tmp_path / "workspace", thread=tmp_path / "thread",
    )
    store = MemoryStore(paths)
    out = _save_memory(
        arguments={
            "id": "fact-1",
            "scope": "agent",
            "type": "fact",
            "body": "the user works in finance",
            "tags": ["context"],
        },
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )
    assert out["id"] == "fact-1"
    assert out["scope"] == "agent"
    # Read back.
    read = store.read_record("fact-1")
    assert read["body"] == "the user works in finance"


def test_save_memory_accepts_thread_scope(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user", agent=tmp_path / "agent",
        workspace=tmp_path / "workspace", thread=tmp_path / "thread",
    )
    store = MemoryStore(paths)

    out = _save_memory(
        arguments={
            "id": "thread-1",
            "scope": "thread",
            "type": "fact",
            "body": "this belongs to the task chain",
        },
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )

    assert out["id"] == "thread-1"
    assert out["scope"] == "thread"
    scope_keys = MemoryScopeKeys(thread_id="t-1")
    assert store.search(scopes=["thread"], scope_keys=scope_keys)[0]["scope"] == "thread"


def test_save_memory_rejects_user_scope(tmp_path: Path) -> None:
    paths = MemoryPaths(
        user=tmp_path / "user", agent=tmp_path / "agent",
        workspace=tmp_path / "workspace", thread=tmp_path / "thread",
    )
    store = MemoryStore(paths)
    out = _save_memory(
        arguments={
            "id": "f", "scope": "user", "type": "fact", "body": "x",
        },
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )
    assert "error" in out
    assert "scope" in out["error"].lower()


def test_save_memory_rejects_existing_id_in_any_scope(tmp_path: Path) -> None:
    """Model-driven save must not silently overwrite. Existing ID anywhere = error.

    Trust user (harness.add_memory keeps overwrite semantics), constrain model.
    """
    paths = MemoryPaths(
        user=tmp_path / "user", agent=tmp_path / "agent",
        workspace=tmp_path / "workspace", thread=tmp_path / "thread",
    )
    store = MemoryStore(paths)
    scope_keys = MemoryScopeKeys()
    # Pre-existing record in user scope (only writable via direct API).
    store.write_record({
        "id": "shared-id",
        "scope": "user",
        "type": "fact",
        "body": "operator-set fact",
    }, scope_keys=scope_keys)

    # Model-driven attempt to "write" the same id, even into a different scope.
    out = _save_memory(
        arguments={"id": "shared-id", "scope": "agent", "type": "fact", "body": "model fact"},
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )
    assert "error" in out
    assert "already exists" in out["error"].lower()

    # Original record is intact.
    existing = store.read_record("shared-id", scope_keys=scope_keys)
    assert existing["body"] == "operator-set fact"
    assert existing["scope"] == "user"


def test_memory_tool_descriptions_carry_usage_guidance():
    specs = {spec["name"]: spec for spec, _handler in get_builtin_specs()}

    recall = specs["recall_memory"]["description"]
    assert "before" in recall.lower()  # recall-before-acting guidance

    propose = specs["propose_memory"]["description"]
    assert "judgment" in propose.lower()  # durable scopes may need judgment
    assert "not" in propose.lower()  # memory is not an output/log store

    save = specs["save_memory"]["description"]
    assert "thread" in save and "agent" in save  # writable scopes
    assert "not" in save.lower()  # memory is not raw content / reports / drafts


def test_save_memory_rejects_existing_id_in_writable_scope(tmp_path: Path) -> None:
    """Same constraint when the prior record was itself written via the builtin."""
    paths = MemoryPaths(
        user=tmp_path / "user", agent=tmp_path / "agent",
        workspace=tmp_path / "workspace", thread=tmp_path / "thread",
    )
    store = MemoryStore(paths)
    # First write succeeds.
    first = _save_memory(
        arguments={"id": "fact-x", "scope": "agent", "type": "fact", "body": "v1"},
        state=_state("run-1"),
        deps=_MemDeps(memory=store),
    )
    assert first.get("id") == "fact-x"

    # Second write with the same id is rejected.
    second = _save_memory(
        arguments={"id": "fact-x", "scope": "agent", "type": "fact", "body": "v2"},
        state=_state("run-2"),
        deps=_MemDeps(memory=store),
    )
    assert "error" in second
    assert "already exists" in second["error"].lower()

    # Stored record is still v1.
    read = store.read_record("fact-x")
    assert read["body"] == "v1"


def test_workspace_tool_descriptions_distinguish_outputs_from_memory():
    specs = {spec["name"]: spec for spec, _handler in get_builtin_specs()}

    draft = specs["save_draft"]["description"]
    assert "output" in draft.lower() and "memory" in draft.lower()

    artifact = specs["save_artifact"]["description"]
    assert "output" in artifact.lower() and "memory" in artifact.lower()

    read = specs["read_workspace_file"]["description"]
    assert "input" in read.lower()  # mentions caller-provided input files


# ---------------------------------------------------------------------------
# transition_stage (N9 / N7 completion — the agent-facing stage entry point)
# ---------------------------------------------------------------------------

from modi_harness.tools.builtin import _transition_stage


def test_transition_stage_spec_is_readonly_and_enumerates_known_stages():
    specs = {spec["name"]: spec for spec, _handler in get_builtin_specs()}
    spec = specs["transition_stage"]
    # A stage transition is a read-only signal to the runtime; the alignment
    # kernel — not a side effect — decides whether it is allowed.
    assert spec["risk_level"] == "L0"
    assert spec["side_effect"] is False
    enum = set(spec["input_schema"]["properties"]["to"]["enum"])
    assert enum == {"clarify", "explore", "plan", "execute", "verify", "deliver"}
    assert spec["input_schema"]["required"] == ["to"]


def test_transition_stage_handler_resolves_the_target_stage():
    out = _transition_stage(
        arguments={"to": "deliver", "rationale": "evidence gathered"},
        state={"stage_id": "stg-old", "human_intent": {"current_stage": {"kind": "explore"}}},
        deps=None,
    )
    assert out["from_stage"] == "explore"
    assert out["to_stage"] == "deliver"
    # The handler returns a fully-built target stage descriptor the node can set.
    assert out["stage"]["kind"] == "deliver"
    assert out["stage"]["id"]


def test_transition_stage_handler_rejects_unknown_target():
    out = _transition_stage(
        arguments={"to": "ship-it"},
        state={"human_intent": {"current_stage": {"kind": "explore"}}},
        deps=None,
    )
    assert "error" in out
