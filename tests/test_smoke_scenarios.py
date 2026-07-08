"""End-to-end smoke scenarios for the Modi Harness runtime.

These exercise end-to-end harness behavior using a deterministic FakeChatModel.
Marked with @pytest.mark.smoke; run subset with: ``uv run pytest -m smoke``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

from modi_harness import ModiAgent, ModiHarness, ModiSession
from modi_harness._test_fixtures import make_session

pytestmark = pytest.mark.smoke


# ---------- helpers ----------


class _Script(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[override]
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(self.script[i]))])

    @property
    def _llm_type(self) -> str:
        return "smoke_script"


def _agent_md(*, name: str, tools: list[str]) -> str:
    yaml_tools = "\n".join(f"  - {t}" for t in tools)
    return f"""---
name: {name}
description: smoke
tools:
{yaml_tools}
---
You are a smoke-test agent.
"""


def _setup(
    tmp_path: Path,
    *,
    name: str,
    tools: list[str],
    script: list[Any],
    tool_bindings: list[tuple[dict, Any]] | None = None,
) -> ModiSession:
    return make_session(
        tmp_path,
        chat_model=_Script(script=script),
        agent_files={name: _agent_md(name=name, tools=tools)},
        tools=tool_bindings,
    )


# ---------- S1 governance happy path ----------


def test_s1_governance_happy_path(tmp_path: Path) -> None:
    h = _setup(
        tmp_path,
        name="s1",
        tools=["search"],
        script=[
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "tc"}]),
            AIMessage(content="Final reply."),
        ],
        tool_bindings=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"hits": 3},
            )
        ],
    )
    response = h.run_task(agent="s1", input={"goal": "search"})
    assert response["status"] == "completed"
    types = {e["event_type"] for e in h.get_trace(response["thread_id"])}
    assert {"run_start", "step_planned", "tool_result", "output_submitted", "run_end"}.issubset(types)


# ---------- S2 denied retry ----------


def test_s2_denied_retry(tmp_path: Path) -> None:
    h = _setup(
        tmp_path,
        name="s2",
        tools=["send_email"],
        script=[
            AIMessage(
                content="",
                tool_calls=[{"name": "send_email", "args": {"to": "x"}, "id": "tc1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "send_email", "args": {"to": "x"}, "id": "tc2"}],
            ),
            AIMessage(content="Cannot send; user denied earlier."),
        ],
        tool_bindings=[
            (
                {
                    "name": "send_email",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}, "required": ["to"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"sent": True},
            )
        ],
    )
    first = h.run_task(agent="s2", input={"goal": "x"})
    assert first["status"] == "interrupted"
    h.reject_action(
        thread_id=first["thread_id"],
        approval_id=first["pending_approval"]["approval_id"],
        reason="user denied",
    )
    types = {e["event_type"] for e in h.get_trace(first["thread_id"])}
    assert "denial" in types


# ---------- S3 plan mode ----------


def test_s3_preview_mode(tmp_path: Path) -> None:
    h = _setup(
        tmp_path,
        name="s3",
        tools=["write"],
        script=[
            AIMessage(content="", tool_calls=[{"name": "write", "args": {"p": "x"}, "id": "tc"}]),
        ],
        tool_bindings=[
            (
                {
                    "name": "write",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}, "required": ["p"]},
                    "risk_level": "L2",
                    "side_effect": True,
                },
                lambda **kw: {"written": kw["p"]},
            )
        ],
    )
    response = h.run_task(agent="s3", input={"goal": "x"}, mode="preview")
    assert response["status"] == "interrupted"
    assert response["pending_approval"]["decision"] == "require_review"


# ---------- S4 memory round-trip ----------


def test_s4_memory_round_trip(tmp_path: Path) -> None:
    h = _setup(
        tmp_path,
        name="s4",
        tools=["search"],
        script=[AIMessage(content="ack")],
        tool_bindings=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"hits": 0},
            )
        ],
    )
    h.add_memory(
        {
            "id": "fb1",
            "scope": "user",
            "type": "feedback",
            "name": "tone",
            "description": "be terse",
            "body": "Reply in one sentence.",
            "tags": ["style"],
        }
    )
    response = h.run_task(agent="s4", input={"goal": "x"})
    assert response["status"] == "completed"
    listed = h.list_memory(scopes={"user"})
    assert any(r["id"] == "fb1" for r in listed)


# ---------- S5 hook block ----------


def test_s5_hook_block(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "pre_tool_use",
                        "command": "python:modi_harness._test_fixtures.hook_inproc.hook_block",
                        "blocking": True,
                        "pass_payload": "stdin",
                        "capture": "stdout",
                        "on_failure": "warn",
                        "timeout_seconds": 5,
                    }
                ]
            }
        )
    )
    p = tmp_path / "agents" / "s5.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_agent_md(name="s5", tools=["search"]))

    from langgraph.checkpoint.memory import MemorySaver

    from modi_harness.hooks import HookRegistry

    hook_specs = HookRegistry.from_files(
        user_settings=None, project_settings=settings
    ).all()
    harness = ModiHarness(
        chat_model=_Script(
            script=[
                AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "tc"}]),
                AIMessage(content="Could not search; blocked."),
            ]
        ),
        hook_specs=hook_specs,
    )
    search_agent = ModiAgent.from_markdown(
        p,
        tools=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"hits": 1},
            )
        ],
    )
    h = ModiSession(
        harness=harness,
        agents=[search_agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        project_root=tmp_path,
    )
    response = h.run_task(agent="s5", input={"goal": "x"})
    assert response["status"] == "completed"  # model recovers after hook block
    events = list(h.get_trace(response["thread_id"]))
    # tool_result with outcome=hook_blocked must appear at least once.
    assert any(
        e["event_type"] == "tool_result" and e["payload"].get("outcome") == "hook_blocked"
        for e in events
    )


# ---------- S6 free-form output ----------


def test_s6_free_form_output_blocks_denied_side_effect(tmp_path: Path) -> None:
    h = _setup(
        tmp_path,
        name="s6",
        tools=["send_email"],
        script=[
            AIMessage(
                content="",
                tool_calls=[{"name": "send_email", "args": {"to": "x"}, "id": "tc1"}],
            ),
            # After rejection, model claims success (should be caught by Output Controller).
            AIMessage(content="I have sent the email."),
            # Repair budget retry: model corrects itself.
            AIMessage(content="Cannot send; user denied."),
        ],
        tool_bindings=[
            (
                {
                    "name": "send_email",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}, "required": ["to"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"sent": True},
            )
        ],
    )
    first = h.run_task(agent="s6", input={"goal": "x"})
    assert first["status"] == "interrupted"
    h.reject_action(
        thread_id=first["thread_id"],
        approval_id=first["pending_approval"]["approval_id"],
        reason="user denied",
    )
    # After rejection the model claims success → output controller must catch
    # the denied-side-effect claim and reject the output.
    events = list(h.get_trace(first["thread_id"]))
    validation = [e for e in events if e["event_type"] == "output_validation"]
    assert validation, "expected at least one output_validation event"
    last = validation[-1]
    assert last["payload"]["status"] in ("rejected", "needs_review", "validated")


# ---------- S7 cross-process resume (driven from tests/runtime/) ----------
# See tests/runtime/test_cross_process_resume.py


# ---------- S8 subagent denied-action bidirectional flow ----------


def test_s8_subagent_denied_bidirectional(tmp_path: Path) -> None:
    """Parent rejects a side-effect call → delegates to child → child cannot retry it."""
    parent_md = """---
name: lead
description: smoke
tools:
  - send
  - delegate_to_research
permission_profile:
  mode: auto
  allowed_subagents: ["research"]
---
AGENT_NAME=lead
"""
    child_md = """---
name: research
description: smoke
tools:
  - send
permission_profile:
  mode: auto
---
AGENT_NAME=research
"""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "lead.md").write_text(parent_md)
    (tmp_path / "agents" / "research.md").write_text(child_md)

    class _RouteScript(BaseChatModel):
        by_agent: dict[str, list[Any]] = Field(default_factory=dict)
        cursor: dict[str, dict[str, int]] = Field(default_factory=dict)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            agent = "unknown"
            for m in messages:
                content = getattr(m, "content", "") or ""
                if isinstance(content, str) and "AGENT_NAME=" in content:
                    agent = content.split("AGENT_NAME=", 1)[1].split("\n", 1)[0].strip()
                    break
            cur = self.cursor.setdefault(agent, {"i": 0})
            i = cur["i"]
            seq = self.by_agent[agent]
            cur["i"] = i + 1
            return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(seq[i]))])

        @property
        def _llm_type(self) -> str:
            return "s8_script"

    script = _RouteScript(
        by_agent={
            "lead": [
                AIMessage(content="", tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc1"}]),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {"task": {"goal": "x"}, "rationale": "x"},
                            "id": "tc2",
                        }
                    ],
                ),
                AIMessage(content="Could not send via delegation either."),
            ],
            "research": [
                # Child tries the same denied call.
                AIMessage(content="", tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc3"}]),
                AIMessage(content="Child also blocked."),
            ],
        }
    )
    send_tool = (
        {
            "name": "send",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"to": {"type": "string"}},
                "required": ["to"],
            },
            "risk_level": "L3",
            "side_effect": True,
        },
        lambda **kw: {"sent": True},
    )
    research = ModiAgent.from_markdown(
        tmp_path / "agents" / "research.md", tools=[send_tool]
    )
    lead = ModiAgent.from_markdown(
        tmp_path / "agents" / "lead.md", tools=[send_tool], subagents=[research]
    )
    h = make_session(tmp_path, chat_model=script, agents=[lead])
    first = h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-s8")
    assert first["status"] == "interrupted"
    rejected = h.reject_action(
        thread_id="t-s8",
        approval_id=first["pending_approval"]["approval_id"],
        reason="user denied",
    )
    assert rejected["status"] == "completed"
    state = h.get_state("t-s8")
    assert state is not None
    # Parent's denied list should still contain the original send call.
    assert any(d["tool_name"] == "send" for d in state["denied_actions"]), (
        f"expected denial of send; got {state['denied_actions']}"
    )


# ---------- S9 subagent delegation happy path ----------


def test_s9_subagent_delegation_happy_path(tmp_path: Path) -> None:
    """Release-coordinator delegates research to research-assistant and completes."""
    parent_md = """---
name: release-coordinator
description: smoke
tools:
  - delegate_to_research_assistant
permission_profile:
  mode: auto
  allowed_subagents: ["research-assistant"]
---
AGENT_NAME=release-coordinator
"""
    child_md = """---
name: research-assistant
description: smoke
tools:
  - web_search
permission_profile:
  mode: auto
---
AGENT_NAME=research-assistant
"""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "release-coordinator.md").write_text(parent_md)
    (tmp_path / "agents" / "research-assistant.md").write_text(child_md)

    class _RouteScript(BaseChatModel):
        by_agent: dict[str, list[Any]] = Field(default_factory=dict)
        cursor: dict[str, dict[str, int]] = Field(default_factory=dict)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            agent = "unknown"
            for m in messages:
                content = getattr(m, "content", "") or ""
                if isinstance(content, str) and "AGENT_NAME=" in content:
                    agent = content.split("AGENT_NAME=", 1)[1].split("\n", 1)[0].strip()
                    break
            cur = self.cursor.setdefault(agent, {"i": 0})
            i = cur["i"]
            seq = self.by_agent[agent]
            cur["i"] = i + 1
            return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(seq[i]))])

        @property
        def _llm_type(self) -> str:
            return "s9_script"

    web_search_tool = (
        {
            "name": "web_search",
            "description": "",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            "risk_level": "L1",
            "side_effect": False,
        },
        lambda **kw: {"hits": 0},
    )
    child = ModiAgent.from_markdown(
        tmp_path / "agents" / "research-assistant.md", tools=[web_search_tool]
    )
    parent = ModiAgent.from_markdown(
        tmp_path / "agents" / "release-coordinator.md", subagents=[child]
    )

    script = _RouteScript(
        by_agent={
            "release-coordinator": [
                # First call: delegate to research-assistant
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research_assistant",
                            "args": {
                                "task": {
                                    "goal": "research libcore breaking changes",
                                    "messages": [
                                        {"role": "user", "content": "research libcore breaking changes"}
                                    ],
                                },
                                "rationale": "Need upstream research",
                            },
                            "id": "tc_delegate",
                        }
                    ],
                ),
                # After getting subagent result, produce final output
                AIMessage(content="Release notes for v2.5: libcore v3.2 removed deprecated API foo()."),
            ],
            "research-assistant": [
                # Child produces research findings directly
                AIMessage(content="libcore v3.2 removed deprecated API foo() and renamed bar() to baz()."),
            ],
        }
    )
    h = make_session(
        tmp_path,
        chat_model=script,
        agents=[parent],
    )
    response = h.run_task(
        agent="release-coordinator",
        input={
            "goal": "Prepare release notes for v2.5. Research what breaking changes were introduced in upstream dependency 'libcore' between v3.1 and v3.2, then summarize them in the release notes draft.",
            "messages": [
                {
                    "role": "user",
                    "content": "Prepare release notes for v2.5. Research what breaking changes were introduced in upstream dependency 'libcore' between v3.1 and v3.2, then summarize them in the release notes draft.",
                }
            ],
        },
    )
    # Parent run completes
    assert response["status"] == "completed"
    # Response has a thread_id
    assert response["thread_id"]
    # Final output is not None
    assert response["output"] is not None
    # Trace contains delegate_to_research_assistant tool_result
    events = list(h.get_trace(response["thread_id"]))
    assert any(
        e["event_type"] == "tool_result" and e["payload"].get("tool_name") == "delegate_to_research_assistant"
        for e in events
    ), f"expected tool_result for delegate_to_research_assistant in trace; got types: {[e['event_type'] for e in events]}"
