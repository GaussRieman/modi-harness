"""Tests for ModiHarness facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiHarness
from modi_harness.tools import ToolRegistry


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[override]
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "script"


def _write_agent(tmp_path: Path, name: str, body: str) -> None:
    p = tmp_path / "agents" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _harness(
    tmp_path: Path,
    *,
    chat_model: BaseChatModel,
    tools: list[tuple[dict, Any]] | None = None,
) -> ModiHarness:
    _write_agent(
        tmp_path,
        "demo",
        """---
name: demo
description: demo
tools:
  - search
---
Use tools or reply directly.
""",
    )
    h = ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=chat_model,
    )
    for spec, handler in tools or []:
        h.register_tool(spec, handler)
    return h


def test_run_task_returns_completed_response(tmp_path: Path) -> None:
    h = _harness(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="hello back")]),
    )
    response = h.run_task(agent="demo", input={"goal": "say hi"})
    assert response["status"] == "completed"
    assert response["run_id"]


def test_get_state_after_run(tmp_path: Path) -> None:
    h = _harness(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
    )
    response = h.run_task(agent="demo", input={"goal": "x"})
    state = h.get_state(response["run_id"])
    assert state is not None
    assert state["status"] == "completed"


def test_get_trace_returns_events(tmp_path: Path) -> None:
    h = _harness(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
    )
    response = h.run_task(agent="demo", input={"goal": "x"})
    events = list(h.get_trace(response["run_id"]))
    types = {e["event_type"] for e in events}
    assert {"run_start", "context_built", "model_call", "run_end"}.issubset(types)


def test_memory_round_trip(tmp_path: Path) -> None:
    h = _harness(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
    )
    h.add_memory(
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
    listed = h.list_memory(scopes={"user"})
    assert any(r["id"] == "rec1" for r in listed)
    h.forget_memory("rec1")
    assert h.list_memory(scopes={"user"}) == []


def test_thread_lifecycle(tmp_path: Path) -> None:
    h = _harness(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="hi"), AIMessage(content="hi again")]),
    )
    info = h.start_thread(agent="demo")
    assert info["thread_id"]
    r1 = h.run_task(agent="demo", input={"goal": "1"}, thread_id=info["thread_id"])
    r2 = h.run_task(agent="demo", input={"goal": "2"}, thread_id=info["thread_id"])
    assert r1["thread_id"] == r2["thread_id"] == info["thread_id"]
    threads = h.list_threads()
    assert any(t["thread_id"] == info["thread_id"] for t in threads)
    h.end_thread(info["thread_id"])


def test_approve_and_reject_actions(tmp_path: Path) -> None:
    h = _harness(
        tmp_path,
        chat_model=_ScriptModel(
            script=[
                AIMessage(content="", tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc"}]),
                AIMessage(content="done"),
            ]
        ),
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
    # Demo agent default_tools is ["search"]; we replace by registering then
    # re-loading via a per-test agent spec.
    _write_agent(
        tmp_path,
        "send_demo",
        """---
name: send_demo
description: demo
tools:
  - send
---
Reply with the tool.
""",
    )
    first = h.run_task(agent="send_demo", input={"goal": "send"})
    assert first["status"] == "interrupted"
    approval_id = first["pending_approval"]["approval_id"]
    second = h.approve_action(run_id=first["run_id"], approval_id=approval_id, decision="approved")
    assert second["status"] == "completed"
