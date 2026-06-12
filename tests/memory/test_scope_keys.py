"""Tests for keyed memory scope partitioning."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.memory import MemoryPaths, MemoryScopeKeys, MemoryStore
from modi_harness.memory.errors import MemoryNotFoundError


def _paths(tmp_path: Path) -> MemoryPaths:
    return MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        project=tmp_path / "project",
        conversation=tmp_path / "conversation",
    )


def _keys(
    *,
    user_key: str = "default",
    agent_name: str = "agent_a",
    project_key: str = "project_a",
    thread_id: str = "thread_a",
) -> MemoryScopeKeys:
    return MemoryScopeKeys(
        user_key=user_key,
        agent_name=agent_name,
        project_key=project_key,
        thread_id=thread_id,
    )


def _record(scope: str, *, record_id: str = "m1", body: str = "hello") -> dict:
    return {
        "id": record_id,
        "scope": scope,
        "type": "reference",
        "name": "n",
        "description": "d",
        "body": body,
        "tags": ["t"],
    }


def test_keyed_write_uses_scope_partition(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    keys = _keys(agent_name="writer")

    store.write_record(_record("agent"), scope_keys=keys)

    assert (tmp_path / "agent" / "writer" / "m1.md").exists()
    assert not (tmp_path / "agent" / "m1.md").exists()
    idx = store.load_index(["agent"], scope_keys=keys)
    assert [r["id"] for r in idx["records"]] == ["m1"]


def test_keyed_load_reads_legacy_flat_scope_as_fallback(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("agent", body="legacy"))

    idx = store.load_index(["agent"], scope_keys=_keys(agent_name="new_agent"))

    assert [r["body"] for r in idx["records"]] == ["legacy"]


def test_agent_scope_isolated_by_agent_name(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("agent", body="a"), scope_keys=_keys(agent_name="a"))
    store.write_record(_record("agent", body="b"), scope_keys=_keys(agent_name="b"))

    assert store.read_record("m1", scope_keys=_keys(agent_name="a"))["body"] == "a"
    assert store.read_record("m1", scope_keys=_keys(agent_name="b"))["body"] == "b"
    with pytest.raises(MemoryNotFoundError):
        store.read_record("m1", scope_keys=_keys(agent_name="c"))


def test_conversation_scope_isolated_by_thread_id(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("conversation", body="one"), scope_keys=_keys(thread_id="t1"))
    store.write_record(_record("conversation", body="two"), scope_keys=_keys(thread_id="t2"))

    assert store.search(scopes=["conversation"], scope_keys=_keys(thread_id="t1"))[0]["body"] == "one"
    assert store.search(scopes=["conversation"], scope_keys=_keys(thread_id="t2"))[0]["body"] == "two"


def test_project_scope_isolated_by_project_key(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("project", body="alpha"), scope_keys=_keys(project_key="alpha"))
    store.write_record(_record("project", body="beta"), scope_keys=_keys(project_key="beta"))

    assert store.read_record("m1", scope_keys=_keys(project_key="alpha"))["body"] == "alpha"
    assert store.read_record("m1", scope_keys=_keys(project_key="beta"))["body"] == "beta"


def test_workspace_scope_alias_uses_project_partition(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))

    store.write_record(_record("workspace", body="workspace rule"), scope_keys=_keys(project_key="w1"))

    assert (tmp_path / "project" / "w1" / "m1.md").exists()
    via_workspace = store.search(scopes=["workspace"], scope_keys=_keys(project_key="w1"))
    via_project = store.search(scopes=["project"], scope_keys=_keys(project_key="w1"))
    assert via_workspace[0]["scope"] == "workspace"
    assert via_workspace[0]["body"] == "workspace rule"
    assert via_project[0]["scope"] == "workspace"


def test_thread_scope_alias_uses_conversation_partition(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))

    store.write_record(_record("thread", body="thread note"), scope_keys=_keys(thread_id="t1"))

    assert (tmp_path / "conversation" / "t1" / "m1.md").exists()
    via_thread = store.search(scopes=["thread"], scope_keys=_keys(thread_id="t1"))
    via_conversation = store.search(scopes=["conversation"], scope_keys=_keys(thread_id="t1"))
    assert via_thread[0]["scope"] == "thread"
    assert via_thread[0]["body"] == "thread note"
    assert via_conversation[0]["scope"] == "thread"
