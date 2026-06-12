"""Tests for memory expiration and supersession filtering."""

from __future__ import annotations

from pathlib import Path

from modi_harness.memory import MemoryPaths, MemoryStore


def _paths(tmp_path: Path) -> MemoryPaths:
    return MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        workspace=tmp_path / "workspace",
        thread=tmp_path / "thread",
    )


def _record(record_id: str, *, scope: str = "user", body: str = "body", **overrides) -> dict:
    base = {
        "id": record_id,
        "scope": scope,
        "type": "project" if scope == "workspace" else "feedback",
        "name": record_id,
        "description": "desc",
        "body": body,
        "tags": ["t"],
        "source_run_id": None,
        "expires_at": None,
        "metadata": {},
    }
    base.update(overrides)
    return base


def test_expired_records_omitted_from_index_search_and_context(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("old", expires_at="2000-01-01T00:00:00.000Z"))
    store.write_record(_record("fresh", body="fresh"))

    assert {r["id"] for r in store.load_index(["user"])["records"]} == {"fresh"}
    assert {r["id"] for r in store.search(scopes=["user"])} == {"fresh"}
    selected = store.select_for_context(task={}, agent_name="a", scopes=["user"], level="minimal")
    assert [r["id"] for r in selected] == ["fresh"]


def test_explicit_read_can_return_expired_record(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("old", expires_at="2000-01-01T00:00:00.000Z"))

    assert store.read_record("old")["id"] == "old"


def test_include_expired_opt_in_for_index_and_search(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("old", expires_at="2000-01-01T00:00:00.000Z"))

    idx = store.load_index(["user"], include_expired=True)
    found = store.search(scopes=["user"], include_expired=True)

    assert [r["id"] for r in idx["records"]] == ["old"]
    assert [r["id"] for r in found] == ["old"]


def test_superseded_records_omitted_by_default(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("old", metadata={"superseded_by": "new"}))
    store.write_record(_record("new", metadata={"supersedes": "old"}))

    assert {r["id"] for r in store.load_index(["user"])["records"]} == {"new"}
    assert {r["id"] for r in store.search(scopes=["user"])} == {"new"}
    assert store.read_record("old")["metadata"]["superseded_by"] == "new"


def test_include_superseded_opt_in_for_index_and_search(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("old", metadata={"superseded_by": "new"}))
    store.write_record(_record("new", metadata={"supersedes": "old"}))

    idx = store.load_index(["user"], include_superseded=True)
    found = store.search(scopes=["user"], include_superseded=True)

    assert {r["id"] for r in idx["records"]} == {"old", "new"}
    assert {r["id"] for r in found} == {"old", "new"}


def test_workspace_horizon_filters_old_workspace_memory(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path), workspace_horizon_days=90)
    store.write_record(
        _record(
            "ancient",
            scope="workspace",
            body="ancient",
            updated_at="2000-01-01T00:00:00.000Z",
        )
    )
    store.write_record(_record("current", scope="workspace", body="current"))

    selected = store.select_for_context(
        task={"tags": ["t"]},
        agent_name="a",
        scopes=["workspace"],
        level="moderate",
    )

    assert [r["id"] for r in selected] == ["current"]
