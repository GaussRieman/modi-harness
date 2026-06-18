"""Tests for memory admission and authority classification."""

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


def _record(record_id: str, record_type: str, *, body: str = "body", **overrides) -> dict:
    base = {
        "id": record_id,
        "scope": "user",
        "type": record_type,
        "name": record_id,
        "description": "desc",
        "body": body,
        "tags": ["t"],
        "metadata": {},
    }
    base.update(overrides)
    return base


def test_select_candidates_classifies_authority(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("fb", "feedback", body="trusted feedback"))
    store.write_record(_record("ref", "reference", body="context ref"))

    selected = store.select_candidates_for_context(
        task={"reference_keys": ["ref"]},
        agent_name="a",
        scopes=["user"],
        level="full",
    )

    by_id = {s["record"]["id"]: s for s in selected}
    assert by_id["fb"]["authority"] == "trusted"
    assert by_id["ref"]["authority"] == "context"
    assert by_id["fb"]["reasons"]


def test_low_confidence_candidates_are_withheld(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(
        _record("weak", "reference", body="weak", metadata={"confidence": 0.1})
    )

    selected = store.select_candidates_for_context(
        task={"reference_keys": ["weak"]},
        agent_name="a",
        scopes=["user"],
        level="full",
    )

    assert selected == []
