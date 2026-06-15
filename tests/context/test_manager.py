"""Tests for ContextManager."""

from __future__ import annotations

from typing import Any

from modi_harness.context import ContextManager
from modi_harness.policy import PolicyGate


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


def _skill(name: str = "s", *, allowed_tools=None, instruction: str = "use carefully") -> dict:
    return {
        "name": name,
        "description": "d",
        "instruction": instruction,
        "allowed_tools": allowed_tools,
        "risk_notes": [],
        "references": [],
        "scripts": [],
        "templates": [],
        "examples": [],
        "tags": [],
        "metadata": {},
    }


def _state(messages: list[dict] | None = None) -> dict:
    return {
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": "ask",
        "task": {},
        "messages": messages or [],
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


def _mem_index(records: list[dict] | None = None) -> dict:
    records = records or []
    by_scope: dict[str, list[str]] = {}
    by_type: dict[str, list[str]] = {}
    by_tag: dict[str, list[str]] = {}
    for r in records:
        by_scope.setdefault(r["scope"], []).append(r["id"])
        by_type.setdefault(r["type"], []).append(r["id"])
        for t in r["tags"]:
            by_tag.setdefault(t, []).append(r["id"])
    return {"records": records, "by_scope": by_scope, "by_type": by_type, "by_tag": by_tag}


def _tool_catalog(*specs: dict) -> dict[str, dict]:
    return {s["name"]: s for s in specs}


def _spec(name: str, risk_level: str = "L1") -> dict:
    return {
        "name": name,
        "description": f"{name} tool",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": risk_level,
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


# ---------- determinism ----------


def test_context_hash_deterministic() -> None:
    cm = ContextManager(policy=PolicyGate())
    args = dict(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    a = cm.build_context(**args)
    b = cm.build_context(**args)
    assert a["context_hash"] == b["context_hash"]


def test_context_hash_changes_with_instruction() -> None:
    cm = ContextManager(policy=PolicyGate())
    a = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    b = cm.build_context(
        state=_state(),
        agent=_agent(["t1"], instruction="different"),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    assert a["context_hash"] != b["context_hash"]


# ---------- tool visibility ----------


def test_skill_with_no_allowed_tools_does_not_narrow() -> None:
    cm = ContextManager(policy=PolicyGate())
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["a", "b"]),
        skills=[_skill(allowed_tools=None)],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("a"), _spec("b")),
        output_contract=None,
    )
    visible = {t["name"] for t in pack["tool_descriptions"]}
    assert visible == {"a", "b"}


def test_skill_with_empty_allowed_tools_narrows_to_nothing() -> None:
    cm = ContextManager(policy=PolicyGate())
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["a", "b"]),
        skills=[_skill(allowed_tools=[])],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("a"), _spec("b")),
        output_contract=None,
    )
    assert pack["tool_descriptions"] == []


def test_skill_with_listed_allowed_tools_narrows() -> None:
    cm = ContextManager(policy=PolicyGate())
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["a", "b", "c"]),
        skills=[_skill(allowed_tools=["a"])],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("a"), _spec("b"), _spec("c")),
        output_contract=None,
    )
    visible = {t["name"] for t in pack["tool_descriptions"]}
    assert visible == {"a"}


def test_multi_skill_union_narrows() -> None:
    cm = ContextManager(policy=PolicyGate())
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["a", "b", "c"]),
        skills=[_skill("s1", allowed_tools=["a"]), _skill("s2", allowed_tools=["b"])],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("a"), _spec("b"), _spec("c")),
        output_contract=None,
    )
    visible = {t["name"] for t in pack["tool_descriptions"]}
    assert visible == {"a", "b"}


def test_policy_deny_list_filters() -> None:
    cm = ContextManager(policy=PolicyGate())
    pack = cm.build_context(
        state=_state(),
        agent=_agent(
            ["a", "b"],
            permission_profile={"mode": "ask", "preauthorized": [], "deny": ["b"], "review_required": []},
        ),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("a"), _spec("b")),
        output_contract=None,
    )
    visible = {t["name"] for t in pack["tool_descriptions"]}
    assert visible == {"a"}


# ---------- assembly ----------


def test_system_includes_untrusted_note() -> None:
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
    assert "untrusted" in pack["system_instruction"].lower()


def test_skill_instructions_in_order() -> None:
    cm = ContextManager(policy=PolicyGate())
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[_skill("s1", instruction="first"), _skill("s2", instruction="second")],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    assert pack["skill_instructions"] == ["first", "second"]


def test_memory_blocks_rendered_separately_from_references() -> None:
    cm = ContextManager(policy=PolicyGate())
    record = {
        "id": "m1",
        "scope": "user",
        "type": "feedback",
        "name": "n",
        "description": "d",
        "body": "be terse",
        "tags": [],
        "source_run_id": None,
        "created_at": "",
        "updated_at": "",
        "expires_at": None,
        "metadata": {},
    }
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index([record]),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    assert any(b["body"] == "be terse" for b in pack["memory_blocks"])
    assert pack["references"] == []


def test_memory_blocks_become_run_context_reference_after_first_step() -> None:
    cm = ContextManager(policy=PolicyGate())
    record = {
        "id": "m1",
        "scope": "user",
        "type": "feedback",
        "name": "n",
        "description": "d",
        "body": "never repeat this full memory body",
        "tags": [],
        "source_run_id": None,
        "created_at": "",
        "updated_at": "",
        "expires_at": None,
        "metadata": {},
    }
    state = _state()
    state["step_count"] = 1
    pack = cm.build_context(
        state=state,
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index([record]),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    assert pack["memory_blocks"] == []
    assert "memory_ref=run_context.memory" in pack["state_summary"]
    assert "memory_records=1" in pack["state_summary"]
    assert "memory_injected=ref" in pack["state_summary"]
    assert "never repeat this full memory body" not in pack["state_summary"]


def test_message_windowing_count_limit() -> None:
    cm = ContextManager(policy=PolicyGate(), max_recent_messages=3)
    msgs = [
        {"role": "user", "content": f"m{i}", "tool_call_id": None, "metadata": {}}
        for i in range(10)
    ]
    pack = cm.build_context(
        state=_state(messages=msgs),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    assert len(pack["recent_messages"]) == 3
    assert [m["content"] for m in pack["recent_messages"]] == ["m7", "m8", "m9"]


def test_message_windowing_extends_back_over_leading_orphan_tool_result() -> None:
    # A tail slice can start on a tool_result whose matching assistant tool_use
    # sits just before the cut. Anthropic rejects that orphan with a 400. The
    # window must extend backwards to include the owning assistant message —
    # losslessly, never dropping the tool result.
    cm = ContextManager(policy=PolicyGate(), max_recent_messages=3)
    msgs = [
        {"role": "user", "content": "q", "tool_call_id": None, "metadata": {}},
        {"role": "assistant", "content": "calls t1", "tool_call_id": None, "metadata": {}},
        {"role": "tool", "content": "r1", "tool_call_id": "call_1", "metadata": {}},
        {"role": "assistant", "content": "answer", "tool_call_id": None, "metadata": {}},
    ]
    pack = cm.build_context(
        state=_state(messages=msgs),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    recent = pack["recent_messages"]
    # Naive tail (size 3) would be [tool r1, assistant, ...] — orphan at head.
    # Backward extension pulls in the assistant("calls t1") that owns r1, so the
    # window opens on an assistant and the tool result is preserved.
    assert recent[0]["role"] != "tool"
    assert [m["content"] for m in recent] == ["calls t1", "r1", "answer"]


def test_message_windowing_extends_back_over_consecutive_tool_results() -> None:
    # One assistant turn may be answered by several tool messages (parallel
    # calls / deferred results). Backward extension must skip over all of them
    # to reach the owning assistant — and keep every result.
    cm = ContextManager(policy=PolicyGate(), max_recent_messages=3)
    msgs = [
        {"role": "user", "content": "q", "tool_call_id": None, "metadata": {}},
        {"role": "assistant", "content": "calls t1,t2,t3", "tool_call_id": None, "metadata": {}},
        {"role": "tool", "content": "r1", "tool_call_id": "c1", "metadata": {}},
        {"role": "tool", "content": "r2", "tool_call_id": "c2", "metadata": {}},
        {"role": "tool", "content": "r3", "tool_call_id": "c3", "metadata": {}},
    ]
    pack = cm.build_context(
        state=_state(messages=msgs),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    recent = pack["recent_messages"]
    # Naive tail (size 3) = [tool r1, tool r2, tool r3] — all orphans. Backward
    # extension reaches the assistant turn that owns them; nothing is dropped.
    assert recent[0]["role"] != "tool"
    assert [m["content"] for m in recent] == ["calls t1,t2,t3", "r1", "r2", "r3"]


def test_workspace_files_appear_as_refs_not_inlined() -> None:
    cm = ContextManager(policy=PolicyGate())
    ws_index = [
        {
            "run_id": "r1",
            "kind": "artifact",
            "path": "/tmp/ws/r1/artifacts/big.bin",
            "artifact_id": None,
            "mime_type": None,
            "trust_level": "untrusted",
            "size_bytes": 999,
            "metadata": {},
        }
    ]
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=ws_index,
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=None,
    )
    assert pack["workspace_index"] == ws_index
    assert pack["references"] == []  # not auto-inlined


def test_output_requirement_passed_through() -> None:
    cm = ContextManager(policy=PolicyGate())
    contract = {
        "schema": None,
        "required_fields": ["x"],
        "citation_required": False,
        "risk_label_required": False,
        "forbidden_patterns": [],
        "review_required": False,
        "free_form": False,
    }
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=contract,
    )
    assert pack["output_requirement"] == contract


def test_free_form_contract_omits_output_requirement() -> None:
    cm = ContextManager(policy=PolicyGate())
    contract = {
        "schema": None,
        "required_fields": [],
        "citation_required": False,
        "risk_label_required": False,
        "forbidden_patterns": [],
        "review_required": False,
        "free_form": True,
    }
    pack = cm.build_context(
        state=_state(),
        agent=_agent(["t1"]),
        skills=[],
        memory_index=_mem_index(),
        workspace_index=[],
        tool_catalog=_tool_catalog(_spec("t1")),
        output_contract=contract,
    )
    assert pack["output_requirement"] is None
