"""End-to-end HarnessGraphAdapter tests over the V0.2 LangGraph runtime.

These exercise the full chain: AgentLoader → ContextManager → ModelAdapter
(fake) → ToolGateway → Policy → OutputController → WorkspaceManager →
TraceMiddleware, all wired into a compiled LangGraph with a MemorySaver
checkpointer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness.agents import AgentLoader
from modi_harness.context import ContextManager
from modi_harness.graph import GraphDeps
from modi_harness.graph.harness_adapter import HarnessGraphAdapter, RunTaskInput
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.memory import MemoryPaths, MemoryStore
from modi_harness.models import ModelAdapter
from modi_harness.output import OutputController
from modi_harness.policy import PolicyGate
from modi_harness.skills import SkillLoader
from modi_harness.tools import ToolGateway, ToolRegistry
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
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "scripted"


def _write_agent(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agents" / "demo.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p.parent


def _basic_agent_md(*, tools: list[str], skills: list[str] = ()) -> str:
    tools_yaml = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    skills_yaml = "\n".join(f"  - {s}" for s in skills) if skills else "  []"
    return f"""---
name: demo
description: demo agent
tools:
{tools_yaml}
skills:
{skills_yaml}
---
You are a test agent. Use your tools and produce a final reply.
"""


def _make_runtime(
    tmp_path: Path,
    *,
    agent_dir: Path,
    skill_dir: Path | None,
    scripted_messages: list[AIMessage],
    tool_specs: list[tuple[dict, Any]],
    rule_packs: list[str] | None = None,
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
    policy = PolicyGate(rule_packs=rule_packs)
    tool_registry = ToolRegistry()
    for spec, handler in tool_specs:
        tool_registry.register_tool(spec, handler)
    dispatcher = HookDispatcher(
        registry=HookRegistry([]),
        project_root=str(tmp_path),
        pass_env=[],
    )
    gateway = ToolGateway(
        registry=tool_registry,
        policy=policy,
        hooks=dispatcher,
        result_inline_limit_bytes=8192,
    )
    context_manager = ContextManager(policy=policy)
    model = ScriptedChatModel(script=list(scripted_messages))
    deps = GraphDeps(
        agents=AgentLoader(project_dir=agent_dir),
        skills=SkillLoader(project_dir=skill_dir) if skill_dir else None,
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


def test_s1_governance_happy_path(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "search", "args": {"q": "modi"}, "id": "tc_1"}],
            ),
            AIMessage(content="Final answer: found three results."),
        ],
        tool_specs=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"results": [kw["q"]]},
            )
        ],
    )
    response = runtime.run(RunTaskInput(agent="demo", input={"goal": "search modi"}))
    assert response["status"] == "completed"
    assert "Final answer" in (response["output"] or {}).get("value", "")


def test_multiple_tool_calls_execute_in_one_runtime_turn(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["search"]))
    seen: list[str] = []
    model = ScriptedChatModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search", "args": {"q": "a"}, "id": "tc_1"},
                    {"name": "search", "args": {"q": "b"}, "id": "tc_2"},
                    {"name": "search", "args": {"q": "c"}, "id": "tc_3"},
                ],
            ),
            AIMessage(content="Final answer after all tools."),
        ]
    )
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=model.script,
        tool_specs=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: seen.append(kw["q"]) or {"results": [kw["q"]]},
            )
        ],
    )
    response = runtime.run(RunTaskInput(agent="demo", input={"goal": "search all"}))

    assert response["status"] == "completed"
    assert seen == ["a", "b", "c"]
    # One model call to request all tools, one model call to synthesize.
    assert runtime._deps.model._chat_model.cursor["i"] == 2  # type: ignore[union-attr]


def test_l3_tool_interrupts_for_approval(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["file_ticket"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_1"}],
            ),
        ],
        tool_specs=[
            (
                {
                    "name": "file_ticket",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"ticket_id": "T1"},
            )
        ],
    )
    response = runtime.run(RunTaskInput(agent="demo", input={}))
    assert response["status"] == "interrupted"
    assert response["pending_approval"] is not None
    assert response["pending_approval"]["decision"] == "require_approval"


def test_denied_retry_blocks_repeat(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["file_ticket"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_2"}],
            ),
            AIMessage(content="Could not file ticket; user has denied this action."),
        ],
        tool_specs=[
            (
                {
                    "name": "file_ticket",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"ticket_id": "T1"},
            )
        ],
    )
    first = runtime.run(RunTaskInput(agent="demo", input={}, thread_id="t-denied"))
    assert first["status"] == "interrupted"

    rejected = runtime.reject(
        thread_id="t-denied",
        approval_id=first["pending_approval"]["approval_id"],
        reason="user denied",
    )
    assert rejected["status"] == "completed"
    trace_events = [e["event_type"] for e in runtime.read_trace("t-denied")]
    assert "denial" in trace_events


def test_plan_mode_no_side_effects(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["write_file"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "write_file", "args": {"path": "x"}, "id": "tc_1"}],
            ),
        ],
        tool_specs=[
            (
                {
                    "name": "write_file",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                    "risk_level": "L2",
                    "side_effect": True,
                },
                lambda **kw: {"written": kw["path"]},
            )
        ],
    )
    response = runtime.run(
        RunTaskInput(agent="demo", input={}, permission_mode="plan")
    )
    assert response["status"] == "interrupted"
    assert response["pending_approval"]["decision"] == "require_review"


def test_max_steps_failure(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["search"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "search", "args": {"q": str(i)}, "id": f"tc_{i}"}],
            )
            for i in range(20)
        ],
        tool_specs=[
            (
                {
                    "name": "search",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    "risk_level": "L1",
                    "side_effect": False,
                },
                lambda **kw: {"results": []},
            )
        ],
        max_steps=4,
    )
    response = runtime.run(RunTaskInput(agent="demo", input={}))
    # Without a final reply within max_steps, the run hits the step cap and
    # the graph routes to __end__ with status still "running" (no terminal
    # validate_output / completion). Adapter surfaces this as failed if there
    # was no final output and status is not interrupted.
    assert response["status"] in ("failed", "running")


def test_failed_validation_preserves_raw_output_in_response(tmp_path: Path) -> None:
    """When a structured contract rejects past the repair budget, the
    response.output must still carry the model's last raw string so callers
    can inspect what the model said. Prior behavior dropped it, leaving
    callers with None and zero diagnostic value.
    """
    agent_md = """---
name: demo
description: demo
tools: []
skills: []
output_contract:
  schema:
    type: object
    properties:
      answer: {type: string}
    required: [answer]
  required_fields: [answer]
---
Produce the final answer.
"""
    agent_dir = _write_agent(tmp_path, agent_md)
    # Three rejected outputs (initial + 2 repair attempts) — the third pushes
    # repair_used past repair_budget=2 → status: failed.
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(content="not json at all"),
            AIMessage(content="still not parseable"),
            AIMessage(content="last attempt — also bad"),
        ],
        tool_specs=[],
        max_steps=10,
    )
    response = runtime.run(RunTaskInput(agent="demo", input={}))
    assert response["status"] == "failed"
    # Critical: output is NOT None — it's the wrapped raw string.
    assert response["output"] is not None
    assert response["output"].get("value") == "last attempt — also bad"


def test_approval_resume_executes_tool(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _basic_agent_md(tools=["file_ticket"]))
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{"name": "file_ticket", "args": {"title": "x"}, "id": "tc_1"}],
            ),
            AIMessage(content="Ticket filed. Done."),
        ],
        tool_specs=[
            (
                {
                    "name": "file_ticket",
                    "description": "",
                    "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                    "risk_level": "L3",
                    "side_effect": True,
                },
                lambda **kw: {"ticket_id": "T1"},
            )
        ],
    )
    first = runtime.run(RunTaskInput(agent="demo", input={}, thread_id="t-approve"))
    assert first["status"] == "interrupted"
    approved = runtime.approve(
        thread_id="t-approve",
        approval_id=first["pending_approval"]["approval_id"],
        decision="approved",
    )
    assert approved["status"] == "completed"
    assert "Ticket filed" in (approved["output"] or {}).get("value", "")


def test_submit_output_auto_persists_to_drafts(tmp_path: Path) -> None:
    """When the model calls submit_output, the harness MUST automatically
    write the payload to ``drafts/output.json`` regardless of whether the
    agent also called save_draft. This is the contract that lets humans
    inspect the answer file post-run without depending on agent discipline.
    """
    import json as _json

    agent_md = """---
name: demo
description: demo
tools: []
skills: []
output_contract:
  schema:
    type: object
    properties:
      answer: {type: string}
    required: [answer]
  required_fields: [answer]
---
Answer the question and submit.
"""
    agent_dir = _write_agent(tmp_path, agent_md)
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "submit_output",
                    "args": {"answer": "42"},
                    "id": "tc_submit_1",
                }],
            ),
        ],
        tool_specs=[],
        max_steps=4,
    )
    response = runtime.run(RunTaskInput(agent="demo", input={}))
    assert response["status"] == "completed"
    assert response["output"] == {"answer": "42"}

    # Drafts directory must hold the payload as JSON.
    drafts_dir = tmp_path / "ws" / response["run_id"] / "drafts"
    output_path = drafts_dir / "output.json"
    assert output_path.exists(), f"expected {output_path}"
    assert _json.loads(output_path.read_text()) == {"answer": "42"}

    # Artifacts directory must hold a default Markdown rendering.
    artifacts_dir = tmp_path / "ws" / response["run_id"] / "artifacts"
    md_path = artifacts_dir / "output.md"
    assert md_path.exists(), f"expected {md_path}"
    md = md_path.read_text()
    # Generic format: top-level key → ## section, value follows.
    assert "## answer" in md
    assert "42" in md
