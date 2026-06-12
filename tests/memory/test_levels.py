"""Tests for MemoryLevel-based selection in MemoryStore.select_for_context."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.memory import MemoryPaths, MemoryStore


def _paths(tmp_path: Path) -> MemoryPaths:
    return MemoryPaths(
        user=tmp_path / "user",
        agent=tmp_path / "agent",
        workspace=tmp_path / "workspace",
        thread=tmp_path / "thread",
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
    return base


def _seed_all_types(store: MemoryStore) -> None:
    """Seed one record of each type: feedback, user, project, reference."""
    store.write_record(_new_record(id="fb1", record_type="feedback", body="feedback note"))
    store.write_record(_new_record(id="u1", record_type="user", body="user pref"))
    store.write_record(
        _new_record(id="p1", scope="workspace", record_type="project", body="project info", tags=["t"])
    )
    store.write_record(
        _new_record(id="r1", record_type="reference", name="ref-key", body="reference data")
    )


class TestMinimalLevel:
    """Level 'minimal' includes only feedback type with 500 token budget."""

    def test_only_feedback_included(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        _seed_all_types(store)
        selected = store.select_for_context(
            task={"tags": ["t"], "reference_keys": ["ref-key"]},
            agent_name="x",
            scopes=["user", "workspace"],
            level="minimal",
        )
        types = {r["type"] for r in selected}
        assert types == {"feedback"}

    def test_budget_defaults_to_500(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        # Write feedback records that total > 500 tokens (each ~125 tokens = 500 bytes)
        for i in range(10):
            store.write_record(
                _new_record(id=f"fb{i}", record_type="feedback", body="x" * 500)
            )
        selected = store.select_for_context(
            task={},
            agent_name="x",
            scopes=["user"],
            level="minimal",
        )
        # 500 bytes / 4 = 125 tokens per record; budget 500 tokens => max 4 records
        assert len(selected) <= 4

    def test_explicit_budget_overrides_level_default(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        for i in range(10):
            store.write_record(
                _new_record(id=f"fb{i}", record_type="feedback", body="x" * 500)
            )
        selected = store.select_for_context(
            task={},
            agent_name="x",
            scopes=["user"],
            level="minimal",
            budget=250,
        )
        # 125 tokens per record; budget 250 => max 2 records
        assert len(selected) <= 2


class TestModerateLevel:
    """Level 'moderate' includes feedback + user + workspace-level project records."""

    def test_includes_feedback_user_project(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        _seed_all_types(store)
        selected = store.select_for_context(
            task={"tags": ["t"], "reference_keys": ["ref-key"]},
            agent_name="x",
            scopes=["user", "workspace"],
            level="moderate",
        )
        types = {r["type"] for r in selected}
        assert "feedback" in types
        assert "user" in types
        assert "project" in types
        assert "reference" not in types

    def test_budget_defaults_to_1500(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        # Each record ~125 tokens; budget 1500 => max 12 records
        for i in range(20):
            store.write_record(
                _new_record(id=f"fb{i}", record_type="feedback", body="x" * 500)
            )
        selected = store.select_for_context(
            task={},
            agent_name="x",
            scopes=["user"],
            level="moderate",
        )
        assert len(selected) <= 12


class TestFullLevel:
    """Level 'full' includes all types with 3000 token budget."""

    def test_includes_all_types(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        _seed_all_types(store)
        selected = store.select_for_context(
            task={"tags": ["t"], "reference_keys": ["ref-key"]},
            agent_name="x",
            scopes=["user", "workspace"],
            level="full",
        )
        types = {r["type"] for r in selected}
        assert types == {"feedback", "user", "project", "reference"}

    def test_budget_defaults_to_3000(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        # Each record ~125 tokens; budget 3000 => max 24 records
        for i in range(30):
            store.write_record(
                _new_record(id=f"fb{i}", record_type="feedback", body="x" * 500)
            )
        selected = store.select_for_context(
            task={},
            agent_name="x",
            scopes=["user"],
            level="full",
        )
        assert len(selected) <= 24

    def test_priority_ordering_preserved(self, tmp_path: Path) -> None:
        store = MemoryStore(_paths(tmp_path))
        _seed_all_types(store)
        selected = store.select_for_context(
            task={"tags": ["t"], "reference_keys": ["ref-key"]},
            agent_name="x",
            scopes=["user", "workspace"],
            level="full",
        )
        types_in_order = [r["type"] for r in selected]
        # feedback first, then user, then project, then reference
        fb_idx = types_in_order.index("feedback")
        u_idx = types_in_order.index("user")
        p_idx = types_in_order.index("project")
        r_idx = types_in_order.index("reference")
        assert fb_idx < u_idx < p_idx < r_idx
