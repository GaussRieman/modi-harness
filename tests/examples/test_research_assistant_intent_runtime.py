"""N9 — research-assistant validation slice over the intent-aligned runtime.

The first real agent proves the redesign end to end. Three slices:

- **Operational happy path** — ``research_question + source_urls`` opens an
  ``operational`` intent in ``bounded`` autonomy at the ``explore`` stage; fetch
  actions are allowed (not auto-escalated); the run delivers through the task
  protocol.
- **Thin intent path** — a bare request opens a ``thin`` intent in ``guided``
  autonomy at the ``clarify`` stage; the runtime lets the agent ask for source
  URLs and does not fail for missing intent.
- **Insufficient-evidence redirect** — proposing a ``deliver`` transition before
  the success bar exists interrupts for human judgment; a redirect updates the
  intent and the trace shows the judgment lineage.

These run the real ``research-assistant`` agent through a ``ModiSession`` (the
public surface), scripting only the model. Intent/clarity/autonomy assertions
read the trace the runtime emits.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

_RUN_PATH = Path(__file__).resolve().parents[2] / "examples" / "research_assistant" / "run.py"


def _load_run_module():
    spec = importlib.util.spec_from_file_location("research_assistant_run", _RUN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        if i >= len(self.script):
            raise RuntimeError(f"_ScriptModel exhausted after {i} calls")
        msg = self.script[i]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(msg))])

    @property
    def _llm_type(self) -> str:
        return "script"


def _events_by_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e["event_type"] == event_type]


def _one_payload(events: list[dict], event_type: str) -> dict:
    matches = _events_by_type(events, event_type)
    assert matches, f"no {event_type} event in trace"
    return matches[0]["payload"]


# ---------------------------------------------------------------------------
# N9.1 — operational happy path
# ---------------------------------------------------------------------------


def _fake_urlopen_factory(run: Any):
    html = (
        b"<html><head><title>X vs Y</title></head><body><main>"
        b"<p>X uses self-attention and trains in parallel.</p>"
        b"<p>Y processes tokens sequentially.</p>"
        b"</main></body></html>"
    )

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def read(self, size: int = -1) -> bytes:
            return html if size < 0 else html[:size]

        headers: ClassVar[dict[str, str]] = {"Content-Type": "text/html; charset=utf-8"}

        def geturl(self) -> str:
            return "https://example.com/x"

    def fake_urlopen(req, timeout):
        return _Resp()

    return fake_urlopen


def _happy_path_script(run: Any) -> list[AIMessage]:
    tasks = [
        {"id": "scope", "title": "界定可比较范围"},
        {"id": "compare", "title": "形成证据支持的比较结论"},
        {"id": "answer", "title": "回答研究问题并标注限制"},
    ]
    submit = {
        "research_question": "比较 X 和 Y",
        "executive_summary": "X 更适合并行训练。",
        "task_results": [
            {"task": "界定可比较范围", "result": "确定比较范围。", "evidence": ["example.com/x"], "limitations": []},
            {"task": "形成证据支持的比较结论", "result": "X 并行能力更强。", "evidence": ["example.com/x"], "limitations": ["缺少外部基准"]},
            {"task": "回答研究问题并标注限制", "result": "已形成有边界比较答案。", "evidence": ["example.com/x"], "limitations": ["仅覆盖给定来源"]},
        ],
        "recommendations": [],
        "source_limitations": ["仅使用给定来源"],
    }
    return [
        AIMessage(content="", tool_calls=[{"name": "create_task_plan", "args": {"tasks": tasks}, "id": "plan"}]),
        AIMessage(content="", tool_calls=[{"name": "start_task", "args": {"task_id": "scope", "current_action": "核对来源覆盖"}, "id": "start"}]),
        # The consequential, alignment-routed action: a remote fetch. It must be
        # ALLOWED under bounded autonomy, not interrupted.
        AIMessage(content="", tool_calls=[{"name": "fetch_url", "args": {"url": "https://example.com/x"}, "id": "fetch"}]),
        AIMessage(content="", tool_calls=[{"name": "complete_task", "args": {"task_id": "scope", "summary": "确定比较范围", "next_task_id": "compare", "current_action": "比较证据"}, "id": "d1"}]),
        AIMessage(content="", tool_calls=[{"name": "complete_task", "args": {"task_id": "compare", "summary": "形成比较结论", "next_task_id": "answer", "current_action": "形成最终答案"}, "id": "d2"}]),
        AIMessage(content="", tool_calls=[{"name": "complete_task", "args": {"task_id": "answer", "summary": "回答研究问题"}, "id": "d3"}]),
        AIMessage(content="", tool_calls=[{"name": "submit_output", "args": submit, "id": "submit"}]),
    ]


def test_research_assistant_operational_happy_path(tmp_path: Path, monkeypatch) -> None:
    run = _load_run_module()
    monkeypatch.setattr(run.urllib.request, "urlopen", _fake_urlopen_factory(run))

    session = run.build_session(
        chat_model=_ScriptModel(script=_happy_path_script(run)),
        memory_root=tmp_path / "mem",
        workspace_root=tmp_path / "ws",
    )
    response = session.run_task(
        agent="research-assistant",
        input={"research_question": "比较 X 和 Y", "source_urls": ["https://example.com/x"]},
        thread_id="n91",
    )
    assert response["status"] == "completed"

    events = list(session.get_trace("n91"))

    # Intent opened operational, in explore, under bounded autonomy.
    init = _one_payload(events, "intent_initialized")
    assert init["stage"] == "explore"
    clarity = _one_payload(events, "intent_clarity_estimated")
    assert clarity["level"] == "operational"
    scope = _one_payload(events, "autonomy_scope_derived")
    assert scope["mode"] == "bounded"

    # The fetch was allowed (not interrupted) — proven from alignment lineage.
    decisions = _events_by_type(events, "alignment_decision")
    fetch_decisions = [
        e["payload"] for e in _events_by_type(events, "action_proposed")
        if e["payload"]["tool_name"] == "fetch_url"
    ]
    assert fetch_decisions, "fetch_url never produced an action_proposed event"
    assert any(d["payload"]["decision"] == "allow" for d in decisions)

    # The final output is traceable back to the intent version and stage.
    submitted = _one_payload(events, "output_submitted")
    assert submitted["intent_version"] >= 1
    assert submitted["stage_id"]


# ---------------------------------------------------------------------------
# N9.2 — thin intent path
# ---------------------------------------------------------------------------


def test_research_assistant_thin_intent_starts_with_guided_autonomy(tmp_path: Path) -> None:
    run = _load_run_module()
    # A bare run with no research question and no source URLs: the intent is
    # genuinely thin (no goal, no materials), so cold-start clarity is ``thin``
    # and autonomy is ``guided`` at the ``clarify`` stage. The split Brain
    # package has a narrow fast rule for missing source_urls, so the run pauses
    # before slow model planning or action execution.
    script: list[AIMessage] = []
    session = run.build_session(
        chat_model=_ScriptModel(script=script),
        memory_root=tmp_path / "mem",
        workspace_root=tmp_path / "ws",
    )
    response = session.run_task(
        agent="research-assistant",
        input={},
        thread_id="n92",
    )

    # The run did not fail for missing intent; it paused to gather it.
    assert response["status"] != "failed"
    assert response["status"] == "interrupted"
    assert response["pending_interaction"] is not None

    events = list(session.get_trace("n92"))
    init = _one_payload(events, "intent_initialized")
    assert init["stage"] == "clarify"
    clarity = _one_payload(events, "intent_clarity_estimated")
    assert clarity["level"] == "thin"
    scope = _one_payload(events, "autonomy_scope_derived")
    assert scope["mode"] == "guided"
    # The runtime surfaced the package fast-rule clarification.
    asked = _one_payload(events, "interaction_requested")
    assert asked["payload"]["field"] == "clarification"
    step = _one_payload(events, "step_planned")
    assert step["reasoning_mode"] == "fast"
    assert step["rule_ref"] == "fast.missing_input.clarify.v1"


# ---------------------------------------------------------------------------
# N9.3 — insufficient-evidence redirect (deliver-gate -> PendingJudgment)
# ---------------------------------------------------------------------------


def test_research_assistant_insufficient_evidence_requests_judgment(tmp_path: Path) -> None:
    run = _load_run_module()
    # Operational start (question + source), but the agent tries to move to the
    # deliver stage before the success bar / coverage exists. Under bounded
    # autonomy that transition is alignment-relevant and pauses for judgment.
    # After the human redirects (editing the intent with a coverage criterion),
    # the agent replans and finishes properly through the task protocol.
    tasks = [
        {"id": "scope", "title": "界定可比较范围"},
        {"id": "compare", "title": "形成证据支持的比较结论"},
        {"id": "answer", "title": "回答研究问题并标注限制"},
    ]
    submit = {
        "research_question": "比较 X 和 Y",
        "executive_summary": "X 更适合并行训练。",
        "task_results": [
            {"task": "界定可比较范围", "result": "确定比较范围。", "evidence": ["example.com/x"], "limitations": []},
            {"task": "形成证据支持的比较结论", "result": "X 并行更强。", "evidence": ["example.com/x"], "limitations": ["缺少外部基准"]},
            {"task": "回答研究问题并标注限制", "result": "已形成有边界答案。", "evidence": ["example.com/x"], "limitations": ["仅覆盖给定来源"]},
        ],
        "recommendations": [],
        "source_limitations": ["仅使用给定来源"],
    }
    script = [
        AIMessage(content="", tool_calls=[{"name": "transition_stage", "args": {"to": "deliver", "rationale": "想直接交付"}, "id": "go-deliver"}]),
        # After the human redirects, replan and finish through the task protocol.
        AIMessage(content="", tool_calls=[{"name": "create_task_plan", "args": {"tasks": tasks}, "id": "plan"}]),
        AIMessage(content="", tool_calls=[{"name": "start_task", "args": {"task_id": "scope", "current_action": "核对来源覆盖"}, "id": "start"}]),
        AIMessage(content="", tool_calls=[{"name": "complete_task", "args": {"task_id": "scope", "summary": "确定范围", "next_task_id": "compare", "current_action": "比较证据"}, "id": "d1"}]),
        AIMessage(content="", tool_calls=[{"name": "complete_task", "args": {"task_id": "compare", "summary": "形成结论", "next_task_id": "answer", "current_action": "形成答案"}, "id": "d2"}]),
        AIMessage(content="", tool_calls=[{"name": "complete_task", "args": {"task_id": "answer", "summary": "回答问题"}, "id": "d3"}]),
        AIMessage(content="", tool_calls=[{"name": "submit_output", "args": submit, "id": "submit"}]),
    ]
    session = run.build_session(
        chat_model=_ScriptModel(script=script),
        memory_root=tmp_path / "mem",
        workspace_root=tmp_path / "ws",
    )
    first = session.run_task(
        agent="research-assistant",
        input={"research_question": "比较 X 和 Y", "source_urls": ["https://example.com/x"]},
        thread_id="n93",
    )
    assert first["status"] == "interrupted"
    pending = first["pending_judgment"]
    assert pending is not None
    judgment_id = pending["judgment_id"]

    state_before = session.get_state("n93")
    version_before = state_before["intent_version"]  # type: ignore[index]

    # The human redirects: this is not an approval — it edits the intent (adds a
    # coverage criterion and sends the agent back to gather evidence).
    final = session.respond_to_judgment(
        thread_id="n93",
        judgment_id=judgment_id,
        kind="redirect",
        rationale="证据不足, 先补齐来源覆盖再交付",
        intent_updates={"add_success_criteria": ["每条关键结论都有来源支撑"]},
    )
    assert final["status"] == "completed"

    events = list(session.get_trace("n93"))
    types = {e["event_type"] for e in events}
    assert "judgment_requested" in types
    assert "judgment_resolved" in types
    assert "intent_updated" in types

    # The redirect recorded a new intent version (the decision is in the field).
    state_after = session.get_state("n93")
    version_after = state_after["intent_version"]  # type: ignore[index]
    assert version_after > version_before

    resolved = _one_payload(events, "judgment_resolved")
    assert resolved["kind"] == "redirect"
    assert resolved["intent_version"] == version_after

    # The new coverage criterion is recorded in the live intent field.
    assert "每条关键结论都有来源支撑" in state_after["human_intent"]["success_criteria"]  # type: ignore[index]
