"""Tests for explainable local memory retrieval."""

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


def _record(record_id: str, *, body: str, tags: list[str] | None = None, **overrides) -> dict:
    base = {
        "id": record_id,
        "scope": "user",
        "type": "feedback",
        "name": record_id,
        "description": "desc",
        "body": body,
        "tags": tags or [],
    }
    base.update(overrides)
    return base


def test_search_candidates_returns_scores_reasons_and_signals(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("name_hit", body="nothing", tags=["style"], name="concise"))
    store.write_record(_record("body_hit", body="please be concise"))
    store.write_record(_record("miss", body="verbose only"))

    candidates = store.search_candidates(query="concise", scopes=["user"], tags=["style"])

    assert [c["record"]["id"] for c in candidates] == ["name_hit"]
    c = candidates[0]
    assert c["score"] > 0
    assert "tag:style" in c["reasons"]
    assert "query:name" in c["reasons"]
    assert c["signals"]["tag"] > 0
    assert c["signals"]["query"] > 0


def test_search_candidates_order_is_deterministic_by_score_then_recency(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(
        _record("old", body="alpha", updated_at="2020-01-01T00:00:00.000Z")
    )
    store.write_record(
        _record("new", body="alpha", updated_at="2025-01-01T00:00:00.000Z")
    )

    candidates = store.search_candidates(query="alpha", scopes=["user"])

    assert [c["record"]["id"] for c in candidates] == ["new", "old"]


def test_search_compatibility_returns_records_only(tmp_path: Path) -> None:
    store = MemoryStore(_paths(tmp_path))
    store.write_record(_record("a", body="alpha"))

    assert store.search(query="alpha", scopes=["user"])[0]["id"] == "a"
