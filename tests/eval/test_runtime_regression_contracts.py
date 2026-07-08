from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

from modi_harness._test_fixtures import make_session, stable_trace_contract


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
        return "runtime_regression_contract_script"


def _events_by_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["event_type"] == event_type]


def _send_agent_md() -> str:
    return """---
name: judgment-eval
description: judgment regression eval
tools:
  - send
permission_profile:
  mode: auto
---
Use send only when aligned with the current intent.
"""


def _send_spec() -> dict[str, Any]:
    return {
        "name": "send",
        "description": "Send a side-effecting message",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
        },
        "risk_level": "L3",
        "side_effect": True,
        "idempotent": False,
    }


def test_judgment_redirect_updates_intent_and_keeps_action_lineage(tmp_path) -> None:
    session = make_session(
        tmp_path,
        chat_model=_Script(script=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "send",
                    "args": {"to": "wrong-person@example.test"},
                    "id": "tc_send",
                }],
            ),
            AIMessage(content="Redirect acknowledged."),
        ]),
        agent_files={"judgment-eval": _send_agent_md()},
        tools=[(_send_spec(), lambda **kw: {"sent": kw["to"]})],
        max_steps=6,
    )

    first = session.run_task(
        agent="judgment-eval",
        input={"goal": "send a note only after the recipient is confirmed"},
        thread_id="judgment-regression",
    )
    assert first["status"] == "interrupted"
    pending = first["pending_judgment"]
    assert pending is not None
    version_before = session.get_state("judgment-regression")["intent_version"]  # type: ignore[index]

    final = session.respond_to_judgment(
        thread_id="judgment-regression",
        judgment_id=pending["judgment_id"],
        kind="redirect",
        rationale="recipient is not confirmed",
        intent_updates={
            "goal": "confirm the recipient before sending any note",
            "add_success_criteria": ["recipient is explicitly confirmed"],
        },
    )

    assert final["status"] == "completed"
    state_after = session.get_state("judgment-regression")
    assert state_after["intent_version"] > version_before  # type: ignore[index]
    assert state_after["human_intent"]["goal"] == "confirm the recipient before sending any note"  # type: ignore[index]
    assert "recipient is explicitly confirmed" in state_after["human_intent"]["success_criteria"]  # type: ignore[index]

    events = list(session.get_trace("judgment-regression"))
    requested = _events_by_type(events, "judgment_requested")
    resolved = _events_by_type(events, "judgment_resolved")
    updated = _events_by_type(events, "intent_updated")
    assert requested
    assert resolved
    assert updated
    assert requested[0]["payload"]["target_action_id"]
    assert requested[0]["payload"]["target_action_id"] != "tc_send"
    assert resolved[0]["payload"]["kind"] == "redirect"
    assert resolved[0]["payload"]["target_action_id"] == requested[0]["payload"]["target_action_id"]
    assert resolved[0]["payload"]["intent_version"] == state_after["intent_version"]
    assert updated[-1]["payload"]["intent_version"] == state_after["intent_version"]


def _retry_agent_md() -> str:
    return """---
name: retry-eval
description: retry regression eval
tools:
  - flaky_search
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
Use flaky_search once, then submit the structured answer.
"""


def _flaky_search_spec() -> dict[str, Any]:
    return {
        "name": "flaky_search",
        "description": "Search with one transient failure",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        "risk_level": "L1",
        "side_effect": False,
        "idempotent": True,
        "retry": {
            "max_attempts": 2,
            "backoff_seconds": 0,
            "retry_on": ["timeout"],
        },
    }


def test_retry_attempts_and_run_summary_are_regression_checked(tmp_path) -> None:
    calls = {"n": 0}

    def flaky_search(**kw: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("transient timeout")
        return {"ok": True, "attempt": calls["n"], "query": kw["q"]}

    session = make_session(
        tmp_path,
        chat_model=_Script(script=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "flaky_search",
                    "args": {"q": "modi"},
                    "id": "tc_flaky",
                }],
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "submit_output",
                    "args": {"answer": "retry recovered"},
                    "id": "tc_submit",
                }],
            ),
        ]),
        agent_files={"retry-eval": _retry_agent_md()},
        tools=[(_flaky_search_spec(), flaky_search)],
        max_steps=6,
    )

    response = session.run_task(
        agent="retry-eval",
        input={
            "goal": "Search modi and return a structured answer",
            "source_urls": ["https://example.test/modi"],
        },
        thread_id="retry-regression",
    )

    assert response["status"] == "completed"
    assert calls["n"] == 2
    events = list(session.get_trace("retry-regression"))
    contract = stable_trace_contract(events)
    assert contract["intent"] == {
        "initialized": {"stage": "explore"},
        "clarity": {"level": "operational"},
        "autonomy": {"mode": "bounded"},
    }
    assert contract["actions"] == [{
        "kind": "tool_call",
        "tool_name": "flaky_search",
        "intent_version": 1,
        "stage": "explore",
    }]
    assert contract["run_end"]["model_calls"] == 0
    assert contract["run_end"]["tool_attempts"] == 2
    assert contract["run_end"]["tool_failures"] == 0
    assert contract["run_end"]["model_usage_total_tokens_min"] == ">=0"
    assert contract["run_end"]["tool_latency_ms_min"] == ">=0"

    tool_payload = _events_by_type(events, "tool_result")[0]["payload"]
    assert tool_payload["attempt"] == 2
    assert tool_payload["attempts"] == [
        {"attempt": 1, "outcome": "error", "error_code": "timeout", "timeout": True, "terminal": False},
        {"attempt": 2, "outcome": "success", "error_code": None, "timeout": False},
    ]
    assert tool_payload["error_code"] is None
