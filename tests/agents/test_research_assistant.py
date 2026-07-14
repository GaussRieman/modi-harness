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


def _fake_research_result(subject: str, question: str, task_id: str = "") -> dict[str, Any]:
    return {
        "subject": subject,
        "question": question,
        "task_id": task_id,
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


def _agent_with_fake_research(
    calls: list[tuple[str, str, str]],
    *,
    search_resolution: str | list[str] = "sourced",
):
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent

    def fake_public_web_research(
        subject: str,
        question: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        calls.append((subject, question, task_id))
        return _fake_research_result(subject, question, task_id)

    def fake_public_web_search(query: str, task_id: str) -> dict[str, Any]:
        calls.append(("public_web_search", query, task_id))
        resolution = (
            search_resolution.pop(0)
            if isinstance(search_resolution, list)
            else search_resolution
        )
        result = _fake_research_result("", query, task_id)
        result["query"] = query
        result["resolution"] = resolution
        if resolution != "sourced":
            result["sources"] = []
            result["summary"] = {"usable_source_count": 0}
        return result

    bindings = []
    for binding in agent.tools:
        if binding.spec["name"] == "public_web_research":
            bindings.append(
                ToolBinding(spec=dict(binding.spec), handler=fake_public_web_research)
            )
        elif binding.spec["name"] == "public_web_search":
            bindings.append(ToolBinding(spec=dict(binding.spec), handler=fake_public_web_search))
        else:
            bindings.append(binding)
    return replace(agent, tools=tuple(bindings))


def _session(
    tmp_path: Path,
    model: BaseChatModel,
    calls: list[tuple[str, str, str]],
    search_resolution: str | list[str] = "sourced",
) -> tuple[ModiSession, Any]:
    agent = _agent_with_fake_research(calls, search_resolution=search_resolution)
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
        "public_web_search",
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
    )] == ["autonomous", "autonomous"]
    assert deep.node("investigate").capability_tools == ("public_web_search",)
    assert deep.node("confirm_scope").completion_review == "required"

    reject = next(item for item in agent.workflows if item.id == "reject_unsupported")
    assert reject.node("reject").operation == "reject_research_request"


def test_clear_entity_uses_quick_lookup_once(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
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
    assert calls == [("中控技术", "这家公司是做什么的?", "")]
    assert model._index == 2
    trace = list(session.get_trace("quick-lookup"))
    selected = next(item for item in trace if item["event_type"] == "workflow_selected")
    assert selected["payload"]["workflow_id"] == "quick_lookup"
    assert selected["payload"]["strategy"] == "model"
    assert [item["event_type"] for item in trace].count("operation_started") == 1


def test_evaluative_request_uses_deep_research_and_multiple_searches(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
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
                    "task_plan": {
                        "items": [
                            {"id": "barriers", "title": "产品和市场竞争壁垒"},
                            {"id": "risks", "title": "经营和行业风险"},
                        ]
                    },
                },
            ),
            (
                "public_web_search",
                {
                    "query": "中控技术 产品和市场竞争壁垒",
                    "task_id": "barriers",
                },
            ),
            (
                "public_web_search",
                {
                    "query": "中控技术 经营和行业风险",
                    "task_id": "risks",
                },
            ),
            (
                "complete_node",
                {
                    "executive_summary": "中控技术具备产品积累, 但仍需关注竞争和周期风险。",
                    "task_results": [
                        {
                            "task_id": "barriers",
                            "question": "产品和市场竞争壁垒",
                            "result": "具备工业自动化产品积累。",
                            "status": "sourced",
                            "citations": [_SOURCE_URL],
                        },
                        {
                            "task_id": "risks",
                            "question": "经营和行业风险",
                            "result": "面临行业竞争风险。",
                            "status": "sourced",
                            "citations": [_SOURCE_URL],
                        },
                    ],
                    "citations": [_SOURCE_URL],
                    "limitations": [],
                },
            ),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    waiting = session.run_task(
        agent=agent.name,
        input={"prompt": "全面分析中控技术的竞争壁垒和风险"},
        thread_id="deep-research",
    )

    assert waiting["status"] == "interrupted"
    assert waiting["pending_interaction"]["kind"] == "node_review"
    assert calls == []
    response = session.respond_to_interaction(
        thread_id="deep-research",
        interaction_id=waiting["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    assert response["status"] == "completed"
    assert len(calls) == 2
    assert calls[0][1:] == ("中控技术 产品和市场竞争壁垒", "barriers")
    assert calls[1][1:] == ("中控技术 经营和行业风险", "risks")
    assert response["output"]["executive_summary"].startswith("中控技术")
    trace = list(session.get_trace("deep-research"))
    completed_nodes = [
        item["payload"]["node_id"]
        for item in trace
        if item["event_type"] == "node_completed"
    ]
    assert completed_nodes == ["confirm_scope", "investigate"]
    event_types = [item["event_type"] for item in trace]
    assert event_types.count("task_plan_created") == 1
    assert event_types.count("task_started") == 2
    assert event_types.count("task_completed") == 2


def test_non_research_request_is_rejected_without_search(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
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
    calls: list[tuple[str, str, str]] = []
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


def test_deep_research_scope_can_be_revised_before_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
    first_plan = {
        "subject": "中控技术",
        "research_question": "研究中控技术",
        "task_plan": {
            "items": [
                {"id": "business", "title": "业务情况"},
                {"id": "technology", "title": "技术情况"},
            ]
        },
    }
    revised_plan = {
        "subject": "中控技术",
        "research_question": "只研究中控技术的技术壁垒和风险",
        "task_plan": {
            "items": [
                {"id": "barriers", "title": "核心技术壁垒"},
                {"id": "risks", "title": "技术商业化风险"},
            ]
        },
    }
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "深入研究中控技术", "subject": "中控技术"},
            ),
            ("complete_node", first_plan),
            ("complete_node", revised_plan),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    first = session.run_task(
        agent=agent.name,
        input={"prompt": "深入研究中控技术"},
        thread_id="revise-scope",
    )
    second = session.respond_to_interaction(
        thread_id="revise-scope",
        interaction_id=first["pending_interaction"]["interaction_id"],
        decision="revise",
        feedback="只看技术壁垒和商业化风险",
    )

    assert first["status"] == "interrupted"
    assert second["status"] == "interrupted"
    draft = second["pending_interaction"]["payload"]["draft"]
    assert draft["research_question"] == revised_plan["research_question"]
    assert [item["title"] for item in draft["task_plan"]["items"]] == [
        "核心技术壁垒",
        "技术商业化风险",
    ]
    assert calls == []


def test_deep_research_pauses_on_evidence_gap_and_can_skip_with_limitations(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "研究杭州具身智能公司", "subject": "杭州具身智能"},
            ),
            (
                "complete_node",
                {
                    "subject": "杭州具身智能",
                    "research_question": "杭州有哪些具身智能公司?",
                    "task_plan": {
                        "items": [
                            {"id": "companies", "title": "发现杭州具身智能公司"},
                            {"id": "products", "title": "核验候选公司的产品"},
                        ]
                    },
                },
            ),
            (
                "public_web_search",
                {
                    "query": "杭州 具身智能 公司",
                    "task_id": "companies",
                },
            ),
            (
                "public_web_search",
                {
                    "query": "杭州 具身智能 公司 产品",
                    "task_id": "products",
                },
            ),
            (
                "complete_node",
                {
                    "executive_summary": "公开检索不足以形成完整公司清单。",
                    "task_results": [
                        {
                            "task_id": "companies",
                            "question": "发现杭州具身智能公司",
                            "result": "用户选择在无可用证据时继续。",
                            "status": "skipped",
                            "citations": [],
                        },
                        {
                            "task_id": "products",
                            "question": "核验候选公司的产品",
                            "result": "找到一项可用公开来源。",
                            "status": "sourced",
                            "citations": [_SOURCE_URL],
                        },
                    ],
                    "citations": [_SOURCE_URL],
                    "limitations": ["公司发现问题由用户选择跳过, 结论可能不完整。"],
                },
            ),
        ]
    )
    session, agent = _session(
        tmp_path,
        model,
        calls,
        search_resolution=["no_evidence", "sourced"],
    )

    scope_review = session.run_task(
        agent=agent.name,
        input={"prompt": "研究杭州具身智能公司"},
        thread_id="evidence-gap",
    )
    gap = session.respond_to_interaction(
        thread_id="evidence-gap",
        interaction_id=scope_review["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    assert gap["status"] == "interrupted"
    assert gap["pending_interaction"]["kind"] == "user_input"
    assert "杭州 具身智能 公司" in gap["pending_interaction"]["prompt"]
    assert calls == [("public_web_search", "杭州 具身智能 公司", "companies")]

    completed = session.respond_to_interaction(
        thread_id="evidence-gap",
        interaction_id=gap["pending_interaction"]["interaction_id"],
        decision="submitted",
        value="skip",
    )

    assert completed["status"] == "completed"
    assert completed["output"]["task_results"][0]["status"] == "skipped"
    assert completed["output"]["limitations"]
    trace_types = [item["event_type"] for item in session.get_trace("evidence-gap")]
    assert "task_blocked" in trace_types
    assert trace_types.count("interaction_requested") == 2


def test_deep_research_evidence_gap_can_be_cancelled(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "研究杭州具身智能公司", "subject": "杭州具身智能"},
            ),
            (
                "complete_node",
                {
                    "subject": "杭州具身智能",
                    "research_question": "杭州有哪些具身智能公司?",
                    "task_plan": {
                        "items": [
                            {"id": "companies", "title": "发现杭州具身智能公司"},
                            {"id": "products", "title": "核验候选公司的产品"},
                        ]
                    },
                },
            ),
            (
                "public_web_search",
                {"query": "杭州 具身智能 公司", "task_id": "companies"},
            ),
        ]
    )
    session, agent = _session(
        tmp_path,
        model,
        calls,
        search_resolution="no_evidence",
    )
    scope_review = session.run_task(
        agent=agent.name,
        input={"prompt": "研究杭州具身智能公司"},
        thread_id="cancel-gap",
    )
    gap = session.respond_to_interaction(
        thread_id="cancel-gap",
        interaction_id=scope_review["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    cancelled = session.respond_to_interaction(
        thread_id="cancel-gap",
        interaction_id=gap["pending_interaction"]["interaction_id"],
        decision="cancelled",
    )

    assert cancelled["status"] == "cancelled"


def test_deep_research_retries_the_same_question_with_user_query(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "研究杭州机器人公司", "subject": "杭州机器人公司"},
            ),
            (
                "complete_node",
                {
                    "subject": "杭州机器人公司",
                    "research_question": "杭州有哪些机器人公司?",
                    "task_plan": {
                        "items": [
                            {"id": "companies", "title": "发现杭州机器人公司"},
                            {"id": "products", "title": "核验公司的机器人产品"},
                        ]
                    },
                },
            ),
            (
                "public_web_search",
                {"query": "杭州机器人公司", "task_id": "companies"},
            ),
            (
                "public_web_search",
                {"query": "杭州宇树科技 机器人", "task_id": "companies"},
            ),
            (
                "public_web_search",
                {"query": "杭州机器人公司 产品", "task_id": "products"},
            ),
            (
                "complete_node",
                {
                    "executive_summary": "用户补充查询后找到了可用来源。",
                    "task_results": [
                        {
                            "task_id": "companies",
                            "question": "发现杭州机器人公司",
                            "result": "找到宇树科技。",
                            "status": "sourced",
                            "citations": [_SOURCE_URL],
                        },
                        {
                            "task_id": "products",
                            "question": "核验公司的机器人产品",
                            "result": "找到机器人产品资料。",
                            "status": "sourced",
                            "citations": [_SOURCE_URL],
                        },
                    ],
                    "citations": [_SOURCE_URL],
                    "limitations": [],
                },
            ),
        ]
    )
    session, agent = _session(
        tmp_path,
        model,
        calls,
        search_resolution=["no_evidence", "sourced", "sourced"],
    )
    scope_review = session.run_task(
        agent=agent.name,
        input={"prompt": "研究杭州机器人公司"},
        thread_id="retry-gap",
    )
    gap = session.respond_to_interaction(
        thread_id="retry-gap",
        interaction_id=scope_review["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    completed = session.respond_to_interaction(
        thread_id="retry-gap",
        interaction_id=gap["pending_interaction"]["interaction_id"],
        decision="submitted",
        value="杭州宇树科技 机器人",
    )

    assert completed["status"] == "completed"
    assert calls[1] == ("public_web_search", "杭州宇树科技 机器人", "companies")
    assert [item[2] for item in calls] == ["companies", "companies", "products"]
