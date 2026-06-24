"""End-to-end stage-transition tests (N9 / N7 completion).

N7 built the stage model, the ``stage_transition`` ActionProposal kind, and the
``assess_transition`` alignment floor — but left no agent-facing entry point, so
a stage transition could never actually flow through the runtime. N9 closes that
gap with the builtin ``transition_stage`` tool. These tests prove the seam end to
end:

- under ``delegated`` autonomy a transition is *allowed* and the run's
  ``current_stage`` actually advances (the allow path);
- under ``bounded``/``guided`` autonomy a transition into ``deliver`` without a
  success bar pauses for human judgment (the N9.3 deliver-gate — covered in the
  research-assistant slice).
"""

from __future__ import annotations

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
from modi_harness.tools.builtin import get_builtin_specs
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


def _allow_judge(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"verdict": "allow", "matched_boundary_ids": [], "drift": False, "reason": "ok"}


def _agent_md() -> str:
    return """---
name: demo
description: demo agent
tools:
  - transition_stage
skills:
  []
safety_constraints:
  - stay factual
---
You are a test agent.
"""


def _make_runtime(
    tmp_path: Path,
    *,
    scripted_messages: list[AIMessage],
    judge: Any = None,
    max_steps: int = 8,
) -> HarnessGraphAdapter:
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "demo.md").write_text(_agent_md())

    tool_registry = ToolRegistry()
    for spec, handler in get_builtin_specs():
        tool_registry.register_tool(spec, handler)

    policy = PolicyGate()
    dispatcher = HookDispatcher(
        registry=HookRegistry([]), project_root=str(tmp_path), pass_env=[]
    )
    gateway = ActionGateway(
        registry=tool_registry,
        policy=policy,
        hooks=dispatcher,
        result_inline_limit_bytes=8192,
        judge=judge if judge is not None else _allow_judge,
    )
    deps = GraphDeps(
        agents=AgentLoader(project_dir=agent_dir),
        skills=None,
        memory=MemoryStore(
            MemoryPaths(
                user=tmp_path / "mem" / "user",
                agent=tmp_path / "mem" / "agent",
                workspace=tmp_path / "mem" / "workspace",
                thread=tmp_path / "mem" / "thread",
            )
        ),
        workspace=WorkspaceManager(workspace_root=tmp_path / "ws"),
        context=ContextManager(policy=policy),
        model=ModelAdapter(chat_model=ScriptedChatModel(script=list(scripted_messages))),
        tools=gateway,
        policy=policy,
        output=OutputController(),
        hooks=dispatcher,
    )
    return HarnessGraphAdapter(
        deps=deps, checkpointer=MemorySaver(), max_steps=max_steps, repair_budget=2
    )


# A delegated-autonomy task: goal + materials + success criteria + (agent)
# boundaries → clarity ceiling ``stable`` → ``delegated`` scope, where a stage
# transition is not itself judgment-worthy, so the allow path is reachable.
_DELEGATED_INPUT = {
    "goal": "compare X and Y",
    "source_urls": ["https://example.com/x"],
    "success_criteria": ["cite every claim"],
}


def test_allowed_transition_advances_current_stage(tmp_path: Path) -> None:
    runtime = _make_runtime(
        tmp_path,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "transition_stage", "args": {"to": "plan"}, "id": "tc_1"}
                ],
            ),
            AIMessage(content="done"),
        ],
    )
    response = runtime.run(
        RunTaskInput(agent="demo", input=_DELEGATED_INPUT, thread_id="ts1")
    )
    assert response["status"] == "completed"

    state = runtime.get_state("ts1")
    assert state["human_intent"]["current_stage"]["kind"] == "plan"  # type: ignore[index]
    # The top-level lineage shortcut tracks the embedded stage.
    assert state["stage_id"] == state["human_intent"]["current_stage"]["id"]  # type: ignore[index]


def test_allowed_transition_records_action_and_lineage(tmp_path: Path) -> None:
    runtime = _make_runtime(
        tmp_path,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "transition_stage", "args": {"to": "plan"}, "id": "tc_1"}
                ],
            ),
            AIMessage(content="done"),
        ],
    )
    runtime.run(RunTaskInput(agent="demo", input=_DELEGATED_INPUT, thread_id="ts2"))
    events = list(runtime.read_trace("ts2"))
    proposed = [e for e in events if e["event_type"] == "action_proposed"]
    assert any(e["payload"]["kind"] == "stage_transition" for e in proposed)
    decisions = [e for e in events if e["event_type"] == "alignment_decision"]
    assert any(e["payload"]["decision"] == "allow" for e in decisions)


def test_transition_into_deliver_without_success_bar_pauses(tmp_path: Path) -> None:
    """The N9.3 gate at the runtime level: a ``deliver`` transition under
    ``bounded`` autonomy (no success criteria) interrupts for human judgment.

    The full alignment-decision lineage is flushed on *resume* (N8 defers the
    lineage trio + judgment_requested into the resume update), so here we assert
    only that the gate fired: the run paused and surfaced a pending judgment.
    The decision-content proof lives in the research-assistant N9.3 test, which
    resumes the run.
    """
    runtime = _make_runtime(
        tmp_path,
        scripted_messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "transition_stage", "args": {"to": "deliver"}, "id": "tc_1"}
                ],
            ),
            AIMessage(content="done"),
        ],
    )
    # No success_criteria → clarity ceiling ``operational`` → ``bounded`` scope.
    response = runtime.run(
        RunTaskInput(
            agent="demo",
            input={"goal": "compare X and Y", "source_urls": ["https://example.com/x"]},
            thread_id="ts3",
        )
    )
    assert response["status"] == "interrupted"
    assert response["pending_judgment"] is not None
    assert response["pending_judgment"]["judgment_id"]
    # The stage did NOT advance — the run is paused before entering deliver.
    state = runtime.get_state("ts3")
    assert state["human_intent"]["current_stage"]["kind"] != "deliver"  # type: ignore[index]
