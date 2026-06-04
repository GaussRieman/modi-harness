"""Tests for ModiSession facade — V0.5 (thread_id keyed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiSession
from modi_harness._test_fixtures import make_session


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "script"


def _session(
    tmp_path: Path,
    *,
    chat_model: BaseChatModel,
    tools: list[tuple[dict, Any]] | None = None,
) -> ModiSession:
    return make_session(
        tmp_path,
        chat_model=chat_model,
        agent_files={
            "demo": """---
name: demo
description: demo
tools:
  - search
---
Use tools or reply directly.
""",
        },
        tools=tools,
    )


def test_run_task_returns_completed_response(tmp_path: Path) -> None:
    s = _session(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="hello back")]),
    )
    response = s.run_task(agent="demo", input={"goal": "say hi"})
    assert response["status"] == "completed"
    assert response["thread_id"]


def test_get_state_after_run(tmp_path: Path) -> None:
    s = _session(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
    )
    s.run_task(agent="demo", input={"goal": "x"}, thread_id="t-state")
    state = s.get_state("t-state")
    assert state is not None
    assert state["status"] == "completed"


def test_get_trace_returns_events(tmp_path: Path) -> None:
    s = _session(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
    )
    s.run_task(agent="demo", input={"goal": "x"}, thread_id="t-trace")
    events = list(s.get_trace("t-trace"))
    types = {e["event_type"] for e in events}
    assert {"run_start", "context_built", "model_call", "run_end"}.issubset(types)


def test_memory_round_trip(tmp_path: Path) -> None:
    s = _session(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
    )
    s.add_memory(
        {
            "id": "rec1",
            "scope": "user",
            "type": "feedback",
            "name": "tone",
            "description": "be terse",
            "body": "Reply in one sentence.",
            "tags": ["style"],
        }
    )
    listed = s.list_memory(scopes={"user"})
    assert any(r["id"] == "rec1" for r in listed)
    s.forget_memory("rec1")
    assert s.list_memory(scopes={"user"}) == []


def test_thread_lifecycle(tmp_path: Path) -> None:
    s = _session(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="hi"), AIMessage(content="hi again")]),
    )
    r1 = s.run_task(agent="demo", input={"goal": "1"}, thread_id="t-lifecycle")
    r2 = s.run_task(agent="demo", input={"goal": "2"}, thread_id="t-lifecycle")
    assert r1["thread_id"] == r2["thread_id"] == "t-lifecycle"
    threads = s.list_threads()
    assert any(t["thread_id"] == "t-lifecycle" for t in threads)
    s.end_thread("t-lifecycle")
    assert any(
        t["thread_id"] == "t-lifecycle" and t["status"] == "closed"
        for t in s.list_threads()
    )


def test_approve_and_reject_actions(tmp_path: Path) -> None:
    s = make_session(
        tmp_path,
        chat_model=_ScriptModel(
            script=[
                AIMessage(content="", tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc"}]),
                AIMessage(content="done"),
            ]
        ),
        agent_files={
            "send_demo": """---
name: send_demo
description: demo
tools:
  - send
---
Reply with the tool.
""",
        },
        tools=[
            (
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
                lambda **kw: {"sent": kw["to"]},
            )
        ],
    )
    first = s.run_task(agent="send_demo", input={"goal": "send"}, thread_id="t-approve")
    assert first["status"] == "interrupted"
    approval_id = first["pending_approval"]["approval_id"]
    second = s.approve_action(thread_id="t-approve", approval_id=approval_id, decision="approved")
    assert second["status"] == "completed"
