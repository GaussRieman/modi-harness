from __future__ import annotations

from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiAgent, ModiHarness, ModiSession
from modi_harness.workflow import parse_workflow


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


def test_stream_uses_normalized_terminal_event(tmp_path: Path) -> None:
    events = list(
        _session(tmp_path, MemorySaver()).stream(
            agent="demo",
            input={},
            thread_id="thread-2",
        )
    )

    assert [event["event_type"] for event in events] == ["terminal"]
    assert events[0]["terminal_response"]["status"] == "completed"
