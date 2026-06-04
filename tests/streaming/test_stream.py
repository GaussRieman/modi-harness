"""Streaming smoke for ModiSession.stream()."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiSession
from modi_harness._test_fixtures import make_session


class _Script(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "stream_script"


def _agent_md(name: str, tools: list[str]) -> str:
    tool_block = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    return f"""---
name: {name}
description: stream
tools:
{tool_block}
---
Reply.
"""


def _session(tmp_path: Path, script: _Script, *, tools: list[str], tool_specs=None) -> ModiSession:
    return make_session(
        tmp_path,
        chat_model=script,
        agent_files={"demo": _agent_md("demo", tools)},
        tools=tool_specs,
    )


def test_stream_emits_terminal(tmp_path: Path) -> None:
    h = _session(tmp_path, _Script(script=[AIMessage(content="hello")]), tools=[])
    events = list(h.stream(agent="demo", input={"goal": "x"}, thread_id="t-stream"))
    assert events, "expected at least one event"
    assert events[-1]["event_type"] == "terminal"
    resp = events[-1]["terminal_response"]
    assert resp["status"] == "completed"
    types = {e["event_type"] for e in events}
    assert "model_delta" in types


def test_stream_tool_call_sequence(tmp_path: Path) -> None:
    search_spec = {
        "name": "search",
        "description": "",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        "risk_level": "L1",
        "side_effect": False,
    }
    h = _session(
        tmp_path,
        _Script(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "tc1"}],
                ),
                AIMessage(content="done"),
            ]
        ),
        tools=["search"],
        tool_specs=[(search_spec, lambda **kw: {"hits": 1})],
    )
    events = list(h.stream(agent="demo", input={"goal": "x"}, thread_id="t-stream-2"))
    types = [e["event_type"] for e in events]
    assert "tool_call_proposal" in types
    assert "tool_call_result" in types
    assert types[-1] == "terminal"


def test_stream_persists_trace_to_workspace(tmp_path: Path) -> None:
    """Sync stream() must flush pending_trace_events to logs/trace.jsonl."""
    h = _session(tmp_path, _Script(script=[AIMessage(content="hello traced")]), tools=[])

    run_id: str | None = None
    for event in h.stream(agent="demo", input={"goal": "x"}, thread_id="t-stream-trace"):
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


def test_stream_terminal_equals_run_task(tmp_path: Path) -> None:
    """Streaming terminal payload == run_task() return for same input."""
    # streaming run
    h1 = _session(tmp_path, _Script(script=[AIMessage(content="foo")]), tools=[])
    events = list(h1.stream(agent="demo", input={"goal": "x"}, thread_id="t-eq-1"))
    streamed = events[-1]["terminal_response"]

    # non-streaming run with the same input
    h2 = _session(tmp_path, _Script(script=[AIMessage(content="foo")]), tools=[])
    direct = h2.run_task(agent="demo", input={"goal": "x"}, thread_id="t-eq-2")

    assert streamed["status"] == direct["status"]
    assert streamed["output"] == direct["output"]
