"""End-to-end: scripted model calls builtin tools through the full graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

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
        return "builtin_e2e_script"


_DEMO_AGENT = """---
name: demo
description: builtin e2e
tools: []
permission_profile:
  mode: auto
---
Reply.
"""


def test_agent_calls_builtin_save_draft_without_listing_it(tmp_path: Path) -> None:
    session = make_session(
        tmp_path,
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
        agent_files={"demo": _DEMO_AGENT},
    )

    response = session.run_task(
        agent="demo",
        input={"goal": "save a draft", "messages": [{"role": "user", "content": "go"}]},
        thread_id="t-builtin-e2e",
    )
    assert response["status"] == "completed"

    # Draft is on disk.
    drafts = list((tmp_path / "ws" / response["run_id"] / "drafts").iterdir())
    assert any(p.name == "summary.md" for p in drafts)

    # Trace records the call.
    events = list(session.get_trace(response["thread_id"]))
    tool_results = [e for e in events if e["event_type"] == "tool_result"]
    assert any(e["payload"].get("tool_name") == "save_draft" for e in tool_results)
