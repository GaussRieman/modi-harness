"""Tests for workspace inline references in ContextManager."""

from __future__ import annotations

from typing import Any

from modi_harness.context import ContextManager
from modi_harness.policy import PolicyGate
from modi_harness.types import ContextBlock, TrustAnnotation


# ---------- factories ----------


def _agent(default_tools: list[str], **overrides: Any) -> dict:
    base = {
        "name": "x",
        "description": "y",
        "instruction": "you are x",
        "default_tools": default_tools,
        "default_skills": [],
        "output_contract": None,
        "permission_profile": None,
        "safety_constraints": ["do not lie"],
        "tags": [],
        "metadata": {},
    }
    base.update(overrides)
    return base


def _state() -> dict:
    return {
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": "ask",
        "task": {},
        "messages": [],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": [],
        "workspace_refs": [],
        "pending_approval": None,
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
    }


def _mem_index() -> dict:
    return {"records": [], "by_scope": {}, "by_type": {}, "by_tag": {}}


def _spec(name: str) -> dict:
    return {
        "name": name,
        "description": f"{name} tool",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": "L1",
        "side_effect": False,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": False,
        "dry_run_supported": False,
        "tags": [],
    }


def _tool_catalog(*specs: dict) -> dict[str, dict]:
    return {s["name"]: s for s in specs}


def _make_context_block(block_id: str, content: str, path: str) -> ContextBlock:
    return ContextBlock(
        block_id=block_id,
        source_kind="workspace_file",
        content=content,
        workspace_ref=path,
        trust=TrustAnnotation(
            trust_level="untrusted",
            source_kind="workspace_file",
            source_id=path,
            sanitizer=None,
        ),
    )


# ---------- tests ----------


def test_no_references_by_default() -> None:
    """build_context() without inlined_references yields empty references."""
    cm = ContextManager(policy=PolicyGate())
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    assert pack["references"] == []


def test_inlined_references_passed_through() -> None:
    """Caller-provided ContextBlocks appear in references and trust_annotations."""
    cm = ContextManager(policy=PolicyGate())

    ref1 = _make_context_block("art-1", "hello world", "/ws/file1.txt")
    ref2 = _make_context_block("art-2", "data here", "/ws/file2.txt")

    pack = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
        inlined_references=[ref1, ref2],
    )

    # References are present
    assert len(pack["references"]) == 2
    assert pack["references"][0]["block_id"] == "art-1"
    assert pack["references"][1]["block_id"] == "art-2"
    assert pack["references"][0]["content"] == "hello world"

    # Trust annotations include the reference annotations
    trust_source_ids = [a["source_id"] for a in pack["trust_annotations"]]
    assert "/ws/file1.txt" in trust_source_ids
    assert "/ws/file2.txt" in trust_source_ids
