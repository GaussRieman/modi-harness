from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiHarness, ModiSession
from modi_harness.discovery import discover_agents
from modi_harness.workflow import WorkflowInstanceError, validate_instance

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


class _ClarifyingResearchModel(BaseChatModel):
    def __init__(self) -> None:
        super().__init__()
        self._index = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        if self._index == 0:
            name = "request_user_input"
            args: dict[str, Any] = {
                "prompt": "请提供明确的研究问题和至少一个来源 URL。",
                "field": "research_request",
                "input_type": "multiline",
                "required": True,
            }
        else:
            name = "complete_node"
            args = {"result": _ResearchModel.results[self._index - 1]}
        self._index += 1
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": name,
                                "args": args,
                                "id": f"clarify-{self._index}",
                            }
                        ],
                    )
                )
            ]
        )

    @property
    def _llm_type(self) -> str:
        return "research-clarification-test"


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
    assert workflow.node("frame_research").completion_required == ("research_question",)
    frame_schema = workflow.node("frame_research").completion_output_schema
    assert frame_schema is not None
    validate_instance(
        frame_schema,
        {"research_question": "What changed?", "source_urls": [], "plan": {}},
    )
    with pytest.raises(WorkflowInstanceError, match="does not match"):
        validate_instance(
            frame_schema,
            {"research_question": "What changed?", "source_urls": ["没有"], "plan": {}},
        )
    assert workflow.node("investigate_evidence").capability_tools == (
        "web_search",
        "fetch_url",
        "source_extract",
    )
    assert {binding.spec["name"] for binding in agent.tools} == {
        "fetch_url",
        "generate_research_digest",
        "judge_research_digest",
        "source_extract",
        "web_search",
    }
    assert "不要复述或要求确认研究计划" in agent.instruction
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
    assert not evidence_validator.validate(
        {
            "sources": [{"url": "没有"}],
            "evidence": [{"text": "unsupported", "source_url": "没有"}],
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


def test_research_assistant_clarifies_vague_input_then_resumes(tmp_path: Path) -> None:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    workflow = agent.workflows[0]
    frame = workflow.node("frame_research")
    assert frame.completion_required == ("research_question",)
    session = ModiSession(
        ModiHarness(_ClarifyingResearchModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        max_steps=50,
    )

    waiting = session.run_task(
        agent=agent.name,
        workflow_id="research",
        input={"prompt": "hi"},
        thread_id="clarification-thread",
    )

    assert waiting["status"] == "interrupted"
    interaction = waiting["pending_interaction"]
    assert interaction is not None
    assert interaction["payload"]["field"] == "research_request"
    assert interaction["payload"]["input_type"] == "multiline"
    before_resume = [
        event["event_type"] for event in session.get_trace("clarification-thread")
    ]
    assert before_resume.count("step_completed") == 1
    assert "interaction_requested" in before_resume
    assert "completion_rejected" not in before_resume

    completed = session.respond_to_interaction(
        thread_id="clarification-thread",
        interaction_id=interaction["interaction_id"],
        decision="approve",
        value=(
            "研究问题: What changed?\n"
            "来源: https://example.test/release"
        ),
    )

    assert completed["status"] == "completed"
    trace_types = [
        event["event_type"] for event in session.get_trace("clarification-thread")
    ]
    assert trace_types.count("interaction_requested") == 1
    assert trace_types.count("interaction_resolved") == 1
    assert trace_types.count("completion_rejected") == 0
    assert trace_types[-1] == "workflow_completed"
