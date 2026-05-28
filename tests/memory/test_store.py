"""Tests for MemoryStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.memory import MemoryPaths, MemoryStore
from modi_harness.memory.errors import (
    MemoryBodyTooLargeError,
    MemoryIdInvalidError,
    MemoryNotFoundError,
)


def _paths(tmp_path: Path) -> MemoryPaths:
    return MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        project=tmp_path / "project",
        conversation=tmp_path / "conv",
    )


def _new_record(scope: str = "user", record_type: str = "feedback", **overrides) -> dict:
    base = {
        "id": "rec_one",
        "scope": scope,
        "type": record_type,
        "name": "tone",
        "description": "user prefers terse",
        "body": "Be concise.",
        "tags": ["style"],
        "source_run_id": None,
        "expires_at": None,
        "metadata": {},
    }
    base.update(overrides)
    return base  # type: ignore[return-value]


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    written = store.write_record(_new_record())
    loaded = store.read_record(written["id"])
    assert loaded["body"] == "Be concise."
    assert loaded["scope"] == "user"
    assert loaded["created_at"]
    assert loaded["updated_at"]


def test_index_groups_by_scope_type_tag(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_new_record(id="a", tags=["x"]))
    store.write_record(_new_record(id="b", scope="agent", record_type="user", tags=["x", "y"]))
    idx = store.load_index({"user", "agent"})
    assert {r["id"] for r in idx["records"]} == {"a", "b"}
    assert "a" in idx["by_scope"]["user"]
    assert "b" in idx["by_scope"]["agent"]
    assert set(idx["by_tag"]["x"]) == {"a", "b"}
    assert "y" in idx["by_tag"]


def test_load_index_filters_to_active_scopes(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_new_record(id="a"))
    store.write_record(_new_record(id="b", scope="project"))
    idx = store.load_index({"user"})
    assert {r["id"] for r in idx["records"]} == {"a"}


def test_update_record_bumps_updated_at(tmp_path: Path) -> None:
    import time
    store = MemoryStore(_paths(tmp_path))
    written = store.write_record(_new_record())
    time.sleep(0.005)
    updated = store.update_record(written["id"], {"body": "shorter"})
    assert updated["body"] == "shorter"
    assert updated["updated_at"] >= written["updated_at"]


def test_delete_record_removes_file_and_index(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    written = store.write_record(_new_record())
    store.delete_record(written["id"])
    with pytest.raises(MemoryNotFoundError):
        store.read_record(written["id"])
    idx = store.load_index({"user"})
    assert idx["records"] == []


def test_search_filters(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_new_record(id="a", tags=["x"]))
    store.write_record(_new_record(id="b", record_type="user", tags=["y"]))
    res = store.search(scopes={"user"}, types={"feedback"}, tags={"x"})
    assert {r["id"] for r in res} == {"a"}


def test_select_for_context_orders_feedback_user_project(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_new_record(id="u1", record_type="user", body="user"))
    store.write_record(_new_record(id="f1", record_type="feedback", body="feedback"))
    store.write_record(
        _new_record(id="p1", scope="project", record_type="project", body="proj", tags=["t"])
    )
    selected = store.select_for_context(
        task={"tags": ["t"]},
        agent_name="x",
        scopes={"user", "project"},
        budget=1000,
    )
    types_in_order = [r["type"] for r in selected]
    assert types_in_order[0] == "feedback"
    assert "user" in types_in_order
    assert "project" in types_in_order


def test_select_respects_budget(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    big_body = "x" * 500
    store.write_record(_new_record(id="big1", body=big_body))
    store.write_record(_new_record(id="big2", body=big_body))
    selected = store.select_for_context(
        task={},
        agent_name="x",
        scopes={"user"},
        budget=200,  # tight; only one record fits
    )
    assert len(selected) <= 1


def test_oversize_body_rejected(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    huge = "x" * (5 * 1024)  # > 4 KiB limit
    with pytest.raises(MemoryBodyTooLargeError):
        store.write_record(_new_record(body=huge))


def test_invalid_id_rejected(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    with pytest.raises(MemoryIdInvalidError):
        store.write_record(_new_record(id="../escape"))
    with pytest.raises(MemoryIdInvalidError):
        store.write_record(_new_record(id="bad space"))


def test_forget_alias(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    written = store.write_record(_new_record())
    store.delete_record(written["id"])
    with pytest.raises(MemoryNotFoundError):
        store.read_record(written["id"])
