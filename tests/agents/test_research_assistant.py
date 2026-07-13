from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiHarness, ModiSession, ToolBinding
from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]

_SOURCE_URL = "https://example.test/company"


def _fake_research_result(subject: str, question: str) -> dict[str, Any]:
    return {
        "subject": subject,
        "question": question,
        "queries": [subject],
        "search_records": [
            {
                "provider": "duckduckgo",
                "query": subject,
                "search_url": "https://duckduckgo.com/?q=example",
                "status": "ok",
                "results": [{"title": subject, "url": _SOURCE_URL}],
            }
        ],
        "candidates": [{"title": subject, "url": _SOURCE_URL, "score": 10}],
        "sources": [
            {
                "url": _SOURCE_URL,
                "title": subject,
                "content_excerpt": f"Public information about {subject}: {question}",
                "usable": True,
                "error": None,
            }
        ],
        "fetch_records": [],
        "limitations": [],
        "summary": {"usable_source_count": 1},
    }


class _ScriptedResearchModel(BaseChatModel):
    def __init__(self, script: list[tuple[str, dict[str, Any]]]) -> None:
        super().__init__()
        self._script = script
        self._index = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del messages, stop, run_manager, kwargs
        name, args = self._script[self._index]
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
        return "research-workflow-test"


def _agent_with_fake_research(calls: list[tuple[str, str]]):
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent

    def fake_public_web_research(subject: str, question: str = "") -> dict[str, Any]:
        calls.append((subject, question))
        return _fake_research_result(subject, question)

    bindings = []
    for binding in agent.tools:
        if binding.spec["name"] == "public_web_research":
            bindings.append(
                ToolBinding(spec=dict(binding.spec), handler=fake_public_web_research)
            )
        else:
            bindings.append(binding)
    return replace(agent, tools=tuple(bindings))


def _session(
    tmp_path: Path,
    model: BaseChatModel,
    calls: list[tuple[str, str]],
) -> tuple[ModiSession, Any]:
    agent = _agent_with_fake_research(calls)
    session = ModiSession(
        ModiHarness(model),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        max_steps=20,
    )
    return session, agent


def test_research_assistant_declares_three_minimal_workflows() -> None:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent

    assert [workflow.id for workflow in agent.workflows] == [
        "deep_research",
        "quick_lookup",
        "reject_unsupported",
    ]
    assert agent.completion_validators == ()
    assert {binding.spec["name"] for binding in agent.tools} == {
        "public_web_research",
        "reject_research_request",
    }
    research_tool = next(
        item for item in agent.tools if item.spec["name"] == "public_web_research"
    )
    assert research_tool.spec["max_calls_per_node"] == 6

    quick = next(item for item in agent.workflows if item.id == "quick_lookup")
    assert quick.start_node == "search"
    assert quick.node("search").execution == "operation"
    assert quick.node("search").operation == "public_web_research"
    assert quick.node("answer").execution == "autonomous"
    assert quick.node("answer").capability_tools == ()

    deep = next(item for item in agent.workflows if item.id == "deep_research")
    assert deep.start_node == "confirm_scope"
    assert [deep.node(node_id).execution for node_id in (
        "confirm_scope",
        "investigate",
        "synthesize",
    )] == ["autonomous", "autonomous", "autonomous"]
    assert deep.node("investigate").capability_tools == ("public_web_research",)

    reject = next(item for item in agent.workflows if item.id == "reject_unsupported")
    assert reject.node("reject").operation == "reject_research_request"


def test_clear_entity_uses_quick_lookup_once(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__quick_lookup",
                {"subject": "中控技术", "question": "这家公司是做什么的?"},
            ),
            (
                "complete_node",
                {
                    "executive_summary": "中控技术提供工业自动化相关产品和服务。",
                    "citations": [_SOURCE_URL],
                },
            ),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    response = session.run_task(
        agent=agent.name,
        input={"prompt": "中控技术"},
        thread_id="quick-lookup",
    )

    assert response["status"] == "completed"
    assert response["output"]["executive_summary"] == (
        "中控技术提供工业自动化相关产品和服务。"
    )
    assert list(response["output"]["citations"]) == [_SOURCE_URL]
    assert "search_records" not in response["output"]
    assert calls == [("中控技术", "这家公司是做什么的?")]
    assert model._index == 2
    trace = list(session.get_trace("quick-lookup"))
    selected = next(item for item in trace if item["event_type"] == "workflow_selected")
    assert selected["payload"] == {"workflow_id": "quick_lookup", "strategy": "model"}
    assert [item["event_type"] for item in trace].count("operation_started") == 1


def test_evaluative_request_uses_deep_research_and_multiple_searches(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {
                    "request": "全面分析中控技术的竞争壁垒和风险",
                    "subject": "中控技术",
                    "question": "竞争壁垒和风险",
                },
            ),
            (
                "complete_node",
                {
                    "subject": "中控技术",
                    "research_question": "中控技术的竞争壁垒和风险是什么?",
                    "objectives": ["竞争壁垒", "主要风险"],
                },
            ),
            (
                "public_web_research",
                {"subject": "中控技术", "question": "产品和市场竞争壁垒"},
            ),
            (
                "public_web_research",
                {"subject": "中控技术", "question": "经营和行业风险"},
            ),
            (
                "complete_node",
                {
                    "research_summary": "公开资料显示公司有工业自动化积累, 同时面临行业竞争风险。",
                    "citations": [_SOURCE_URL],
                    "limitations": [],
                },
            ),
            (
                "complete_node",
                {
                    "executive_summary": "中控技术具备产品积累, 但仍需关注竞争和周期风险。",
                    "citations": [_SOURCE_URL],
                    "recommendations": ["结合财报继续核验"],
                },
            ),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    response = session.run_task(
        agent=agent.name,
        input={"prompt": "全面分析中控技术的竞争壁垒和风险"},
        thread_id="deep-research",
    )

    assert response["status"] == "completed"
    assert len(calls) == 2
    assert calls[0][1] == "产品和市场竞争壁垒"
    assert calls[1][1] == "经营和行业风险"
    assert response["output"]["executive_summary"].startswith("中控技术")
    trace = list(session.get_trace("deep-research"))
    completed_nodes = [
        item["payload"]["node_id"]
        for item in trace
        if item["event_type"] == "node_completed"
    ]
    assert completed_nodes == ["confirm_scope", "investigate", "synthesize"]


def test_non_research_request_is_rejected_without_search(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__reject_unsupported",
                {
                    "reason": "weather is outside public research scope",
                    "message": "我只能处理公开资料研究, 不能查询实时天气。",
                },
            )
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    response = session.run_task(
        agent=agent.name,
        input={"prompt": "明天天气"},
        thread_id="reject-weather",
    )

    assert response["status"] == "completed"
    assert response["output"]["rejected"] is True
    assert "不能查询实时天气" in response["output"]["executive_summary"]
    assert calls == []
    assert model._index == 1


def test_vague_deep_research_requests_scope_before_search(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "帮我深入研究一下", "subject": "", "question": ""},
            ),
            (
                "request_user_input",
                {
                    "prompt": "请告诉我要深入研究的主体和重点问题。",
                    "field": "research_scope",
                    "input_type": "text",
                    "required": True,
                },
            ),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    response = session.run_task(
        agent=agent.name,
        input={"prompt": "帮我深入研究一下"},
        thread_id="deep-clarify",
    )

    assert response["status"] == "interrupted"
    assert response["pending_interaction"]["payload"]["field"] == "research_scope"
    assert calls == []
    assert model._index == 2
