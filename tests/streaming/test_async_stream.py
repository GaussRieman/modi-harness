"""Async streaming tests for ModiSession.astream()."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

from modi_harness import ModiSession
from modi_harness._test_fixtures import make_session


class _Script(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(self.script[i]))])

    @property
    def _llm_type(self) -> str:
        return "async_stream_script"


def _agent_md(name: str, tools: list[str]) -> str:
    tool_block = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    return f"""---
name: {name}
description: async stream test
tools:
{tool_block}
---
Reply.
"""


def _session(tmp_path: Path, script: _Script) -> ModiSession:
    return make_session(
        tmp_path,
        chat_model=script,
        agent_files={"demo": _agent_md("demo", [])},
    )


@pytest.mark.asyncio
async def test_async_model_delta_per_token(tmp_path: Path) -> None:
    """N1.1: astream emits model_delta events with delta field."""
    h = _session(tmp_path, _Script(script=[AIMessage(content="hello async")]))
    events: list[dict] = []
    async for event in h.astream(agent="demo", input={"goal": "x"}, thread_id="t-async-1"):
        events.append(event)

    assert events, "expected at least one event"
    assert events[-1]["event_type"] == "terminal"
    assert events[-1]["terminal_response"]["status"] == "completed"


@pytest.mark.asyncio
async def test_harness_astream(tmp_path: Path) -> None:
    """N1.2: ModiSession.astream mirrors sync stream() as async iterator."""
    h = _session(tmp_path, _Script(script=[AIMessage(content="hi from astream")]))
    events: list[dict] = []
    async for event in h.astream(agent="demo", input={"goal": "x"}, thread_id="t-async-2"):
        events.append(event)

    assert events, "expected at least one event"
    assert events[-1]["event_type"] == "terminal"
    resp = events[-1]["terminal_response"]
    assert resp["status"] == "completed"
    types = {e["event_type"] for e in events}
    assert types == {"terminal"}


@pytest.mark.asyncio
async def test_astream_persists_trace_to_workspace(tmp_path: Path) -> None:
    """astream must flush pending_trace_events to logs/trace.jsonl just like run_task."""
    h = _session(tmp_path, _Script(script=[AIMessage(content="hello traced")]))

    run_id: str | None = None
    async for event in h.astream(agent="demo", input={"goal": "x"}, thread_id="t-trace"):
        if event["event_type"] == "terminal":
            run_id = event["terminal_response"]["run_id"]

    assert run_id, "expected a run_id from terminal event"
    trace_path = tmp_path / "ws" / run_id / "logs" / "trace.jsonl"
    assert trace_path.exists(), f"trace.jsonl not written at {trace_path}"
    lines = [ln for ln in trace_path.read_text().splitlines() if ln.strip()]
    assert lines, "trace.jsonl is empty"
    types = {__import__("json").loads(ln)["event_type"] for ln in lines}
    assert "run_start" in types
    assert "run_end" in types


@pytest.mark.asyncio
async def test_async_sync_equivalence(tmp_path: Path) -> None:
    """N1.3: Async terminal payload matches sync run_task response."""
    # Async streaming run
    h1 = _session(tmp_path, _Script(script=[AIMessage(content="equiv")]))
    events: list[dict] = []
    async for event in h1.astream(agent="demo", input={"goal": "x"}, thread_id="t-eq-async"):
        events.append(event)
    streamed = events[-1]["terminal_response"]

    # Sync run_task with same input
    h2 = _session(tmp_path, _Script(script=[AIMessage(content="equiv")]))
    direct = h2.run_task(agent="demo", input={"goal": "x"}, thread_id="t-eq-sync")

    assert streamed["status"] == direct["status"]
    assert streamed["output"] == direct["output"]
