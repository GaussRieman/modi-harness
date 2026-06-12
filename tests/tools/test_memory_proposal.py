"""Tests for proposal-based model-facing memory writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from modi_harness.graph.deps import GraphDeps
from modi_harness.memory import MemoryPaths, MemoryScopeKeys, MemoryStore
from modi_harness.policy import PolicyGate
from modi_harness.tools.builtin import get_builtin_specs


def _handler(name: str):
    for spec, handler in get_builtin_specs():
        if spec["name"] == name:
            return spec, handler
    raise AssertionError(f"missing builtin {name}")


class _Deps:
    def __init__(self, tmp_path: Path) -> None:
        self.memory = MemoryStore(
            MemoryPaths(
                user=tmp_path / "user",
                agent=tmp_path / "agent",
                project=tmp_path / "project",
                conversation=tmp_path / "conversation",
            )
        )
        self.policy = PolicyGate()
        self.memory_scope_keys = MemoryScopeKeys(
            user_key="u",
            agent_name="agent",
            project_key="p",
            thread_id="t",
        )


def _state() -> dict[str, Any]:
    return {
        "run_id": "run1",
        "thread_id": "thread1",
        "agent_name": "agent",
        "permission_mode": "auto",
        "denied_actions": [],
    }


def test_propose_memory_allows_agent_scope_and_writes(tmp_path: Path) -> None:
    _spec, handler = _handler("propose_memory")
    deps = _Deps(tmp_path)

    result = handler(
        arguments={
            "id": "m1",
            "scope": "agent",
            "type": "feedback",
            "body": "remember this",
        },
        state=_state(),
        deps=deps,
    )

    assert result["status"] == "committed"
    assert deps.memory.read_record("m1", scope_keys=deps.memory_scope_keys)["body"] == "remember this"


def test_propose_memory_requires_approval_for_user_scope(tmp_path: Path) -> None:
    _spec, handler = _handler("propose_memory")
    deps = _Deps(tmp_path)

    result = handler(
        arguments={
            "id": "m1",
            "scope": "user",
            "type": "feedback",
            "body": "durable",
        },
        state=_state(),
        deps=deps,
    )

    assert result["status"] == "approval_required"
    assert result["approval_id"]


def test_propose_memory_requires_approval_for_workspace_scope(tmp_path: Path) -> None:
    _spec, handler = _handler("propose_memory")
    deps = _Deps(tmp_path)

    result = handler(
        arguments={
            "id": "m1",
            "scope": "workspace",
            "type": "feedback",
            "body": "durable workspace fact",
        },
        state=_state(),
        deps=deps,
    )

    assert result["status"] == "approval_required"
    assert result["approval_id"]


def test_propose_memory_denies_untrusted_tool_result_source(tmp_path: Path) -> None:
    _spec, handler = _handler("propose_memory")
    deps = _Deps(tmp_path)

    result = handler(
        arguments={
            "id": "m1",
            "scope": "agent",
            "type": "feedback",
            "body": "from tool",
            "source_kind": "tool_result",
        },
        state=_state(),
        deps=deps,
    )

    assert result["status"] == "denied"
    assert "untrusted" in result["reason"]
