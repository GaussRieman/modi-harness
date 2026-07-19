from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiHarness, ModiSession, ToolBinding
from modi_harness.checkpoint import InMemoryRootCheckpointStore
from modi_harness.discovery import discover_agents
from modi_harness.long_task import InMemoryChildCheckpointStore

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


def _dimension_finding_draft(
    task_id: str,
    question: str,
    conclusion: str,
    *,
    verification_id: str,
) -> dict[str, Any]:
    return {
        "finding": {
            "conclusion": conclusion,
            "implications": "这项发现直接回答当前研究维度。",
            "verification_id": verification_id,
            "status": "sourced",
            "limitations": [],
        }
    }


def _scope_intent(
    *,
    subject: str,
    research_question: str,
    dimensions: list[tuple[str, str]],
) -> dict[str, Any]:
    candidate_dimensions = [
        {
            "id": task_id,
            "title": title,
            "criterion_id": f"criterion-{task_id}",
            "question": title,
            "entities": [{"name": subject, "aliases": []}],
            "dimension": title,
            "verification_method": "single_source_sufficient",
            "authority_bindings": [],
            "depends_on": [],
        }
        for task_id, title in dimensions
    ]
    return {
        "intent_id": "research-scope",
        "version": 1,
        "status": "draft",
        "goal": research_question,
        "desired_outcome": "形成有公开来源并明确限制的研究结论",
        "success_criteria": [
            {
                "id": item["criterion_id"],
                "description": item["question"],
                "required": True,
                "verification_mode": "evidence",
                "validator_id": "research-criterion-verifier",
            }
            for item in candidate_dimensions
        ],
        "constraints": ["仅使用当前公开来源"],
        "non_goals": [],
        "assumptions": [],
        "planning_context": {
            "subject": subject,
            "research_question": research_question,
            "candidate_dimensions": candidate_dimensions,
        },
    }


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
    search_resolution: str | list[str] | dict[str, str] = "sourced",
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
        if isinstance(search_resolution, dict):
            resolution = search_resolution.get(task_id, "sourced")
        elif isinstance(search_resolution, list):
            resolution = search_resolution.pop(0)
        else:
            resolution = search_resolution
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
        root_checkpoint_store=InMemoryRootCheckpointStore(),
        child_checkpoint_store=InMemoryChildCheckpointStore(),
    )
    return session, agent


def test_research_assistant_declares_three_entry_workflows_and_one_child_workflow() -> None:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent

    assert [workflow.id for workflow in agent.workflows] == [
        "deep_research",
        "quick_lookup",
        "reject_unsupported",
        "research_dimension",
    ]
    assert [item.id for item in agent.completion_validators] == [
        "research-task-graph-result"
    ]
    assert [item.id for item in agent.child_templates] == ["research-dimension"]
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
        for node_id in (
            "confirm_scope",
            "investigate",
            "finalize_report",
        )
    ] == ["autonomous", "task_graph", "operation"]
    assert deep.node("investigate").task_graph is not None
    assert deep.node("investigate").task_graph.child_templates == (
        "research-dimension",
    )
    assert deep.node("confirm_scope").completion_review == "required"
    assert deep.node("finalize_report").operation == "build_evidence_graph"
    assert "report" not in deep.node("finalize_report").inputs

    reject = next(item for item in agent.workflows if item.id == "reject_unsupported")
    assert reject.node("reject").operation == "reject_research_request"

    dimension = next(
        item for item in agent.workflows if item.id == "research_dimension"
    )
    assert dimension.start_node == "research"
    assert [
        dimension.node(node_id).execution
        for node_id in ("research", "commit_finding")
    ] == ["autonomous", "operation"]
    assert dimension.node("research").capability_tools == (
        "get_current_time",
        "public_web_search",
        "verify_claim_evidence",
    )
    assert dimension.node("commit_finding").operation == "record_research_finding"
    assert "evidence" not in dimension.node("commit_finding").inputs
    assert dimension.node("commit_finding").inputs["task_id"] == {
        "$ref": "#/workflow/input/context_manifest/extensions/research_task/id"
    }
    finding_schema = dimension.node("research").completion_output_schema["properties"][
        "finding"
    ]
    assert "evidence" not in finding_schema["properties"]


def test_research_dimension_commits_latest_cumulative_verification(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "dimensions"
    conclusion = "Tesla Model Y 与小米 YU7 的车身尺寸已有公开来源。"
    model = _ScriptedResearchModel(
        [
            _time_call(),
            _search_call(
                task_id,
                '"Tesla Model Y" 2026 车身尺寸 轴距',
                time_index=1,
                entity="Tesla Model Y",
                aliases=["Model Y", "Tesla ModelY", "特斯拉 Model Y"],
                dimension="车身尺寸与轴距",
            ),
            _verify_call(task_id, conclusion),
            _time_call(),
            _search_call(
                task_id,
                '"小米 YU7" 2026 车身尺寸 轴距',
                time_index=2,
                entity="小米 YU7",
                aliases=["小米YU7", "Xiaomi YU7", "小米YU"],
                dimension="车身尺寸与轴距",
            ),
            _verify_call(
                task_id,
                conclusion,
                search_ids=[
                    "search-dimensions-1",
                    "search-dimensions-2",
                ],
            ),
            (
                "complete_node",
                _dimension_finding_draft(
                    task_id,
                    "两款车型的车身尺寸与轴距有何差异?",
                    conclusion,
                    verification_id="verification-dimensions-2",
                ),
            ),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    response = session.run_task(
        agent=agent.name,
        workflow_id="research_dimension",
        input={
            "context_manifest": {
                "extensions": {
                    "research_task": {
                        "id": task_id,
                        "question": "两款车型的车身尺寸与轴距有何差异?",
                        "verification_method": "single_source_sufficient",
                        "authority_bindings": [],
                    }
                }
            }
        },
        thread_id="research-dimension-cumulative",
    )

    assert response["status"] == "completed", response
    assert response["output"]["task_id"] == task_id
    assert response["output"]["verification_id"] == "verification-dimensions-2"
    assert response["output"]["conclusion"] == conclusion
    assert list(response["output"]["citations"]) == [_SOURCE_URL]
    assert next(iter(response["output"]["evidence"]))["source_url"] == _SOURCE_URL
    assert calls == [
        (
            "public_web_search",
            '"Tesla Model Y" 2026 车身尺寸 轴距',
            task_id,
        ),
        (
            "public_web_search",
            '"小米 YU7" 2026 车身尺寸 轴距',
            task_id,
        ),
    ]
    completed_operations = [
        item["payload"]["adapter_id"]
        for item in session.get_trace("research-dimension-cumulative")
        if item["event_type"] == "operation_completed"
    ]
    assert completed_operations == [
        "get_current_time",
        "public_web_search",
        "verify_claim_evidence",
        "get_current_time",
        "public_web_search",
        "verify_claim_evidence",
        "record_research_finding",
    ]
    assert model._index == 7


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
    scope = _scope_intent(
        subject="杭州 AI 就业市场",
        research_question="杭州 AI 就业市场现状如何?",
        dimensions=[
            ("roles", "哪些 AI 岗位正在招聘?"),
            ("pay", "薪资和经验门槛如何?"),
        ],
    )
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
    assert draft["planning_context"]["subject"] == scope["planning_context"]["subject"]
    assert draft["goal"] == scope["goal"]
    assert [
        item["id"] for item in draft["planning_context"]["candidate_dimensions"]
    ] == ["roles", "pay"]
    assert model._index == 3
    requested = [
        event
        for event in session.get_trace("single-scope-review")
        if event["event_type"] == "interaction_requested"
    ]
    assert len(requested) == 1


def test_deep_research_scope_can_be_revised_before_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str]] = []
    first_plan = _scope_intent(
        subject="中控技术",
        research_question="研究中控技术",
        dimensions=[("business", "业务情况"), ("technology", "技术情况")],
    )
    revised_plan = _scope_intent(
        subject="中控技术",
        research_question="只研究中控技术的技术壁垒和风险",
        dimensions=[
            ("barriers", "核心技术壁垒"),
            ("risks", "技术商业化风险"),
        ],
    )
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
    assert draft["goal"] == revised_plan["goal"]
    assert [
        item["title"]
        for item in draft["planning_context"]["candidate_dimensions"]
    ] == [
        "核心技术壁垒",
        "技术商业化风险",
    ]
    assert calls == []
