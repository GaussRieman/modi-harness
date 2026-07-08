"""End-to-end lineage/observability tests over the intent-aligned runtime (N8).

These wire ``ActionGateway`` (alignment-first) into the compiled graph so a
consequential action flows through alignment + governance, and assert the trace
proves alignment — not just execution. The acceptance bar (plan N8 exit gate):
a maintainer can answer "which intent version and stage produced this action,
and what decided it?" from trace alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness.actions import ActionGateway
from modi_harness.agents import AgentLoader
from modi_harness.context import ContextManager
from modi_harness.graph import GraphDeps
from modi_harness.graph.harness_adapter import HarnessGraphAdapter, RunTaskInput
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.memory import MemoryPaths, MemoryStore
from modi_harness.models import ModelAdapter
from modi_harness.output import OutputController
from modi_harness.policy import PolicyGate
from modi_harness.tools import ToolRegistry
from modi_harness.trace.lineage import (
    group_by_intent_version,
    lineage_for_action,
    read_lineage,
)
from modi_harness.workspace import WorkspaceManager


class ScriptedChatModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        if i >= len(self.script):
            raise RuntimeError(f"ScriptedChatModel exhausted after {i} calls")
        msg = self.script[i]
        self.cursor["i"] = i + 1
        msg = _as_step_decision_message(msg)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "scripted"


def _as_step_decision_message(msg: AIMessage) -> AIMessage:
    calls = list(getattr(msg, "tool_calls", None) or [])
    if calls and calls[0].get("name") == "submit_step_decision":
        return msg
    if calls:
        call = calls[0]
        tool_name = call["name"]
        args = dict(call.get("args") or {})
        operation = {
            "kind": "tool",
            "summary": f"call {tool_name}",
            "target": tool_name,
            "arguments": args,
            "expected_outcome": f"{tool_name} completes",
        }
        if tool_name == "submit_output":
            operation = {
                "kind": "output_finalize",
                "summary": "finalize output",
                "target": "validate_output",
                "arguments": {"draft": args},
                "expected_outcome": "output is validated",
            }
        return _step_message(
            {
                "step_kind": "act",
                "reason": f"structured slow Brain selected {tool_name}",
                "intent_patch": None,
                "ask": None,
                "operation": operation,
                "expected_state_change": None,
                "postcheck": None,
                "continuation": "continue",
                "human_judgment": {
                    "required": False,
                    "reason": "operation is inside the current autonomy scope",
                    "trigger": "none",
                },
                "continuation_basis": {
                    "source": "slow_plan",
                    "reference": tool_name,
                    "reason": f"continue after {tool_name}",
                },
            },
            call_id=f"brain_{call.get('id') or tool_name}",
        )
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    return _step_message(
        {
            "step_kind": "verify",
            "reason": "structured slow Brain finalized the answer",
            "intent_patch": None,
            "ask": None,
            "operation": {
                "kind": "output_finalize",
                "summary": "finalize output",
                "target": "validate_output",
                "arguments": {"draft": text},
                "expected_outcome": "output is validated",
            },
            "expected_state_change": {"pending_draft": True},
            "postcheck": None,
            "continuation": "continue",
            "human_judgment": {
                "required": False,
                "reason": "final output follows the current intent",
                "trigger": "none",
            },
            "continuation_basis": {
                "source": "slow_plan",
                "reference": "output_finalize",
                "reason": "continue into output validation",
            },
        }
    )


def _step_message(args: dict[str, Any], *, call_id: str = "brain_step") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "submit_step_decision", "args": args, "id": call_id}],
    )


def _write_agent(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agents" / "demo.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p.parent


def _agent_md(*, tools: list[str]) -> str:
    tools_yaml = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    return f"""---
name: demo
description: demo agent
tools:
{tools_yaml}
skills:
  []
---
You are a test agent. Use your tools and produce a final reply.
"""


def _allow_judge(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"verdict": "allow", "matched_boundary_ids": [], "drift": False, "reason": "ok"}


def _make_runtime(
    tmp_path: Path,
    *,
    agent_dir: Path,
    scripted_messages: list[AIMessage],
    tool_specs: list[tuple[dict, Any]],
    judge: Any = None,
    max_steps: int = 8,
) -> HarnessGraphAdapter:
    workspace = WorkspaceManager(workspace_root=tmp_path / "ws")
    memory = MemoryStore(
        MemoryPaths(
            user=tmp_path / "mem" / "user",
            agent=tmp_path / "mem" / "agent",
            workspace=tmp_path / "mem" / "workspace",
            thread=tmp_path / "mem" / "thread",
        )
    )
    policy = PolicyGate()
    tool_registry = ToolRegistry()
    for spec, handler in tool_specs:
        tool_registry.register_tool(spec, handler)
    dispatcher = HookDispatcher(
        registry=HookRegistry([]),
        project_root=str(tmp_path),
        pass_env=[],
    )
    gateway = ActionGateway(
        registry=tool_registry,
        policy=policy,
        hooks=dispatcher,
        result_inline_limit_bytes=8192,
        judge=judge if judge is not None else _allow_judge,
    )
    context_manager = ContextManager(policy=policy)
    model = ScriptedChatModel(script=list(scripted_messages))
    deps = GraphDeps(
        agents=AgentLoader(project_dir=agent_dir),
        skills=None,
        memory=memory,
        workspace=workspace,
        context=context_manager,
        model=ModelAdapter(chat_model=model),
        tools=gateway,
        policy=policy,
        output=OutputController(),
        hooks=dispatcher,
    )
    return HarnessGraphAdapter(
        deps=deps,
        checkpointer=MemorySaver(),
        max_steps=max_steps,
        repair_budget=2,
    )


def _search_spec(risk: str = "L1", *, side_effect: bool = False) -> dict:
    return {
        "name": "search",
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        "risk_level": risk,
        "side_effect": side_effect,
    }


def test_consequential_action_has_full_lineage(tmp_path: Path) -> None:
    """A tool call routed through alignment emits action_proposed +
    alignment_decision + intent_lineage_recorded, and the lineage carries the
    intent version and stage that authorized it."""
    agent_dir = _write_agent(tmp_path, _agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "modi"}, "id": "tc_1"}]),
            AIMessage(content="Final answer."),
        ],
        tool_specs=[(_search_spec(), lambda **kw: {"results": [kw["q"]]})],
    )
    response = runtime.run(RunTaskInput(agent="demo", input={"goal": "search modi"}, thread_id="t1"))
    assert response["status"] == "completed"

    events = list(runtime.read_trace("t1"))
    types = [e["event_type"] for e in events]
    assert "action_proposed" in types
    assert "alignment_decision" in types
    assert "intent_lineage_recorded" in types

    lineages = list(read_lineage(events))
    assert len(lineages) == 1
    lin = lineages[0]
    assert lin["action_id"]
    assert lin["alignment_decision_id"]
    assert lin["intent_version"] >= 1
    assert lin["stage_id"]
    assert lin["judgment_id"] is None

    # alignment_decision event proves whether the model judged it.
    decision_events = [e for e in events if e["event_type"] == "alignment_decision"]
    assert decision_events[0]["payload"]["decision"] == "allow"
    assert decision_events[0]["payload"]["model_judged"] is True

    tool_results = [e for e in events if e["event_type"] == "tool_result"]
    assert tool_results[0]["payload"]["step_id"].startswith("tool-0001-runtime-op-loop-")
    assert tool_results[0]["payload"]["step_type"] == "tool"
    assert tool_results[0]["payload"]["parent_step_id"].startswith("loop-")


def test_action_proposed_carries_intent_version_and_stage(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "tc_1"}]),
            AIMessage(content="done"),
        ],
        tool_specs=[(_search_spec(), lambda **kw: {"results": []})],
    )
    runtime.run(RunTaskInput(agent="demo", input={"goal": "g"}, thread_id="t2"))
    events = list(runtime.read_trace("t2"))
    proposed = [e for e in events if e["event_type"] == "action_proposed"]
    assert proposed
    payload = proposed[0]["payload"]
    assert payload["tool_name"] == "search"
    assert payload["kind"]
    assert payload["intent_version"] >= 1
    assert payload["stage_id"]


def test_judgment_updates_produce_new_intent_version(tmp_path: Path) -> None:
    """An L3 side-effecting action interrupts for judgment; a redirecting
    judgment bumps the intent version and emits judgment_requested +
    judgment_resolved + intent_updated."""
    agent_dir = _write_agent(tmp_path, _agent_md(tools=["file_ticket"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_1"}]),
            AIMessage(content="Understood, redirected."),
        ],
        tool_specs=[
            (
                {
                    "name": "file_ticket",
                    "description": "",
                    "input_schema": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"],
                    },
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"ticket_id": "T1"},
            )
        ],
    )
    first = runtime.run(RunTaskInput(agent="demo", input={"goal": "file"}, thread_id="t3"))
    assert first["status"] == "interrupted"
    version_before = runtime.get_state("t3")["intent_version"]  # type: ignore[index]

    approval_id = first["pending_approval"]["approval_id"]
    final = runtime.resume(
        thread_id="t3",
        payload={"approval_id": approval_id, "kind": "redirect", "rationale": "do this instead"},
    )
    assert final["status"] == "completed"

    events = list(runtime.read_trace("t3"))
    types = [e["event_type"] for e in events]
    assert "judgment_requested" in types
    assert "judgment_resolved" in types
    assert "intent_updated" in types

    version_after = runtime.get_state("t3")["intent_version"]  # type: ignore[index]
    assert version_after > version_before

    resolved = [e for e in events if e["event_type"] == "judgment_resolved"]
    requested = [e for e in events if e["event_type"] == "judgment_requested"]
    assert requested
    assert resolved[0]["payload"]["kind"] == "redirect"
    assert resolved[0]["payload"]["intent_version"] == version_after
    assert resolved[0]["payload"]["target_action_id"] == requested[0]["payload"]["target_action_id"]
    assert resolved[0]["payload"]["target_action_id"] != "tc_1"


def test_final_output_traceable_to_intent_version_and_stage(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        scripted_messages=[AIMessage(content="The final answer.")],
        tool_specs=[(_search_spec(), lambda **kw: {"results": []})],
    )
    response = runtime.run(RunTaskInput(agent="demo", input={"goal": "g"}, thread_id="t4"))
    assert response["status"] == "completed"
    events = list(runtime.read_trace("t4"))
    submitted = [e for e in events if e["event_type"] == "output_submitted"]
    assert submitted
    payload = submitted[0]["payload"]
    assert payload["intent_version"] >= 1
    assert payload["stage_id"]


def test_group_by_intent_version_over_recorded_trace(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "a"}, "id": "tc_1"}]),
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "b"}, "id": "tc_2"}]),
            AIMessage(content="done"),
        ],
        tool_specs=[(_search_spec(), lambda **kw: {"results": [kw["q"]]})],
    )
    runtime.run(RunTaskInput(agent="demo", input={"goal": "g"}, thread_id="t5"))
    events = list(runtime.read_trace("t5"))
    lineages = list(read_lineage(events))
    assert len(lineages) == 2
    grouped = group_by_intent_version(lineages)
    # Both actions ran under the same (initial) intent version.
    assert len(grouped) == 1
    only_version = next(iter(grouped))
    assert len(grouped[only_version]) == 2
    # Each action's lineage is individually findable.
    for lin in lineages:
        assert lineage_for_action(lineages, lin["action_id"]) == lin


def test_lineage_events_do_not_leak_tool_arguments(tmp_path: Path) -> None:
    """Lineage instrumentation must not become a new secret-leak path: the
    action_proposed / alignment_decision / intent_lineage_recorded events carry
    only join keys (ids, version, stage, decision), never raw tool arguments.
    A sensitive value in a tool argument must never reach the trace at all.
    """
    agent_dir = _write_agent(tmp_path, _agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "search", "args": {"q": "x", "api_key": "sk-secret"}, "id": "tc_1"}],
            ),
            AIMessage(content="done"),
        ],
        tool_specs=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}, "api_key": {"type": "string"}},
                        "required": ["q"],
                    },
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"results": []},
            )
        ],
    )
    response = runtime.run(RunTaskInput(agent="demo", input={"goal": "g"}, thread_id="t6"))
    run_id = response["run_id"]
    raw = (tmp_path / "ws" / run_id / "logs" / "trace.jsonl").read_text()
    # The secret never enters the trace — lineage carries no raw arguments.
    assert "sk-secret" not in raw

    events = list(runtime.read_trace("t6"))
    for et in ("action_proposed", "alignment_decision", "intent_lineage_recorded"):
        for ev in (e for e in events if e["event_type"] == et):
            assert "arguments" not in ev["payload"]
            assert "api_key" not in json.dumps(ev["payload"])
