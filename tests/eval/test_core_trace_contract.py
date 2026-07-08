from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

from modi_harness._test_fixtures import make_session, stable_trace_contract

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "core_trace_contract.json"


class _Script(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        if i >= len(self.script):
            raise RuntimeError(f"_Script exhausted after {i} calls")
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(self.script[i]))])

    @property
    def _llm_type(self) -> str:
        return "core_trace_contract_script"


def _agent_md() -> str:
    return """---
name: core-eval
description: core trace contract eval agent
tools:
  - search
permission_profile:
  mode: auto
output:
  type: structured
  schema:
    type: object
    properties:
      answer:
        type: string
    required: [answer]
---
Use search once, then submit the structured answer.
"""


def _search_spec() -> dict[str, Any]:
    return {
        "name": "search",
        "description": "Search test corpus",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        "risk_level": "L1",
        "side_effect": False,
        "idempotent": True,
    }


def test_core_agent_trace_matches_regression_contract(tmp_path: Path) -> None:
    session = make_session(
        tmp_path,
        chat_model=_Script(script=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "search",
                    "args": {"q": "modi"},
                    "id": "tc_search",
                }],
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "submit_output",
                    "args": {"answer": "modi result"},
                    "id": "tc_submit",
                }],
            ),
        ]),
        agent_files={"core-eval": _agent_md()},
        tools=[(_search_spec(), lambda **kw: {"hits": [kw["q"]], "source": "fixture"})],
        max_steps=6,
    )

    response = session.run_task(
        agent="core-eval",
        input={
            "goal": "Search for modi and return a structured answer",
            "success_criteria": ["answer includes the fixture result"],
        },
        thread_id="core-trace-contract",
    )

    assert response["status"] == "completed"
    events = list(session.get_trace("core-trace-contract"))
    actual = stable_trace_contract(events)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert actual == expected
