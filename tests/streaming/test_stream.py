"""Streaming smoke for ModiHarness.stream()."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiHarness


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


def _write_agent(root: Path, name: str, tools: list[str]) -> None:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tool_block = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    p.write_text(
        f"""---
name: {name}
description: stream
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


def test_stream_emits_terminal(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo", tools=[])
    h = _harness(tmp_path, _Script(script=[AIMessage(content="hello")]))
    events = list(h.stream(agent="demo", input={"goal": "x"}, thread_id="t-stream"))
    assert events, "expected at least one event"
    assert events[-1]["event_type"] == "terminal"
    resp = events[-1]["terminal_response"]
    assert resp["status"] == "completed"
    types = {e["event_type"] for e in events}
    assert "model_delta" in types


def test_stream_tool_call_sequence(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo", tools=["search"])
    h = _harness(
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
    )
    h.register_tool(
        {
            "name": "search",
            "description": "",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            "risk_level": "L1",
            "side_effect": False,
        },
        lambda **kw: {"hits": 1},
    )
    events = list(h.stream(agent="demo", input={"goal": "x"}, thread_id="t-stream-2"))
    types = [e["event_type"] for e in events]
    assert "tool_call_proposal" in types
    assert "tool_call_result" in types
    assert types[-1] == "terminal"


def test_stream_terminal_equals_run_task(tmp_path: Path) -> None:
    """Streaming terminal payload == run_task() return for same input."""
    _write_agent(tmp_path / "agents", "demo", tools=[])
    # streaming run
    h1 = _harness(tmp_path, _Script(script=[AIMessage(content="foo")]))
    events = list(h1.stream(agent="demo", input={"goal": "x"}, thread_id="t-eq-1"))
    streamed = events[-1]["terminal_response"]

    # non-streaming run with the same input
    h2 = _harness(tmp_path, _Script(script=[AIMessage(content="foo")]))
    direct = h2.run_task(agent="demo", input={"goal": "x"}, thread_id="t-eq-2")

    assert streamed["status"] == direct["status"]
    assert streamed["output"] == direct["output"]
