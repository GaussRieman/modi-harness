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


def _verify_call(
    task_id: str,
    claim: str,
    *,
    urls: list[str] | None = None,
    source_type: str = "official",
    independent: bool = True,
    directness: str = "direct",
    as_of: str = "2026-06",
    search_ids: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    urls = urls if urls is not None else [_SOURCE_URL]
    search_ids = search_ids or [f"search-{task_id}-1"]
    return (
        "verify_claim_evidence",
        {
            "task_id": task_id,
            "claim": claim,
            "search_ids": search_ids,
            "items": [
                {
                    "source_url": url,
                    "source_type": source_type,
                    "stance": "supporting",
                    "independent": independent,
                    "directness": directness,
                    "as_of": as_of,
                }
                for url in urls
            ],
        },
    )


def _finding_call(
    task_id: str,
    question: str,
    conclusion: str,
    *,
    status: str = "sourced",
    citations: list[str] | None = None,
    limitations: list[str] | None = None,
    implications: str = "这项发现直接回答当前研究问题。",
    verification_method: str = "single_source_sufficient",
    source_type: str = "official",
    independence: str = "independent",
    directness: str = "direct",
    as_of: str = "2026-06",
    verification_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    urls = [_SOURCE_URL] if citations is None and status == "sourced" else citations or []
    if verification_id is None and verification_method != "unverifiable_flag":
        verification_id = f"verification-{task_id}-1"
    return (
        "record_research_finding",
        {
            "task_id": task_id,
            "question": question,
            "conclusion": conclusion,
            "implications": implications,
            "verification_method": verification_method,
            **({"verification_id": verification_id} if verification_id else {}),
            "status": status,
            "evidence": [
                {
                    "claim": conclusion,
                    "source_url": url,
                    "source_type": source_type,
                    "stance": "supporting",
                    "independence": independence,
                    "directness": directness,
                    "as_of": as_of,
                }
                for url in urls
            ],
            "limitations": limitations or [],
        },
    )


def _time_call() -> tuple[str, dict[str, Any]]:
    return ("get_current_time", {})


def _search_call(
    task_id: str,
    query: str,
    *,
    time_index: int,
    entity: str = "中控技术",
    aliases: list[str] | None = None,
    dimension: str = "公开信息",
) -> tuple[str, dict[str, Any]]:
    return (
        "public_web_search",
        {
            "searches": [
                {
                    "query": query,
                    "entity": entity,
                    "aliases": aliases or [],
                    "dimension": dimension,
                }
            ],
            "task_id": task_id,
            "time_token": f"time-{time_index}",
        },
    )


def _fake_research_result(
    subject: str,
    question: str,
    task_id: str = "",
    *,
    search_id: str = "search-quick-1",
) -> dict[str, Any]:
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
        "search_id": search_id,
        "limitations": [],
        "summary": {"usable_source_count": 1},
        "operation_summary": {
            "search_id": search_id,
            "task_id": task_id,
            "usable_sources": [{"url": _SOURCE_URL, "title": subject}],
        },
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
    handlers = {binding.spec["name"]: binding.handler for binding in agent.tools}
    time_count = 0
    search_counts: dict[str, int] = {}
    verification_counts: dict[str, int] = {}

    def fake_get_current_time() -> dict[str, Any]:
        nonlocal time_count
        time_count += 1
        result = handlers["get_current_time"]()
        result["time_token"] = f"time-{time_count}"
        return result

    def fake_public_web_research(
        subject: str,
        question: str = "",
        task_id: str = "",
        time_token: str = "",
    ) -> dict[str, Any]:
        del time_token
        calls.append((subject, question, task_id))
        result = _fake_research_result(subject, question, task_id)
        return result

    def fake_public_web_search(
        searches: list[dict[str, Any]],
        task_id: str,
        time_token: str,
    ) -> dict[str, Any]:
        del time_token
        query = " | ".join(str(item["query"]) for item in searches)
        calls.append(("public_web_search", query, task_id))
        search_counts[task_id] = search_counts.get(task_id, 0) + 1
        search_id = f"search-{task_id}-{search_counts[task_id]}"
        resolution = (
            search_resolution.pop(0)
            if isinstance(search_resolution, list)
            else search_resolution
        )
        result = _fake_research_result("", query, task_id, search_id=search_id)
        result["searches"] = searches
        result["resolution"] = resolution
        if resolution != "sourced":
            result["sources"] = []
            result["summary"] = {"usable_source_count": 0}
            result["operation_summary"]["usable_sources"] = []
        return result

    def fake_verify_claim_evidence(**kwargs: Any) -> dict[str, Any]:
        task_id = str(kwargs["task_id"])
        verification_counts[task_id] = verification_counts.get(task_id, 0) + 1
        result = handlers["verify_claim_evidence"](**kwargs)
        verification_id = f"verification-{task_id}-{verification_counts[task_id]}"
        result["verification_id"] = verification_id
        result["operation_summary"]["verification_id"] = verification_id
        return result

    bindings = []
    for binding in agent.tools:
        if binding.spec["name"] == "get_current_time":
            bindings.append(ToolBinding(spec=dict(binding.spec), handler=fake_get_current_time))
        elif binding.spec["name"] == "public_web_research":
            bindings.append(
                ToolBinding(spec=dict(binding.spec), handler=fake_public_web_research)
            )
        elif binding.spec["name"] == "public_web_search":
            bindings.append(ToolBinding(spec=dict(binding.spec), handler=fake_public_web_search))
        elif binding.spec["name"] == "verify_claim_evidence":
            bindings.append(
                ToolBinding(spec=dict(binding.spec), handler=fake_verify_claim_evidence)
            )
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
        max_steps=30,
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
    assert agent.child_templates == ()
    assert {binding.spec["name"] for binding in agent.tools} == {
        "get_current_time",
        "public_web_research",
        "public_web_search",
        "verify_claim_evidence",
        "record_research_finding",
        "build_evidence_graph",
        "reject_research_request",
    }
    research_tool = next(
        item for item in agent.tools if item.spec["name"] == "public_web_research"
    )
    assert research_tool.spec["max_calls_per_node"] == 6
    discovery_tool = next(
        item for item in agent.tools if item.spec["name"] == "public_web_search"
    )
    assert discovery_tool.spec["max_calls_per_task"] == 2
    assert "max_calls_per_node" not in discovery_tool.spec
    verify_tool = next(
        item for item in agent.tools if item.spec["name"] == "verify_claim_evidence"
    )
    assert "max_calls_per_task" not in verify_tool.spec
    assert "max_calls_per_node" not in verify_tool.spec

    quick = next(item for item in agent.workflows if item.id == "quick_lookup")
    assert quick.start_node == "current_time"
    assert quick.node("current_time").execution == "operation"
    assert quick.node("current_time").operation == "get_current_time"
    assert quick.node("search").execution == "operation"
    assert quick.node("search").operation == "public_web_research"
    assert quick.node("answer").execution == "autonomous"
    assert quick.node("answer").capability_tools == ()

    deep = next(item for item in agent.workflows if item.id == "deep_research")
    assert deep.start_node == "confirm_scope"
    assert [
        deep.node(node_id).execution
        for node_id in ("confirm_scope", "investigate", "finalize_report")
    ] == ["autonomous", "autonomous", "operation"]
    assert deep.node("investigate").capability_tools == (
        "get_current_time",
        "public_web_search",
        "verify_claim_evidence",
        "record_research_finding",
    )
    assert deep.node("confirm_scope").completion_review == "required"
    assert deep.node("finalize_report").operation == "build_evidence_graph"

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
    assert [item["event_type"] for item in trace].count("operation_started") == 2
    completed_operations = {
        item["payload"]["adapter_id"]: item["payload"]["operation_summary"]
        for item in trace
        if item["event_type"] == "operation_completed"
    }
    assert completed_operations["get_current_time"]["timezone"] == "Asia/Shanghai"
    assert completed_operations["public_web_research"]["search_id"] == "search-quick-1"
    assert "time_token" not in str(completed_operations)
    assert "content_excerpt" not in str(completed_operations)


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
                            {
                                "id": "barriers",
                                "title": "产品和市场竞争壁垒",
                            },
                            {
                                "id": "risks",
                                "title": "经营和行业风险",
                            },
                        ]
                    },
                },
            ),
            _time_call(),
            _search_call(
                "barriers",
                "中控技术 产品和市场竞争壁垒",
                time_index=1,
                dimension="产品和市场竞争壁垒",
            ),
            _verify_call("barriers", "具备工业自动化产品积累。"),
            _finding_call(
                "barriers",
                "产品和市场竞争壁垒",
                "具备工业自动化产品积累。",
            ),
            _time_call(),
            _search_call(
                "risks",
                "中控技术 经营和行业风险",
                time_index=2,
                dimension="经营和行业风险",
            ),
            _verify_call("risks", "面临行业竞争风险。"),
            _finding_call(
                "risks",
                "经营和行业风险",
                "面临行业竞争风险。",
            ),
            (
                "complete_node",
                {
                    "direct_answer": "中控技术具备产品积累, 但仍需关注竞争和周期风险。",
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
    assert response["output"]["direct_answer"].startswith("中控技术")
    assert "evidence_graph" in response["output"]
    assert response["output"]["key_findings"][0]["task_id"] == "barriers"
    trace = list(session.get_trace("deep-research"))
    completed_nodes = [
        item["payload"]["node_id"]
        for item in trace
        if item["event_type"] == "node_completed"
    ]
    assert completed_nodes == ["confirm_scope", "investigate", "finalize_report"]
    event_types = [item["event_type"] for item in trace]
    assert event_types.count("task_plan_created") == 1
    assert event_types.count("task_started") == 2
    assert event_types.count("task_completed") == 2


def test_deep_research_injects_verified_evidence_into_recorded_findings(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    final_output = {
        "direct_answer": "两项研究问题均已有公开来源。",
        "key_findings": [
            {"task_id": "business", "conclusion": "不应采用的重复内容"},
        ],
        "citations": ["https://unobserved.test/final"],
        "limitations": [],
    }
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "分析中控技术", "subject": "中控技术"},
            ),
            (
                "complete_node",
                {
                    "subject": "中控技术",
                    "research_question": "中控技术的业务和市场情况如何?",
                    "task_plan": {
                        "items": [
                            {
                                "id": "business",
                                "title": "主营业务",
                            },
                            {
                                "id": "market",
                                "title": "市场情况",
                            },
                        ]
                    },
                },
            ),
            _time_call(),
            _search_call(
                "business",
                "中控技术 主营业务",
                time_index=1,
                dimension="主营业务",
            ),
            _verify_call("business", "公司提供工业自动化产品。"),
            _finding_call(
                "business",
                "主营业务",
                "公司提供工业自动化产品。",
                citations=["https://unobserved.test/source"],
            ),
            _time_call(),
            _search_call(
                "market",
                "中控技术 市场行业",
                time_index=2,
                dimension="市场行业",
            ),
            _verify_call("market", "公司服务多个流程工业行业。"),
            _finding_call(
                "market",
                "市场情况",
                "公司服务多个流程工业行业。",
            ),
            ("complete_node", final_output),
        ]
    )
    session, agent = _session(tmp_path, model, calls)
    scope_review = session.run_task(
        agent=agent.name,
        input={"prompt": "分析中控技术"},
        thread_id="repair-finding",
    )

    completed = session.respond_to_interaction(
        thread_id="repair-finding",
        interaction_id=scope_review["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    assert completed["status"] == "completed"
    state = session.get_state("repair-finding")
    assert state is not None
    failed_steps = [
        item
        for item in state["step_records"]
        if item["state_delta"].get("operation_error")
    ]
    assert failed_steps == []
    assert completed["output"]["key_findings"][0]["conclusion"] == (
        "公司提供工业自动化产品。"
    )
    assert list(completed["output"]["citations"]) == [_SOURCE_URL]
    assert not any(
        event["event_type"] == "completion_rejected"
        for event in session.get_trace("repair-finding")
    )


def test_four_question_research_budget_covers_hidden_protocol_repairs(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    questions = [
        ("employers", "杭州有哪些主要 AI 雇主?", 1),
        ("roles", "杭州企业在招聘哪些 AI 岗位?", 2),
        ("pay", "杭州 AI 岗位的薪资和门槛如何?", 2),
        ("trend", "杭州 AI 人才需求趋势如何?", 2),
    ]
    scope = {
        "subject": "杭州 AI 就业市场",
        "research_question": "杭州 AI 就业市场现状如何?",
        "task_plan": {
            "items": [
                {
                    "id": task_id,
                    "title": question,
                }
                for task_id, question, _count in questions
            ]
        },
    }
    script: list[tuple[str, dict[str, Any]]] = [
        (
            "route__deep_research",
            {"request": "the AI job market in Hangzhou", "subject": "杭州 AI 就业"},
        ),
        ("complete_node", scope),
    ]
    padding_attempts = 5
    for time_index, (task_id, question, _count) in enumerate(questions, start=1):
        if task_id == "trend":
            script.extend(
                (
                    "complete_node",
                    {
                        "direct_answer": "研究尚未完成。",
                        "limitations": [],
                    },
                )
                for _ in range(padding_attempts)
            )
        script.extend(
            [
                _time_call(),
                _search_call(
                    task_id,
                    f"{question} 查询 1",
                    time_index=time_index,
                    entity="杭州 AI 就业市场",
                    dimension=question,
                ),
            ]
        )
        conclusion = f"{question} 已获得证据。"
        script.append(_verify_call(task_id, conclusion))
        script.append(_finding_call(task_id, question, conclusion))
    script.append(
        (
            "complete_node",
            {
                "direct_answer": "杭州 AI 就业市场的四项问题均已完成研究。",
                "limitations": [],
            },
        )
    )
    session, agent = _session(tmp_path, _ScriptedResearchModel(script), calls)
    review = session.run_task(
        agent=agent.name,
        input={"prompt": "the AI job market in Hangzhou"},
        thread_id="bounded-searches",
    )

    completed = session.respond_to_interaction(
        thread_id="bounded-searches",
        interaction_id=review["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    assert completed["status"] == "completed"
    assert len(calls) == 4
    state = session.get_state("bounded-searches")
    assert state is not None
    investigate_steps = [
        item for item in state["step_records"] if item["node_id"] == "investigate"
    ]
    expected_steps = 4 * 4 + padding_attempts + 1
    assert len(investigate_steps) == expected_steps
    assert investigate_steps[-2]["decision"]["operation"]["target"] == (
        "record_research_finding"
    )
    assert investigate_steps[-2]["index"] == expected_steps - 1
    assert investigate_steps[-1]["decision"]["operation"]["target"] == "complete_node"


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


def test_scope_review_suppresses_duplicate_model_confirmation(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
    scope = {
        "subject": "杭州 AI 就业市场",
        "research_question": "杭州 AI 就业市场现状如何?",
        "task_plan": {
            "items": [
                {
                    "id": "roles",
                    "title": "哪些 AI 岗位正在招聘?",
                },
                {
                    "id": "pay",
                    "title": "薪资和经验门槛如何?",
                },
            ]
        },
    }
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "the AI job market in Hangzhou", "subject": "杭州 AI 就业"},
            ),
            (
                "request_user_input",
                {
                    "prompt": "是否按这份范围执行?",
                    "field": "scope_confirmation",
                    "input_type": "confirm",
                    "required": True,
                },
            ),
            ("complete_node", scope),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    waiting = session.run_task(
        agent=agent.name,
        input={"prompt": "the AI job market in Hangzhou"},
        thread_id="single-scope-review",
    )

    assert waiting["status"] == "interrupted"
    assert waiting["pending_interaction"]["kind"] == "node_review"
    draft = waiting["pending_interaction"]["payload"]["draft"]
    assert draft["subject"] == scope["subject"]
    assert draft["research_question"] == scope["research_question"]
    assert [item["id"] for item in draft["task_plan"]["items"]] == ["roles", "pay"]
    assert model._index == 3
    requested = [
        event
        for event in session.get_trace("single-scope-review")
        if event["event_type"] == "interaction_requested"
    ]
    assert len(requested) == 1


def test_deep_research_scope_can_be_revised_before_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
    first_plan = {
        "subject": "中控技术",
        "research_question": "研究中控技术",
        "task_plan": {
            "items": [
                {
                    "id": "business",
                    "title": "业务情况",
                },
                {
                    "id": "technology",
                    "title": "技术情况",
                },
            ]
        },
    }
    revised_plan = {
        "subject": "中控技术",
        "research_question": "只研究中控技术的技术壁垒和风险",
        "task_plan": {
            "items": [
                {
                    "id": "barriers",
                    "title": "核心技术壁垒",
                },
                {
                    "id": "risks",
                    "title": "技术商业化风险",
                },
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


def test_deep_research_keeps_evidence_gap_as_a_limitation_without_interrupting(
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
                            {
                                "id": "companies",
                                "title": "发现杭州具身智能公司",
                            },
                            {
                                "id": "products",
                                "title": "核验候选公司的产品",
                            },
                        ]
                    },
                },
            ),
            _time_call(),
            _search_call(
                "companies",
                "杭州 具身智能 公司",
                time_index=1,
                entity="杭州具身智能公司",
                dimension="公司发现",
            ),
            _verify_call(
                "companies",
                "两次不同查询仍未找到可用来源。",
                urls=[],
            ),
            _finding_call(
                "companies",
                "发现杭州具身智能公司",
                "两次不同查询仍未找到可用来源。",
                status="blocked",
                citations=[],
                limitations=["公开搜索未返回可用公司来源"],
            ),
            _time_call(),
            _search_call(
                "products",
                "杭州 具身智能 公司 产品",
                time_index=2,
                entity="杭州具身智能公司",
                dimension="公司产品",
            ),
            _verify_call("products", "找到一项可用公开来源。"),
            _finding_call(
                "products",
                "核验候选公司的产品",
                "找到一项可用公开来源。",
            ),
            (
                "complete_node",
                {
                    "direct_answer": "公开检索不足以形成完整公司清单。",
                    "limitations": ["公司发现问题缺少足够公开证据, 结论可能不完整。"],
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
    completed = session.respond_to_interaction(
        thread_id="evidence-gap",
        interaction_id=scope_review["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    assert completed["status"] == "completed"
    assert calls == [
        (
            "public_web_search",
            "杭州 具身智能 公司",
            "companies",
        ),
        ("public_web_search", "杭州 具身智能 公司 产品", "products"),
    ]
    assert completed["output"]["key_findings"][0]["status"] == "limited"
    assert completed["output"]["key_findings"][0]["conclusion"] == (
        "两次不同查询仍未找到可用来源。"
    )
    assert "公开搜索未返回可用公司来源" in completed["output"]["limitations"]
    assert completed["output"]["limitations"]
    trace_types = [item["event_type"] for item in session.get_trace("evidence-gap")]
    assert "task_blocked" not in trace_types
    assert trace_types.count("interaction_requested") == 1


def test_search_requires_verification_and_follow_up_invalidates_old_verification(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "分析中控技术业务和风险", "subject": "中控技术"},
            ),
            (
                "complete_node",
                {
                    "subject": "中控技术",
                    "research_question": "中控技术的业务和风险如何?",
                    "task_plan": {
                        "items": [
                            {"id": "business", "title": "主营业务"},
                            {"id": "risks", "title": "主要风险"},
                        ]
                    },
                },
            ),
            _time_call(),
            _search_call(
                "business",
                "中控技术 主营业务",
                time_index=1,
                dimension="主营业务",
            ),
            _finding_call(
                "business",
                "主营业务",
                "有来源却直接记录为证据不足。",
                status="blocked",
                citations=[],
                limitations=["尚未验证来源"],
            ),
            _finding_call(
                "business",
                "主营业务",
                "搜索后错误切换为不可验证。",
                status="blocked",
                citations=[],
                limitations=["不可验证"],
                verification_method="unverifiable_flag",
            ),
            _verify_call("business", "公司提供工业自动化产品。"),
            _time_call(),
            _search_call(
                "business",
                "中控技术 官方 主营业务",
                time_index=2,
                dimension="官方主营业务",
            ),
            _finding_call(
                "business",
                "主营业务",
                "公司提供工业自动化产品。",
            ),
            _verify_call(
                "business",
                "公司提供工业自动化产品。",
                search_ids=["search-business-1", "search-business-2"],
            ),
            _finding_call(
                "business",
                "主营业务",
                "公司提供工业自动化产品。",
                verification_id="verification-business-2",
            ),
            _time_call(),
            _search_call(
                "risks",
                "中控技术 主要风险",
                time_index=3,
                dimension="主要风险",
            ),
            _verify_call("risks", "公司面临行业竞争风险。"),
            _finding_call("risks", "主要风险", "公司面临行业竞争风险。"),
            (
                "complete_node",
                {
                    "direct_answer": "业务和风险问题均已核验。",
                    "limitations": [],
                },
            ),
        ]
    )
    session, agent = _session(tmp_path, model, calls)
    review = session.run_task(
        agent=agent.name,
        input={"prompt": "分析中控技术业务和风险"},
        thread_id="verification-gates",
    )

    completed = session.respond_to_interaction(
        thread_id="verification-gates",
        interaction_id=review["pending_interaction"]["interaction_id"],
        decision="approved",
    )

    assert completed["status"] == "completed"
    state = session.get_state("verification-gates")
    assert state is not None
    errors = [
        str(item["state_delta"].get("operation_error") or "")
        for item in state["step_records"]
    ]
    assert any("requires a verification_id produced" in item for item in errors)
    assert any("cannot use unverifiable_flag after searching" in item for item in errors)
    assert any("verification is stale" in item for item in errors)
