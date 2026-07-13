from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiHarness, ModiSession
from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]


class _ResearchModel(BaseChatModel):
    results: ClassVar[tuple[dict[str, Any], ...]] = (
        {
            "research_question": "What changed?",
            "source_urls": ["https://example.test/release"],
            "plan": {"questions": ["What changed?"], "success": ["source-bound"]},
        },
        {
            "research_question": "What changed?",
            "sources": [{"url": "https://example.test/release"}],
            "source_records": [{"url": "https://example.test/release", "content": "x"}],
            "evidence": [
                {
                    "text": "The release uses mandatory Workflows.",
                    "source_url": "https://example.test/release",
                }
            ],
            "limitations": [],
        },
        {
            "research_question": "What changed?",
            "digest": {"status": "generated"},
            "draft": {"executive_summary": "The runtime changed."},
        },
        {
            "research_question": "What changed?",
            "executive_summary": "The runtime now requires Workflows.",
            "task_results": [
                {
                    "result": "The release uses mandatory Workflows.",
                    "evidence": ["https://example.test/release"],
                }
            ],
            "recommendations": [],
            "source_limitations": [],
        },
    )

    def __init__(self) -> None:
        super().__init__()
        self._index = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        result = self.results[self._index]
        self._index += 1
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "complete_node",
                                "args": {"result": result},
                                "id": f"complete-{self._index}",
                            }
                        ],
                    )
                )
            ]
        )

    @property
    def _llm_type(self) -> str:
        return "research-workflow-test"


def test_research_assistant_binds_source_aware_completion_validator() -> None:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    assert [item.id for item in agent.completion_validators] == [
        "validate_evidence_bundle",
        "validate_research_briefing",
    ]
    assert agent.output_contract is None
    assert agent.task_protocol.mode == "off"
    workflow = agent.workflows[0]
    assert [node.id for node in workflow.nodes] == [
        "frame_research",
        "investigate_evidence",
        "synthesize_briefing",
        "verify_briefing",
    ]
    assert {node.execution for node in workflow.nodes} == {"autonomous"}
    evidence_validator = agent.completion_validators[0]
    assert evidence_validator.validate(
        {
            "sources": [{"url": "https://example.test/release"}],
            "evidence": [
                {
                    "text": "The release uses mandatory Workflows.",
                    "source_url": "https://example.test/release",
                }
            ],
            "limitations": [],
        }
    )
    validator = agent.completion_validators[1]
    briefing = {
        "research_question": "What changed?",
        "executive_summary": "The cited release changed the runtime.",
        "task_results": [
            {
                "result": "The release uses mandatory Workflows.",
                "evidence": ["https://example.test/release"],
            }
        ],
        "recommendations": [],
        "source_limitations": [],
    }

    assert validator.validate(briefing) is True
    briefing["task_results"][0]["evidence"] = []
    assert validator.validate(briefing) is False


def test_research_assistant_runs_four_autonomous_nodes_with_trace(tmp_path: Path) -> None:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    session = ModiSession(
        ModiHarness(_ResearchModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        max_steps=50,
    )

    response = session.run_task(
        agent=agent.name,
        workflow_id="research",
        input={
            "research_question": "What changed?",
            "source_urls": ["https://example.test/release"],
        },
        thread_id="research-thread",
    )

    assert response["status"] == "completed"
    assert response["output"]["executive_summary"] == (
        "The runtime now requires Workflows."
    )
    trace = list(session.get_trace("research-thread"))
    event_types = [event["event_type"] for event in trace]
    assert event_types.count("node_started") == 4
    assert event_types.count("step_completed") == 4
    assert event_types.count("completion_accepted") == 4
    assert event_types.count("node_completed") == 4
    assert event_types[-1] == "workflow_completed"
    assert [
        event["payload"]["node_id"]
        for event in trace
        if event["event_type"] == "node_started"
    ] == [
        "frame_research",
        "investigate_evidence",
        "synthesize_briefing",
        "verify_briefing",
    ]
