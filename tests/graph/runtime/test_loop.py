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


def _task_agent_md() -> str:
    return """---
name: demo
description: task protocol agent
tools: []
skills: []
task_protocol:
  mode: required
  review: before_execution
---
Create a task plan, wait for approval, execute each task, then answer.
"""


def _interactive_task_agent_md() -> str:
    return """---
name: demo
description: interactive task protocol agent
tools: []
skills: []
interaction_protocol:
  startup: agent
task_protocol:
  mode: required
  review: before_execution
---
Ask for input, plan, wait for confirmation, then execute.
"""


def _interactive_agent_md() -> str:
    return """---
name: demo
description: interactive protocol agent
tools: []
skills: []
interaction_protocol:
  startup: agent
---
Ask for input, then answer.
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


def test_task_plan_review_interrupt_and_resume(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _task_agent_md())
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "create_task_plan",
                    "args": {"tasks": [{"id": "research", "title": "Research pricing"}]},
                    "id": "plan_1",
                }],
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "start_task",
                    "args": {"task_id": "research", "current_action": "Reading pricing"},
                    "id": "start_1",
                }],
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "complete_task",
                    "args": {"task_id": "research", "summary": "Pricing extracted"},
                    "id": "done_1",
                }],
            ),
            AIMessage(content="Research complete."),
        ],
        tool_specs=[],
    )

    first = runtime.run(RunTaskInput(agent="demo", input={}, thread_id="task-review"))

    assert first["status"] == "interrupted"
    interaction = first["pending_interaction"]
    assert interaction is not None
    assert interaction["kind"] == "plan_review"
    assert runtime.get_state("task-review")["task_plan"] is None  # type: ignore[index]

    final = runtime.resume(
        thread_id="task-review",
        payload={
            "interaction_id": interaction["interaction_id"],
            "decision": "approved",
        },
    )

    assert final["status"] == "completed"
    plan = runtime.get_state("task-review")["task_plan"]  # type: ignore[index]
    assert plan["items"][0]["status"] == "completed"


def test_user_input_then_plan_review_resume_on_same_thread(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _interactive_task_agent_md())
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{
                "name": "request_user_input",
                "args": {
                    "prompt": "Enter research URLs",
                    "input_type": "url_list",
                    "field": "source_urls",
                },
                "id": "ask-urls",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "create_task_plan",
                "args": {"tasks": [{"id": "research", "title": "Research source"}]},
                "id": "plan",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "start_task",
                "args": {"task_id": "research", "current_action": "Reading source"},
                "id": "start",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "complete_task",
                "args": {"task_id": "research", "summary": "Source researched"},
                "id": "complete",
            }]),
            AIMessage(content="done"),
        ],
        tool_specs=[],
    )

    first = runtime.run(
        RunTaskInput(
            agent="demo",
            input={"interactive_startup": True},
            thread_id="interactive-task",
        )
    )
    user_input = first["pending_interaction"]
    assert user_input is not None and user_input["kind"] == "user_input"

    planned = runtime.resume(
        thread_id="interactive-task",
        payload={
            "interaction_id": user_input["interaction_id"],
            "decision": "submitted",
            "value": ["https://example.com"],
        },
    )
    plan_review = planned["pending_interaction"]
    assert planned["status"] == "interrupted"
    assert plan_review is not None and plan_review["kind"] == "plan_review"
    planned_state = runtime.get_state("interactive-task")
    assert planned_state["human_context"]["inputs"]["source_urls"] == [  # type: ignore[index]
        "https://example.com"
    ]
    human_messages = [
        message
        for message in planned_state["messages"]  # type: ignore[index]
        if (message.get("metadata") or {}).get("kind") == "human_input"
    ]
    assert human_messages[-1]["role"] == "user"
    assert "https://example.com" in human_messages[-1]["content"]

    final = runtime.resume(
        thread_id="interactive-task",
        payload={
            "interaction_id": plan_review["interaction_id"],
            "decision": "approved",
        },
    )

    assert final["status"] == "completed"
    final_state = runtime.get_state("interactive-task")
    assert final_state["human_context"]["decisions"][-1]["decision"] == "approved"  # type: ignore[index]
    assert any(
        message["role"] == "user" and "已批准当前任务计划" in message["content"]
        for message in final_state["messages"]  # type: ignore[index]
    )


def test_user_input_resume_normalizes_numbered_choice(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _interactive_agent_md())
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{
                "name": "request_user_input",
                "args": {
                    "prompt": "请选择应用",
                    "input_type": "text",
                    "field": "task_request",
                    "default": "zhizheng-replay",
                    "choices": ["police-intake", "zhizheng", "zhizheng-replay"],
                },
                "id": "ask-app",
            }]),
            AIMessage(content="done"),
        ],
        tool_specs=[],
    )

    first = runtime.run(
        RunTaskInput(agent="demo", input={"interactive_startup": True}, thread_id="numbered-choice")
    )
    interaction = first["pending_interaction"]
    assert interaction is not None

    final = runtime.resume(
        thread_id="numbered-choice",
        payload={
            "interaction_id": interaction["interaction_id"],
            "decision": "submitted",
            "value": "3",
        },
    )

    assert final["status"] == "completed"
    state = runtime.get_state("numbered-choice")
    assert state["human_context"]["inputs"]["task_request"] == "zhizheng-replay"  # type: ignore[index]
    human_messages = [
        message
        for message in state["messages"]  # type: ignore[index]
        if (message.get("metadata") or {}).get("kind") == "human_input"
    ]
    assert "zhizheng-replay" in human_messages[-1]["content"]


def test_blocked_task_resumes_after_user_supplies_replacement_source(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _interactive_task_agent_md())
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{
                "name": "create_task_plan",
                "args": {"tasks": [{"id": "source", "title": "Research source"}]},
                "id": "plan",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "start_task",
                "args": {"task_id": "source", "current_action": "Reading primary source"},
                "id": "start",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "block_task",
                "args": {"task_id": "source", "reason": "Primary source unavailable"},
                "id": "block",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "request_user_input",
                "args": {
                    "prompt": "Provide a replacement source",
                    "input_type": "url_list",
                    "field": "source_urls",
                },
                "id": "ask-replacement",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "resume_task",
                "args": {"task_id": "source", "current_action": "Reading replacement source"},
                "id": "resume",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "complete_task",
                "args": {"task_id": "source", "summary": "Replacement source researched"},
                "id": "complete",
            }]),
            AIMessage(content="done"),
        ],
        tool_specs=[],
        max_steps=10,
    )

    planned = runtime.run(RunTaskInput(agent="demo", input={}, thread_id="blocked-resume"))
    plan_review = planned["pending_interaction"]
    assert plan_review is not None

    blocked = runtime.resume(
        thread_id="blocked-resume",
        payload={"interaction_id": plan_review["interaction_id"], "decision": "approved"},
    )
    replacement = blocked["pending_interaction"]
    assert replacement is not None and replacement["kind"] == "user_input"
    assert runtime.get_state("blocked-resume")["task_plan"]["items"][0]["status"] == "blocked"  # type: ignore[index]

    final = runtime.resume(
        thread_id="blocked-resume",
        payload={
            "interaction_id": replacement["interaction_id"],
            "decision": "submitted",
            "value": ["https://example.com/replacement"],
        },
    )

    assert final["status"] == "completed"
    assert runtime.get_state("blocked-resume")["task_plan"]["items"][0]["status"] == "completed"  # type: ignore[index]


def test_task_plan_review_can_cancel_without_executing(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _task_agent_md())
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "create_task_plan",
                    "args": {"tasks": [{"id": "one", "title": "Do work"}]},
                    "id": "plan_1",
                }],
            ),
        ],
        tool_specs=[],
    )
    first = runtime.run(RunTaskInput(agent="demo", input={}, thread_id="task-cancel"))
    interaction = first["pending_interaction"]
    assert interaction is not None

    final = runtime.resume(
        thread_id="task-cancel",
        payload={
            "interaction_id": interaction["interaction_id"],
            "decision": "cancelled",
        },
    )

    assert final["status"] == "cancelled"
    assert runtime.get_state("task-cancel")["task_plan"] is None  # type: ignore[index]


def test_task_plan_review_can_revise_then_approve(tmp_path: Path) -> None:
    agent_dir = _write_agent(tmp_path, _task_agent_md())
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{
                "name": "create_task_plan",
                "args": {"tasks": [{"id": "one", "title": "Initial task"}]},
                "id": "plan-1",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "revise_task_plan",
                "args": {"tasks": [{"id": "one", "title": "Revised task"}]},
                "id": "plan-2",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "start_task",
                "args": {"task_id": "one", "current_action": "Doing revised work"},
                "id": "start",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "complete_task",
                "args": {"task_id": "one", "summary": "Revised work done"},
                "id": "done",
            }]),
            AIMessage(content="done"),
        ],
        tool_specs=[],
    )
    first = runtime.run(RunTaskInput(agent="demo", input={}, thread_id="task-revise"))
    first_interaction = first["pending_interaction"]
    assert first_interaction is not None

    revised = runtime.resume(
        thread_id="task-revise",
        payload={
            "interaction_id": first_interaction["interaction_id"],
            "decision": "revise",
            "feedback": "Use the revised scope",
        },
    )
    second_interaction = revised["pending_interaction"]
    assert revised["status"] == "interrupted"
    assert second_interaction is not None
    assert second_interaction["interaction_id"] != first_interaction["interaction_id"]
    revised_state = runtime.get_state("task-revise")
    assert revised_state["human_context"]["feedback"][-1]["value"] == "Use the revised scope"  # type: ignore[index]
    assert any(
        message["role"] == "user" and "Use the revised scope" in message["content"]
        for message in revised_state["messages"]  # type: ignore[index]
    )

    final = runtime.resume(
        thread_id="task-revise",
        payload={
            "interaction_id": second_interaction["interaction_id"],
            "decision": "approved",
        },
    )

    assert final["status"] == "completed"
    plan = runtime.get_state("task-revise")["task_plan"]  # type: ignore[index]
    assert plan["version"] == 2
    assert plan["items"][0]["title"] == "Revised task"


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


def test_preview_mode_no_side_effects(tmp_path: Path) -> None:
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
        RunTaskInput(agent="demo", input={}, permission_mode="preview")
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
    assert response["status"] == "failed"
    assert response["error"] == {
        "code": "max_steps_exceeded",
        "message": "run exceeded the 4-step limit",
    }


def test_completed_task_plan_gets_one_final_submission_step(tmp_path: Path) -> None:
    agent_dir = _write_agent(
        tmp_path,
        _task_agent_md().replace("review: before_execution", "review: never"),
    )
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{
                "name": "create_task_plan",
                "args": {"tasks": [{"id": "one", "title": "Do work"}]},
                "id": "plan",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "start_task",
                "args": {"task_id": "one", "current_action": "Working"},
                "id": "start",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "complete_task",
                "args": {"task_id": "one", "summary": "Done"},
                "id": "complete",
            }]),
            AIMessage(content="Final answer"),
        ],
        tool_specs=[],
        max_steps=3,
    )

    response = runtime.run(RunTaskInput(agent="demo", input={}))

    assert response["status"] == "completed"
    assert (response["output"] or {}).get("value") == "Final answer"
    final_state = runtime.get_state(response["thread_id"])
    assert "finalization_started" in {
        event["event_type"] for event in final_state["pending_trace_events"]  # type: ignore[index]
    }


def test_finalization_repair_budget_is_independent_from_research_steps(tmp_path: Path) -> None:
    agent_md = """---
name: demo
description: task protocol agent
tools: []
skills: []
task_protocol:
  mode: required
  review: never
output_contract:
  schema:
    type: object
    properties:
      answer: {type: string}
    required: [answer]
---
Complete the task plan, then submit the structured answer.
"""
    agent_dir = _write_agent(tmp_path, agent_md)
    runtime = _make_runtime(
        tmp_path,
        agent_dir=agent_dir,
        skill_dir=None,
        scripted_messages=[
            AIMessage(content="", tool_calls=[{
                "name": "create_task_plan",
                "args": {"tasks": [{"id": "one", "title": "Do work"}]},
                "id": "plan",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "start_task",
                "args": {"task_id": "one", "current_action": "Working"},
                "id": "start",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "complete_task",
                "args": {"task_id": "one", "summary": "Done"},
                "id": "complete",
            }]),
            AIMessage(content="wrong shape"),
            AIMessage(content="", tool_calls=[{
                "name": "submit_output",
                "args": {"answer": "ok"},
                "id": "submit",
            }]),
        ],
        tool_specs=[],
        max_steps=3,
    )

    response = runtime.run(RunTaskInput(agent="demo", input={}))

    assert response["status"] == "completed"
    assert response["output"] == {"answer": "ok"}
    final_state = runtime.get_state(response["thread_id"])
    event_types = [
        event["event_type"] for event in final_state["pending_trace_events"]  # type: ignore[index]
    ]
    assert "output_repair_started" in event_types
    assert "max_steps_exceeded" not in event_types


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


def test_interrupted_judgment_carries_reviewed_action_hash(tmp_path: Path) -> None:
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
    first = runtime.run(RunTaskInput(agent="demo", input={}, thread_id="t-reviewed-hash"))
    assert first["status"] == "interrupted"
    assert first["pending_judgment"] is not None
    reviewed_hash = first["pending_judgment"]["reviewed_action_hash"]
    assert isinstance(reviewed_hash, str)
    assert reviewed_hash

    state = runtime.get_state("t-reviewed-hash")
    assert state is not None
    pending = state["pending_tool_calls"][0]  # type: ignore[index]
    assert pending["metadata"]["reviewed_action_hash"] == reviewed_hash


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

    events = list(runtime.read_trace(response["thread_id"]))
    context_events = [e for e in events if e["event_type"] == "context_built"]
    assert context_events
    context_payload = context_events[0]["payload"]
    assert context_payload["input_tokens"] > 0
    assert "source_tokens" in context_payload["token_breakdown"]
    assert "memory_tokens" in context_payload["token_breakdown"]
    assert "schema_tokens" in context_payload["token_breakdown"]
    assert context_payload["payload_bytes"] > 0

    model_calls = [e for e in events if e["event_type"] == "model_call"]
    assert model_calls[0]["payload"]["input_tokens"] == context_payload["input_tokens"]

    model_results = [e for e in events if e["event_type"] == "model_result"]
    assert model_results[0]["payload"]["elapsed_ms"] >= 0
    assert model_results[0]["payload"]["output_tokens"] > 0

    submitted = [e for e in events if e["event_type"] == "output_submitted"]
    assert len(submitted) == 1
    payload = submitted[0]["payload"]
    assert payload["status"] == "validated"
    assert payload["source"] == "submit_output"
    assert payload["schema_valid"] is True
    assert payload["issues"] == []
    assert payload["output_keys"] == ["answer"]
    assert payload["output_hash"]
    assert payload["schema_hash"]
    assert payload["draft_ref"].endswith("/drafts/output.json")
    assert payload["artifact_ref"].endswith("/artifacts/output.md")
