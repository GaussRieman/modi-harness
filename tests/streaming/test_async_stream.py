"""Async streaming tests for ModiHarness.astream() and RuntimeAdapter.astream()."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiHarness
from modi_harness.runtime import RunTaskInput


class _Script(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "async_stream_script"


def _write_agent(root: Path, name: str, tools: list[str]) -> None:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tool_block = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    p.write_text(
        f"""---
name: {name}
description: async stream test
tools:
{tool_block}
---
Reply.
"""
    )


def _harness(tmp_path: Path, script: _Script) -> ModiHarness:
    return ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=script,
    )


@pytest.mark.asyncio
async def test_async_model_delta_per_token(tmp_path: Path) -> None:
    """N1.1: RuntimeAdapter.astream emits model_delta events with delta field."""
    _write_agent(tmp_path / "agents", "demo", tools=[])
    h = _harness(tmp_path, _Script(script=[AIMessage(content="hello async")]))
    events: list[dict] = []
    async for event in h._runtime.astream(
        RunTaskInput(agent="demo", input={"goal": "x"}, thread_id="t-async-1")
    ):
        events.append(event)

    assert events, "expected at least one event"
    # Should have model_delta events with a delta field
    deltas = [e for e in events if e["event_type"] == "model_delta"]
    assert deltas, "expected at least one model_delta event"
    for d in deltas:
        assert "delta" in d["payload"], "model_delta must carry a 'delta' field"
    # Terminal event at the end
    assert events[-1]["event_type"] == "terminal"


@pytest.mark.asyncio
async def test_harness_astream(tmp_path: Path) -> None:
    """N1.2: ModiHarness.astream mirrors sync stream() as async iterator."""
    _write_agent(tmp_path / "agents", "demo", tools=[])
    h = _harness(tmp_path, _Script(script=[AIMessage(content="hi from astream")]))
    events: list[dict] = []
    async for event in h.astream(agent="demo", input={"goal": "x"}, thread_id="t-async-2"):
        events.append(event)

    assert events, "expected at least one event"
    assert events[-1]["event_type"] == "terminal"
    resp = events[-1]["terminal_response"]
    assert resp["status"] == "completed"
    types = {e["event_type"] for e in events}
    assert "model_delta" in types


@pytest.mark.asyncio
async def test_astream_persists_trace_to_workspace(tmp_path: Path) -> None:
    """astream must flush pending_trace_events to logs/trace.jsonl just like run_task."""
    _write_agent(tmp_path / "agents", "demo", tools=[])
    h = _harness(tmp_path, _Script(script=[AIMessage(content="hello traced")]))

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
    _write_agent(tmp_path / "agents", "demo", tools=[])

    # Async streaming run
    h1 = _harness(tmp_path, _Script(script=[AIMessage(content="equiv")]))
    events: list[dict] = []
    async for event in h1.astream(agent="demo", input={"goal": "x"}, thread_id="t-eq-async"):
        events.append(event)
    streamed = events[-1]["terminal_response"]

    # Sync run_task with same input
    h2 = _harness(tmp_path, _Script(script=[AIMessage(content="equiv")]))
    direct = h2.run_task(agent="demo", input={"goal": "x"}, thread_id="t-eq-sync")

    assert streamed["status"] == direct["status"]
    assert streamed["output"] == direct["output"]
