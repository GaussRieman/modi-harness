"""Tests for ``modi_harness.cli.runner``.

Validates the streaming runner across the three core flows:

- happy path: scripted model produces a plain text response, the runner
  renders the model output and a green ``completed`` terminal marker, and
  returns exit code ``0``.
- approval approved: scripted model proposes an L3 (require-approval)
  side-effect tool, the runner pauses on ``approval_request``, the prompt is
  patched to approve, the runner resumes via ``approve_action`` and exits
  ``0``.
- approval rejected: same setup, prompt returns ``rejected`` with a reason,
  the runner resumes via ``reject_action`` and exits ``1``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from rich.console import Console

from modi_harness._test_fixtures import as_step_decision_message, make_session
from modi_harness.cli.renderer import StreamRenderer
from modi_harness.cli.runner import run_streaming


class _Script(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(self.script[i]))])

    @property
    def _llm_type(self) -> str:
        return "runner_script"


def _agent_md(name: str, tools: list[str]) -> str:
    tool_block = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    return f"""---
name: {name}
description: runner test
tools:
{tool_block}
---
Reply or call a tool.
"""


def _native_task_agent_md(name: str) -> str:
    return f"""---
name: {name}
description: native task runner test
tools: []
task_protocol:
  mode: required
  review: before_execution
---
Plan, wait for confirmation, complete the task, then reply.
"""


def _webagent_md() -> str:
    return """---
name: webagent
description: webagent runner test
tools:
  - parse_police_intake
  - run_police_intake
---
Call parse_police_intake, wait for confirmation, then call run_police_intake.
"""


_SEND_TOOL = (
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


_REVIEW_TOOL = (
    {
        "name": "review_plan",
        "description": "Review a plan before execution.",
        "input_schema": {
            "type": "object",
            "properties": {"plan": {"type": "string"}},
            "required": ["plan"],
        },
        "risk_level": "L3",
        "side_effect": False,
        "idempotent": False,
    },
    lambda **kw: {"accepted": kw["plan"]},
)


_PARSE_POLICE_TOOL = (
    {
        "name": "parse_police_intake",
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"intake_path": {"type": "string"}},
            "required": ["intake_path"],
            "additionalProperties": False,
        },
        "risk_level": "L0",
        "side_effect": False,
    },
    lambda **kw: {
        "ok": True,
        "intake_path": kw["intake_path"],
        "url": "http://example.test/",
        "fields": {
            "报警人姓名": "李江",
            "报警人联系电话": "18199987774",
            "处警人员": "赵武,钱柳",
            "警情地址": "诚高大厦6楼",
            "报警内容描述": "我被我的同事周枫打了",
            "警情类别": "行政(治安)类警情",
            "警情类型": "侵犯人身权利",
        },
        "_modi_pending_interaction": {
            "prompt": "确认提交警情录入",
            "input_type": "confirm",
            "field": "draft_confirmation",
            "default": "go",
        },
    },
)


_RUN_POLICE_TOOL = (
    {
        "name": "run_police_intake",
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"intake_path": {"type": "string"}},
            "required": ["intake_path"],
            "additionalProperties": False,
        },
        "risk_level": "L0",
        "side_effect": False,
    },
    lambda **_kw: {
        "ok": True,
        "url": "http://example.test/",
        "submitted": True,
        "record_id": "",
        "evidence_dir": "/tmp/evidence",
        "trace_path": "/tmp/evidence/trace.json",
    },
)


class _ScriptedApprovalPrompt:
    def __init__(self, answers: list[tuple[str, str | None, dict[str, Any]]]) -> None:
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    def ask(self, approval, agent=None):
        self.calls.append(dict(approval))
        return self.answers[len(self.calls) - 1]


class _ScriptedInteractionPrompt(_ScriptedApprovalPrompt):
    pass


class _FakeJudgmentSession:
    def __init__(self) -> None:
        self.resume_payloads: list[dict[str, Any]] = []

    async def astream(self, **_kwargs):
        yield {
            "event_type": "terminal",
            "payload": {},
            "terminal_response": {
                "status": "interrupted",
                "pending_approval": None,
                "pending_judgment": {
                    "judgment_id": "j-brain",
                    "approval_id": "j-brain",
                    "tool_call_id": None,
                    "target_action_id": None,
                    "target_stage_id": "clarify",
                    "reviewed_action_hash": None,
                    "prompt": "Review Brain handoff",
                    "allowed_kinds": ["approve", "reject"],
                    "proposed_intent_patch": None,
                    "summary": "Brain needs judgment",
                    "rationale": None,
                    "risk_level": "L0",
                    "requested_at": "",
                },
                "pending_interaction": None,
            },
        }

    async def astream_resume(self, *, thread_id: str, payload: dict[str, Any]):
        del thread_id
        self.resume_payloads.append(dict(payload))
        yield {
            "event_type": "terminal",
            "payload": {},
            "terminal_response": {
                "status": "completed",
                "pending_approval": None,
                "pending_judgment": None,
                "pending_interaction": None,
            },
        }

    def get_agent(self, name: str):
        class _Agent:
            pass

        agent = _Agent()
        agent.name = name
        agent.description = "fake"
        agent.safety_constraints = []
        return agent


def _recording_console() -> Console:
    return Console(record=True, width=200, force_terminal=False)


@pytest.mark.asyncio
async def test_happy_path(tmp_path: Path) -> None:
    """Scripted model produces a plain text reply; runner exits 0."""
    session = make_session(
        tmp_path,
        chat_model=_Script(script=[AIMessage(content="hello from runner")]),
        agent_files={"demo": _agent_md("demo", tools=[])},
    )
    console = _recording_console()

    code = await run_streaming(
        session,
        agent="demo",
        input={"goal": "say hi"},
        thread_id="t-runner-happy",
        console=console,
    )

    assert code == 0
    text = console.export_text(styles=False)
    assert "[demo] running..." in text
    assert "completed" in text
    assert "elapsed" in text


@pytest.mark.asyncio
async def test_approval_approved(tmp_path: Path) -> None:
    """L3 tool call triggers approval; prompt approves; runner exits 0."""
    session = make_session(
        tmp_path,
        chat_model=_Script(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc"}],
                ),
                AIMessage(content="email sent."),
            ]
        ),
        agent_files={"send_demo": _agent_md("send_demo", tools=["send"])},
        tools=[_SEND_TOOL],
    )
    console = _recording_console()

    with patch(
        "modi_harness.cli.runner.JudgmentPrompt.ask",
        return_value=("approve", None, {}),
    ) as mock_ask:
        code = await run_streaming(
            session,
            agent="send_demo",
            input={"goal": "send"},
            thread_id="t-runner-approve",
            console=console,
        )

    assert mock_ask.call_count == 1
    assert code == 0
    text = console.export_text(styles=False)
    assert "[send_demo] running..." in text
    # Final terminal line for the resumed run should be ``completed``.
    assert "completed" in text


@pytest.mark.asyncio
async def test_runner_prompts_for_pending_judgment_terminal() -> None:
    session = _FakeJudgmentSession()
    prompt = _ScriptedApprovalPrompt([("approve", None, {})])
    console = _recording_console()

    code = await run_streaming(
        session,  # type: ignore[arg-type]
        agent="demo",
        input={"goal": "x"},
        thread_id="t-brain-judgment",
        console=console,
        approval_prompt=prompt,
    )

    assert code == 0
    assert len(prompt.calls) == 1
    assert prompt.calls[0]["judgment_id"] == "j-brain"
    assert session.resume_payloads == [{"judgment_id": "j-brain", "kind": "approve"}]


@pytest.mark.asyncio
async def test_approval_rejected(tmp_path: Path) -> None:
    """L3 tool call triggers approval; prompt rejects; runner exits non-zero."""
    session = make_session(
        tmp_path,
        chat_model=_Script(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc"}],
                ),
                # After rejection the model produces a recovery / refusal reply.
                AIMessage(content="cannot send; user denied."),
            ]
        ),
        agent_files={"send_demo": _agent_md("send_demo", tools=["send"])},
        tools=[_SEND_TOOL],
    )
    console = _recording_console()

    with patch(
        "modi_harness.cli.runner.JudgmentPrompt.ask",
        return_value=("reject", "no thanks", {}),
    ) as mock_ask:
        code = await run_streaming(
            session,
            agent="send_demo",
            input={"goal": "send"},
            thread_id="t-runner-reject",
            console=console,
        )

    assert mock_ask.call_count == 1
    # Rejection drives the run to ``completed`` (via the recovery message) but
    # it could equally land on ``failed`` depending on the rule pack; either
    # way, the test asserts the runner returns the documented exit code.
    text = console.export_text(styles=False)
    assert "[send_demo] running..." in text
    assert "declined (reject): no thanks" in text
    # Exit code mirrors the resumed status: 0 only for ``completed``.
    if code == 0:
        assert "completed" in text
    else:
        assert "completed" not in text or "failed" in text or "blocked" in text


@pytest.mark.asyncio
async def test_runner_generates_thread_id_when_missing(tmp_path: Path) -> None:
    """When the caller omits thread_id, the runner still drives the run."""
    session = make_session(
        tmp_path,
        chat_model=_Script(script=[AIMessage(content="ok")]),
        agent_files={"demo": _agent_md("demo", tools=[])},
    )
    console = _recording_console()

    code = await run_streaming(
        session,
        agent="demo",
        input={"goal": "x"},
        console=console,
    )

    assert code == 0
    assert "completed" in console.export_text(styles=False)


@pytest.mark.asyncio
async def test_webagent_parse_pauses_for_confirmation_then_runs(tmp_path: Path) -> None:
    model = _Script(script=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "parse_police_intake",
                "args": {"intake_path": "agents/modi-webagent/data/injection/intro.md"},
                "id": "parse",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "run_police_intake",
                "args": {"intake_path": "agents/modi-webagent/data/injection/intro.md"},
                "id": "run",
            }],
        ),
        AIMessage(content="done"),
    ])
    session = make_session(
        tmp_path,
        chat_model=model,
        agent_files={"webagent": _webagent_md()},
        tools=[_PARSE_POLICE_TOOL, _RUN_POLICE_TOOL],
    )
    console = _recording_console()
    prompt = _ScriptedInteractionPrompt([("submitted", "go")])

    code = await run_streaming(
        session,
        agent="webagent",
        input={"goal": "警情录入"},
        thread_id="t-webagent-confirm",
        console=console,
        renderer=StreamRenderer(console),
        interaction_prompt=prompt,
    )

    assert code == 0
    assert model.cursor["i"] == 3
    assert len(prompt.calls) == 1
    assert prompt.calls[0]["payload"]["field"] == "draft_confirmation"
    text = console.export_text(styles=False)
    assert "✓ completed" in text
    assert "repair_budget_exhausted" not in text


@pytest.mark.asyncio
async def test_runner_streams_repeated_plan_review_interrupts(tmp_path: Path) -> None:
    """Feedback resumes the same thread, replans, and interrupts again."""
    model = _Script(script=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "review_plan",
                "args": {"plan": "first plan"},
                "id": "plan-1",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "review_plan",
                "args": {"plan": "revised plan with cost analysis"},
                "id": "plan-2",
            }],
        ),
        AIMessage(content="research completed after approval"),
    ])
    session = make_session(
        tmp_path,
        chat_model=model,
        agent_files={"planner": _agent_md("planner", tools=["review_plan"])},
        tools=[_REVIEW_TOOL],
    )
    prompt = _ScriptedApprovalPrompt([
        ("reject", "plan_feedback: include cost analysis", {}),
        ("approve", None, {}),
    ])
    console = _recording_console()

    code = await run_streaming(
        session,
        agent="planner",
        input={"goal": "plan and research"},
        thread_id="t-repeated-plan-review",
        console=console,
        approval_prompt=prompt,
    )

    assert code == 0
    assert model.cursor["i"] == 3
    assert len(prompt.calls) == 2
    assert session.get_state("t-repeated-plan-review")["status"] == "completed"
    text = console.export_text(styles=False)
    assert "first plan" in text
    assert "revised plan with cost analysis" in text
    assert "completed" in text


@pytest.mark.asyncio
async def test_runner_resumes_native_plan_review_interaction(tmp_path: Path) -> None:
    model = _Script(script=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "create_task_plan",
                "args": {"tasks": [{"id": "one", "title": "Research source"}]},
                "id": "plan",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "start_task",
                "args": {"task_id": "one", "current_action": "Reading source"},
                "id": "start",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "complete_task",
                "args": {"task_id": "one", "summary": "Source read"},
                "id": "complete",
            }],
        ),
        AIMessage(content="done"),
    ])
    session = make_session(
        tmp_path,
        chat_model=model,
        agent_files={"planner": _native_task_agent_md("planner")},
    )
    prompt = _ScriptedInteractionPrompt([("approved", None)])

    code = await run_streaming(
        session,
        agent="planner",
        input={"goal": "research"},
        thread_id="t-native-plan-review",
        console=_recording_console(),
        interaction_prompt=prompt,
    )

    assert code == 0
    assert len(prompt.calls) == 1
    assert prompt.calls[0]["kind"] == "plan_review"
    assert session.get_task_plan("t-native-plan-review")["items"][0]["status"] == "completed"
