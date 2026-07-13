from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiAgent, ModiHarness, ModiSession, ToolBinding
from modi_harness.types import PermissionProfile
from modi_harness.workflow import CompletionValidator, OperationAdapter, parse_workflow
from modi_harness.workflow.session import _GatewayDispatcher


class _CompleteModel(BaseChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "complete_node",
                    "args": {"result": {"answer": "ok"}},
                    "id": "complete-1",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "workflow-complete-test"


def _agent() -> ModiAgent:
    workflow = parse_workflow(
        {
            "id": "answer",
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


def _session(tmp_path: Path, checkpointer: MemorySaver) -> ModiSession:
    return ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[_agent()],
        checkpointer=checkpointer,
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
    )


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

    restored = _session(tmp_path, checkpointer)
    assert restored.get_state("thread-1")["workflow_id"] == "answer"  # type: ignore[index]
    assert restored.get_state("thread-1")["status"] == "completed"  # type: ignore[index]


def test_stream_emits_incremental_events_and_normalized_terminal(tmp_path: Path) -> None:
    events = list(
        _session(tmp_path, MemorySaver()).stream(
            agent="demo",
            input={},
            thread_id="thread-2",
        )
    )

    assert [event["event_type"] for event in events] == [
        "workflow_started",
        "node_started",
        "step_completed",
        "node_completed",
        "terminal",
    ]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
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

    assert events[0]["event_type"] == "workflow_started"
    assert events[1]["event_type"] == "node_started"
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
