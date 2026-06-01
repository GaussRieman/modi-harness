"""End-to-end: scripted model calls builtin tools through the full graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
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
        return "builtin_e2e_script"


def _write_agent(root: Path, name: str) -> None:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    # Note: tools: [] — no domain tools listed, only builtins should be visible.
    p.write_text(
        f"""---
name: {name}
description: builtin e2e
tools: []
permission_profile:
  mode: auto
---
Reply.
"""
    )


def test_agent_calls_builtin_save_draft_without_listing_it(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo")
    h = ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=_Script(script=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "save_draft",
                    "args": {"name": "summary.md", "content": "hello"},
                    "id": "tc1",
                }],
            ),
            AIMessage(content="done"),
        ]),
    )

    response = h.run_task(
        agent="demo",
        input={"goal": "save a draft", "messages": [{"role": "user", "content": "go"}]},
        thread_id="t-builtin-e2e",
    )
    assert response["status"] == "completed"

    # Draft is on disk.
    drafts = list((tmp_path / "ws" / response["run_id"] / "drafts").iterdir())
    assert any(p.name == "summary.md" for p in drafts)

    # Trace records the call.
    events = list(h.get_trace(response["thread_id"]))
    tool_results = [e for e in events if e["event_type"] == "tool_result"]
    assert any(e["payload"].get("tool_name") == "save_draft" for e in tool_results)
