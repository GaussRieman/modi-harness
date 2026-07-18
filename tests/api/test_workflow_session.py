from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiAgent, ModiHarness, ModiSession, ToolBinding
from modi_harness.api.errors import ModiSessionConfigError
from modi_harness.checkpoint import InMemoryRootCheckpointStore
from modi_harness.types import PermissionProfile
from modi_harness.workflow import (
    CompletionValidator,
    Node,
    OperationAdapter,
    TaskGraphLimits,
    TaskGraphNodeConfig,
    Workflow,
    parse_workflow,
)
from modi_harness.workflow.session import _GatewayDispatcher


class _CompleteModel(BaseChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "complete_node",
                    "args": {"answer": "ok"},
                    "id": "complete-1",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "workflow-complete-test"


class _RepairingCompleteModel(BaseChatModel):
    def __init__(self) -> None:
        super().__init__()
        self._index = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        arguments = {} if self._index == 0 else {"answer": "ok"}
        self._index += 1
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "complete_node",
                    "args": arguments,
                    "id": f"complete-{self._index}",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "workflow-completion-repair-test"


class _DualRepresentationCompleteModel(BaseChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        message = AIMessage(
            content="",
            tool_calls=[{"name": "complete_node", "args": {}, "id": "complete-dual"}],
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "complete-dual",
                        "type": "function",
                        "function": {
                            "name": "complete_node",
                            "arguments": '{"answer":"ok"}',
                        },
                    }
                ]
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "workflow-dual-tool-representation-test"


def _agent() -> ModiAgent:
    workflow = parse_workflow(
        {
            "id": "answer",
            "description": "Answer a request.",
            "input_schema": {"type": "object"},
            "start_node": "answer",
            "nodes": [
                {
                    "id": "answer",
                    "execution": "autonomous",
                    "goal": "Answer the request",
                    "completion": {
                        "output_schema": {
                            "type": "object",
                            "required": ["answer"],
                        },
                        "validator": "valid_answer",
                    },
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )
    return ModiAgent(
        name="demo",
        description="demo",
        instruction="Return a concise answer.",
        workflows=(workflow,),
        completion_validators=(
            CompletionValidator(
                id="valid_answer",
                version="1",
                validate=lambda value: bool(value.get("answer")),
            ),
        ),
    )


def _task_graph_agent() -> ModiAgent:
    workflow = Workflow(
        id="long-task",
        description="Run a long task.",
        input_schema={"type": "object"},
        start_node="execute",
        nodes=(
            Node(
                id="execute",
                execution="task_graph",
                inputs={},
                completion_output_schema={"type": "object"},
                completion_validator="goal-validator",
                completion_required=(),
                completion_review="none",
                transitions={
                    "completed": "$complete",
                    "failed": "$fail",
                    "waiting": "$wait",
                },
                task_graph=TaskGraphNodeConfig(
                    planner="planner",
                    graph_policy="graph-policy",
                    context_builder="context-builder",
                    task_validators=("task-validator",),
                    group_validators=(),
                    criterion_validators=("criterion-validator",),
                    goal_verifier="goal-verifier",
                    operation_adapters=("run",),
                    parent_inline_components=(),
                    human_task_contracts=(),
                    child_templates=(),
                    limits=TaskGraphLimits(
                        max_tasks=4,
                        max_graph_depth=2,
                        max_replans=1,
                        max_concurrency=1,
                        max_child_runs=0,
                    ),
                ),
            ),
        ),
        definition_fingerprint="task-graph-fixture",
    )
    return ModiAgent(
        name="long-task",
        description="long task",
        instruction="Execute the confirmed Intent.",
        workflows=(workflow,),
        completion_validators=(
            CompletionValidator(
                id="goal-validator",
                version="1",
                validate=lambda value: isinstance(value, dict),
            ),
        ),
    )


def _session(tmp_path: Path, checkpointer: MemorySaver) -> ModiSession:
    return ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[_agent()],
        checkpointer=checkpointer,
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )


def test_task_graph_session_requires_shared_root_store(tmp_path: Path) -> None:
    with pytest.raises(ModiSessionConfigError, match="shared root checkpoint store"):
        ModiSession(
            ModiHarness(_CompleteModel()),
            agents=[_task_graph_agent()],
            checkpointer=MemorySaver(),
            workspace_root=tmp_path / "workspace",
            memory_root=tmp_path / "memory",
        )


def test_task_graph_session_accepts_shared_root_store(tmp_path: Path) -> None:
    root_store = InMemoryRootCheckpointStore()

    session = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[_task_graph_agent()],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
    )

    assert session.list_agents() == ["long-task"]
    assert session._adapter._root_checkpoint_store is root_store


def test_autonomous_workflow_completes_and_persists(tmp_path: Path) -> None:
    checkpointer = MemorySaver()
    response = _session(tmp_path, checkpointer).run_task(
        agent="demo",
        workflow_id="answer",
        input={},
        thread_id="thread-1",
    )

    assert response["status"] == "completed"
    assert response["output"] == {"answer": "ok"}
    assert [event["event_type"] for event in _session(tmp_path, checkpointer).get_trace(
        "thread-1"
    )] == [
        "workflow_selected",
        "workflow_started",
        "node_started",
        "step_completed",
        "completion_accepted",
        "node_completed",
        "workflow_completed",
    ]

    restored = _session(tmp_path, checkpointer)
    assert restored.get_state("thread-1")["workflow_id"] == "answer"  # type: ignore[index]
    assert restored.get_state("thread-1")["status"] == "completed"  # type: ignore[index]


def test_autonomous_workflow_repairs_complete_node_without_result(tmp_path: Path) -> None:
    model = _RepairingCompleteModel()
    session = ModiSession(
        ModiHarness(model),
        agents=[_agent()],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )

    response = session.run_task(
        agent="demo",
        workflow_id="answer",
        input={},
        thread_id="repair-completion",
    )

    assert response["status"] == "completed"
    assert response["output"] == {"answer": "ok"}
    assert model._index == 2
    assert [event["event_type"] for event in session.get_trace("repair-completion")] == [
        "workflow_selected",
        "workflow_started",
        "node_started",
        "step_completed",
        "completion_rejected",
        "step_completed",
        "completion_accepted",
        "node_completed",
        "workflow_completed",
    ]


def test_autonomous_workflow_uses_non_empty_duplicate_tool_arguments(tmp_path: Path) -> None:
    session = ModiSession(
        ModiHarness(_DualRepresentationCompleteModel()),
        agents=[_agent()],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )

    response = session.run_task(
        agent="demo",
        workflow_id="answer",
        input={},
        thread_id="dual-tool-representation",
    )

    assert response["status"] == "completed"
    assert response["output"] == {"answer": "ok"}
    event_types = [
        event["event_type"] for event in session.get_trace("dual-tool-representation")
    ]
    assert event_types == [
        "workflow_selected",
        "workflow_started",
        "node_started",
        "step_completed",
        "completion_accepted",
        "node_completed",
        "workflow_completed",
    ]


def test_stream_emits_incremental_events_and_normalized_terminal(tmp_path: Path) -> None:
    events = list(
        _session(tmp_path, MemorySaver()).stream(
            agent="demo",
            input={},
            thread_id="thread-2",
        )
    )

    assert [event["event_type"] for event in events] == [
        "workflow_selected",
        "workflow_started",
        "node_started",
        "step_completed",
        "completion_accepted",
        "node_completed",
        "terminal",
    ]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5, 6, 7]
    assert events[-1]["terminal_response"]["status"] == "completed"


@pytest.mark.asyncio
async def test_astream_emits_execution_events_before_terminal(tmp_path: Path) -> None:
    events = [
        event
        async for event in _session(tmp_path, MemorySaver()).astream(
            agent="demo",
            input={},
            thread_id="thread-async",
        )
    ]

    assert events[0]["event_type"] == "workflow_selected"
    assert events[1]["event_type"] == "workflow_started"
    assert events[2]["event_type"] == "node_started"
    assert events[-1]["event_type"] == "terminal"
    assert events[-1]["terminal_response"]["status"] == "completed"


def test_gateway_dispatcher_applies_adapter_recovery_retry_ceiling() -> None:
    captured: list[int | None] = []

    class _Registry:
        @staticmethod
        def get(_name):
            return {"retry": {"max_attempts": 4}}

    class _Gateway:
        registry = _Registry()

        @staticmethod
        def execute_tool_call(_proposal, **kwargs):
            captured.append(kwargs["max_attempts"])
            return SimpleNamespace(
                outcome="error",
                record={},
                error_message="failed",
            )

    dispatcher = _GatewayDispatcher(
        gateway=_Gateway(),  # type: ignore[arg-type]
        profile={"name": "demo", "default_tools": ["write"]},  # type: ignore[arg-type]
        permission_mode="trust",
        run_id="run-1",
        thread_id="thread-1",
        deps=None,
    )
    base = {
        "id": "write",
        "version": "1",
        "kind": "tool",
        "target": "write",
        "node_selectable": True,
        "required_capabilities": (),
        "side_effect": True,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }

    dispatcher.dispatch(
        OperationAdapter(**base, recovery_mode="manual_reconciliation"),  # type: ignore[arg-type]
        {},
    )
    dispatcher.dispatch(
        OperationAdapter(**base, recovery_mode="provider_idempotent"),  # type: ignore[arg-type]
        {},
    )

    assert captured == [1, 4]


def test_checkpoint_resume_executes_exact_pending_operation(tmp_path: Path) -> None:
    calls: list[str] = []

    def reviewed_tool(question: str) -> dict[str, str]:
        calls.append(question)
        return {"answer": "approved"}

    workflow = parse_workflow(
        {
            "id": "reviewed",
            "description": "Run reviewed work.",
            "input_schema": {
                "type": "object",
                "required": ["question"],
                "properties": {"question": {"type": "string"}},
            },
            "start_node": "reviewed_call",
            "nodes": [
                {
                    "id": "reviewed_call",
                    "execution": "operation",
                    "operation": "reviewed_tool",
                    "inputs": {
                        "question": {"$ref": "#/workflow/input/question"},
                    },
                    "completion": {
                        "output_schema": {
                            "type": "object",
                            "required": ["answer"],
                        }
                    },
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )
    agent = ModiAgent(
        name="reviewed-agent",
        description="reviewed",
        instruction="unused",
        workflows=(workflow,),
        tools=(
            ToolBinding(
                spec={
                    "name": "reviewed_tool",
                    "description": "reviewed",
                    "input_schema": {
                        "type": "object",
                        "required": ["question"],
                    },
                    "risk_level": "L1",
                    "side_effect": True,
                },
                handler=reviewed_tool,
            ),
        ),
        permission_profile=PermissionProfile(
            mode="auto",
            preauthorized=[],
            deny=[],
            review_required=["reviewed_tool"],
        ),
    )
    checkpointer = MemorySaver()

    first = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=checkpointer,
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )
    interrupted = first.run_task(
        agent=agent.name,
        input={"question": "same proposal"},
        thread_id="review-thread",
    )

    assert interrupted["status"] == "interrupted"
    assert interrupted["pending_judgment"] is not None
    assert calls == []
    judgment_id = interrupted["pending_judgment"]["judgment_id"]
    human_request = first.get_current_human_request("review-thread")
    assert human_request == {
        "kind": "judgment",
        "source": "operation_node",
        "request_id": judgment_id,
        "node_id": "reviewed_call",
        "node_attempt": 1,
        "prompt": "human response required",
    }

    restored = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=checkpointer,
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )
    completed = restored.respond_to_judgment(
        thread_id="review-thread",
        judgment_id=judgment_id,
        kind="approve",
    )

    assert completed["status"] == "completed"
    assert completed["output"] == {"answer": "approved"}
    assert calls == ["same proposal"]
    assert restored.get_current_human_request("review-thread") is None
    trace = list(restored.get_trace("review-thread"))
    event_types = [event["event_type"] for event in trace]
    assert "operation_started" in event_types
    assert "operation_completed" in event_types
    assert "approval_request" in event_types
    assert "interaction_resolved" in event_types
    operation = next(
        event for event in trace if event["event_type"] == "operation_completed"
    )
    assert operation["payload"]["adapter_id"] == "reviewed_tool"
    assert operation["payload"]["invocation_id"]


def test_checkpoint_resume_approves_reviewed_node_result(tmp_path: Path) -> None:
    workflow = parse_workflow(
        {
            "id": "review_answer",
            "description": "Review an answer before completion.",
            "input_schema": {"type": "object"},
            "start_node": "answer",
            "nodes": [
                {
                    "id": "answer",
                    "execution": "autonomous",
                    "goal": "Answer and wait for review",
                    "completion": {
                        "review": "required",
                        "output_schema": {
                            "type": "object",
                            "required": ["answer"],
                        },
                    },
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )
    agent = ModiAgent(
        name="review-answer",
        description="review answer",
        instruction="Return an answer.",
        workflows=(workflow,),
    )
    checkpointer = MemorySaver()
    first = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=checkpointer,
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )

    waiting = first.run_task(
        agent=agent.name,
        input={},
        thread_id="node-review-thread",
    )

    assert waiting["status"] == "interrupted"
    interaction = waiting["pending_interaction"]
    assert interaction["kind"] == "node_review"
    assert interaction["payload"]["draft"] == {"answer": "ok"}

    restored = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=checkpointer,
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )
    completed = restored.respond_to_interaction(
        thread_id="node-review-thread",
        interaction_id=interaction["interaction_id"],
        decision="approved",
    )

    assert completed["status"] == "completed"
    assert completed["output"] == {"answer": "ok"}
    event_types = [item["event_type"] for item in restored.get_trace("node-review-thread")]
    assert "interaction_requested" in event_types
    assert "interaction_resolved" in event_types
