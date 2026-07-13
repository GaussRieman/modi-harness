from __future__ import annotations

import urllib.parse
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiHarness, ModiSession, ToolBinding
from modi_harness.discovery import discover_agents
from modi_harness.workflow import WorkflowInstanceError, validate_instance

REPO_ROOT = Path(__file__).resolve().parents[2]


def _search_record(
    provider: str,
    query: str,
    *,
    status: str = "empty",
) -> dict[str, Any]:
    if provider == "bing_rss":
        url = "https://www.bing.com/search?" + urllib.parse.urlencode(
            {"q": query, "format": "rss"}
        )
    elif provider == "baidu":
        url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
    else:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(
            {"q": query}
        )
    return {
        "provider": provider,
        "query": query,
        "search_url": url,
        "status": status,
        "results": [],
        "error": "provider unavailable" if status in {"blocked", "failed"} else None,
    }


_QUERY = "Modi Harness runtime change"
_SEARCH_RECORDS = [_search_record("bing_rss", _QUERY), _search_record("baidu", _QUERY)]
_FINAL_RESULT = {
    "research_question": "What changed?",
    "executive_summary": "The runtime now requires explicit Workflows.",
    "task_results": [
        {
            "task": "Identify the change",
            "result": "The release uses mandatory Workflows.",
            "evidence": ["https://example.test/release"],
            "limitations": [],
        }
    ],
    "recommendations": [],
    "source_limitations": [],
    "sources": ["https://example.test/release"],
    "search_records": _SEARCH_RECORDS,
}


class _ResearchModel(BaseChatModel):
    clarify_first: bool = False

    def __init__(self, *, clarify_first: bool = False) -> None:
        super().__init__()
        self.clarify_first = clarify_first
        self._index = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        if self.clarify_first and self._index == 0:
            name = "request_user_input"
            args: dict[str, Any] = {
                "prompt": "请提供要研究的主体或问题。",
                "field": "research_request",
                "input_type": "text",
                "required": True,
            }
        elif self._index == int(self.clarify_first):
            name = "public_web_research"
            args = {"subject": "Modi Harness", "question": "What changed?"}
        else:
            name = "complete_node"
            args = _FINAL_RESULT
        self._index += 1
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {"name": name, "args": args, "id": f"call-{self._index}"}
                        ],
                    )
                )
            ]
        )

    @property
    def _llm_type(self) -> str:
        return "single-node-research-test"


def _fake_public_web_research(subject: str, question: str = "") -> dict[str, Any]:
    return {
        "subject": subject,
        "question": question,
        "queries": [_QUERY],
        "search_records": _SEARCH_RECORDS,
        "candidates": [
            {
                "title": "Release",
                "url": "https://example.test/release",
                "score": 12,
            }
        ],
        "sources": [
            {
                "url": "https://example.test/release",
                "title": "Release",
                "content_excerpt": "The release uses mandatory Workflows.",
                "usable": True,
                "error": None,
            }
        ],
        "fetch_records": [],
        "limitations": [],
        "summary": {"usable_source_count": 1},
    }


def _agent_with_fake_research():
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    binding = agent.tools[0]
    return replace(
        agent,
        tools=(
            ToolBinding(
                spec=dict(binding.spec),
                handler=_fake_public_web_research,
            ),
        ),
    )


def test_research_assistant_is_one_autonomous_compound_node() -> None:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent

    assert [item.id for item in agent.completion_validators] == [
        "validate_research_briefing"
    ]
    assert agent.output_contract is None
    assert agent.task_protocol.mode == "off"
    assert [skill.name for skill in agent.skills] == ["web-research"]
    workflow = agent.workflows[0]
    assert [node.id for node in workflow.nodes] == ["research"]
    node = workflow.node("research")
    assert node.execution == "autonomous"
    assert node.capability_tools == ("public_web_research",)
    assert node.max_steps == 4
    assert node.completion_validator == "validate_research_briefing"
    assert {binding.spec["name"] for binding in agent.tools} == {
        "public_web_research"
    }
    assert agent.tools[0].spec["max_calls_per_node"] == 1
    assert "不要把研究拆成内部阶段" in agent.instruction

    schema = node.completion_output_schema
    assert schema is not None
    validate_instance(schema, _FINAL_RESULT)
    with pytest.raises(WorkflowInstanceError, match="non-empty"):
        validate_instance(schema, {**_FINAL_RESULT, "search_records": []})


def test_research_validator_accepts_positive_and_bounded_negative_results() -> None:
    validator = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent.completion_validators[0]

    assert validator.validate(_FINAL_RESULT)
    negative = {
        "research_question": "威灿科技是什么公司?",
        "executive_summary": "本次多来源公开检索未建立与该名称可靠匹配的公开资料。",
        "task_results": [
            {
                "task": "核验公开身份",
                "result": "当前检索结果不足以形成可靠判断。",
                "evidence": [],
                "limitations": ["搜索结果没有可靠的主体名称匹配"],
            }
        ],
        "recommendations": [],
        "source_limitations": ["仅覆盖本次公开 Web 检索"],
        "sources": [],
        "search_records": [
            _search_record("bing_rss", "威灿科技"),
            _search_record("baidu", "威灿科技"),
        ],
    }
    assert validator.validate(negative)

    assert validator.explain is not None
    one_provider = {**negative, "search_records": negative["search_records"][:1]}
    assert validator.explain(one_provider) == (
        "negative research requires search records from at least two healthy providers"
    )
    unhealthy = {
        **negative,
        "search_records": [
            _search_record("bing_rss", "威灿科技", status="failed"),
            _search_record("baidu", "威灿科技", status="blocked"),
        ],
    }
    assert validator.explain(unhealthy) == (
        "negative research requires search records from at least two healthy providers"
    )
    absolute = {**negative, "executive_summary": "该公司不存在。"}
    assert validator.explain(absolute) == (
        "a bounded public-search miss cannot prove that the subject does not exist"
    )


def test_research_assistant_runs_one_operation_and_two_model_steps(tmp_path: Path) -> None:
    agent = _agent_with_fake_research()
    model = _ResearchModel()
    session = ModiSession(
        ModiHarness(model),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        max_steps=10,
    )

    response = session.run_task(
        agent=agent.name,
        workflow_id="research",
        input={"research_question": "What changed?"},
        thread_id="research-thread",
    )

    assert response["status"] == "completed"
    assert response["output"]["executive_summary"] == (
        "The runtime now requires explicit Workflows."
    )
    assert model._index == 2
    trace = list(session.get_trace("research-thread"))
    event_types = [event["event_type"] for event in trace]
    assert event_types.count("node_started") == 1
    assert event_types.count("operation_started") == 1
    assert event_types.count("operation_completed") == 1
    assert event_types.count("step_completed") == 2
    assert event_types.count("completion_accepted") == 1
    assert event_types.count("node_completed") == 1
    assert event_types[-1] == "workflow_completed"


def test_research_assistant_clarifies_then_uses_same_single_node(tmp_path: Path) -> None:
    agent = _agent_with_fake_research()
    model = _ResearchModel(clarify_first=True)
    session = ModiSession(
        ModiHarness(model),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        max_steps=10,
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

    completed = session.respond_to_interaction(
        thread_id="clarification-thread",
        interaction_id=interaction["interaction_id"],
        decision="approve",
        value="研究 Modi Harness 的最新变化",
    )

    assert completed["status"] == "completed"
    assert model._index == 3
    trace = list(session.get_trace("clarification-thread"))
    assert {event["payload"].get("node_id") for event in trace if "node_id" in event["payload"]} == {
        "research"
    }
