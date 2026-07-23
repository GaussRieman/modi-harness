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
    source_urls: list[str],
) -> dict[str, Any]:
    return {
        "finding": {
            "conclusion": conclusion,
            "implications": "这项发现直接回答当前研究维度。",
            "source_urls": source_urls,
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


def _research_brief_call(
    request: str,
    *,
    objective: str | None = None,
    task_type: str = "landscape",
    entities: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    return (
        "complete_node",
        {
            "research_brief": {
                "original_request": request,
                "objective": objective or request,
                "task_type": task_type,
                "entities": entities or [],
                "freshness": "current_or_unspecified",
                "constraints": [],
                "material_ambiguities": [],
            },
            "exploration_queries": [
                {"query": f"{request} overview", "purpose": "direct overview"},
                {"query": f"{request} official", "purpose": "official sources"},
                {"query": f"{request} report", "purpose": "industry reports"},
                {"query": f"{request} latest", "purpose": "current developments"},
            ],
        },
    )


def _research_map_call(
    *,
    subject: str,
    task_id: str,
    title: str,
    question: str,
) -> tuple[str, dict[str, Any]]:
    coverage_id = f"coverage-{task_id}"
    return (
        "complete_node",
        {
            "subject": subject,
            "landscape_map": {
                "summary": "探索搜索已定位相关公开资料。",
                "themes": [],
                "early_conflicts": [],
                "unresolved_terms": [],
            },
            "coverage_map": {
                "items": [
                    {
                        "id": coverage_id,
                        "label": title,
                        "question": question,
                        "rationale": "直接回答用户的核心问题。",
                        "required": True,
                        "status": "partial",
                    }
                ]
            },
            "tasks": [
                {
                    "id": task_id,
                    "title": title,
                    "question": question,
                    "rationale": "补齐核心答案。",
                    "information_gap": question,
                    "coverage_ids": [coverage_id],
                    "entities": [{"name": subject, "aliases": []}],
                    "priority": 80,
                }
            ],
        },
    )


def _search_call(
    task_id: str,
    query: str,
    *,
    time_index: int | None = None,
    entity: str = "中控技术",
    aliases: list[str] | None = None,
    dimension: str = "公开信息",
) -> tuple[str, dict[str, Any]]:
    arguments: dict[str, Any] = {
            "searches": [
                {
                    "query": query,
                    "entity": entity,
                    "aliases": aliases or [],
                    "dimension": dimension,
                }
            ],
            "task_id": task_id,
        }
    if time_index is not None:
        arguments["time_token"] = f"time-{time_index}"
    return ("public_web_search", arguments)


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
                        tool_calls=[{"name": name, "args": args, "id": f"call-{self._index}"}],
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
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent
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

    def fake_public_web_explore(
        request: str,
        time_token: str,
        queries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        del time_token
        calls.append(("public_web_explore", request, "explore"))
        result = _fake_research_result(request, request, "explore", search_id="search-explore-1")
        result["request"] = request
        result["query_plan"] = queries or [{"query": request, "purpose": "direct request"}]
        result["queries"] = [str(item["query"]) for item in result["query_plan"]]
        return result

    def fake_public_web_search(
        searches: list[dict[str, Any]],
        task_id: str,
        time_token: str,
        authority_bindings: list[dict[str, Any]] | None = None,
        verification_method: str = "",
    ) -> dict[str, Any]:
        del time_token, authority_bindings, verification_method
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
        if resolution == "timeout":
            raise TimeoutError("search request timed out")
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
            bindings.append(ToolBinding(spec=dict(binding.spec), handler=fake_public_web_research))
        elif binding.spec["name"] == "public_web_search":
            bindings.append(ToolBinding(spec=dict(binding.spec), handler=fake_public_web_search))
        elif binding.spec["name"] == "public_web_explore":
            bindings.append(ToolBinding(spec=dict(binding.spec), handler=fake_public_web_explore))
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
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent

    assert [workflow.id for workflow in agent.workflows] == [
        "deep_research",
        "quick_lookup",
        "reject_unsupported",
        "research_dimension",
    ]
    assert [item.id for item in agent.completion_validators] == ["research-task-graph-result"]
    assert [item.id for item in agent.child_templates] == ["research-dimension"]
    assert {binding.spec["name"] for binding in agent.tools} == {
        "get_current_time",
        "public_web_research",
        "public_web_explore",
        "public_web_search",
        "initialize_deep_research",
        "verify_claim_evidence",
        "record_research_finding",
        "build_evidence_graph",
        "reject_research_request",
    }
    research_tool = next(item for item in agent.tools if item.spec["name"] == "public_web_research")
    assert research_tool.spec["max_calls_per_node"] == 6
    discovery_tool = next(item for item in agent.tools if item.spec["name"] == "public_web_search")
    assert discovery_tool.spec["max_calls_per_task"] == 2
    assert "max_calls_per_node" not in discovery_tool.spec
    verify_tool = next(item for item in agent.tools if item.spec["name"] == "verify_claim_evidence")
    assert "max_calls_per_task" not in verify_tool.spec
    assert "max_calls_per_node" not in verify_tool.spec

    quick = next(item for item in agent.workflows if item.id == "quick_lookup")
    assert "仔细搜寻" in quick.description
    assert "不得选择" in quick.description
    assert quick.start_node == "current_time"
    assert quick.node("current_time").execution == "operation"
    assert quick.node("current_time").operation == "get_current_time"
    assert quick.node("search").execution == "operation"
    assert quick.node("search").operation == "public_web_research"
    assert quick.node("answer").execution == "autonomous"
    assert quick.node("answer").capability_tools == ()

    deep = next(item for item in agent.workflows if item.id == "deep_research")
    assert "仔细搜寻" in deep.description
    assert "必须选择" in deep.description
    assert deep.start_node == "understand"
    assert deep.node("understand").transitions["completed"] == "current_time"
    assert deep.node("current_time").transitions["completed"] == "explore"
    assert [
        deep.node(node_id).execution
        for node_id in (
            "current_time",
            "understand",
            "explore",
            "map_research",
            "initialize",
            "investigate",
            "synthesize",
            "finalize_report",
        )
    ] == [
        "operation",
        "autonomous",
        "operation",
        "autonomous",
        "operation",
        "task_graph",
        "autonomous",
        "operation",
    ]
    assert deep.node("investigate").task_graph is not None
    assert deep.node("investigate").task_graph.child_templates == ("research-dimension",)
    assert all(node.completion_review != "required" for node in deep.nodes)
    brief_schema = deep.node("understand").completion_output_schema["properties"][
        "research_brief"
    ]["properties"]
    assert all(
        "maxItems" not in brief_schema[field]
        for field in ("entities", "constraints", "material_ambiguities")
    )
    assert deep.node("map_research").max_steps == 2
    assert "required" not in deep.node("map_research").completion_output_schema
    assert deep.node("finalize_report").operation == "build_evidence_graph"
    assert deep.node("finalize_report").inputs["report"] == {"$ref": "#/nodes/synthesize/output"}
    assert deep.node("synthesize").transitions["failed"] == "finalize_fallback"
    assert deep.node("finalize_fallback").operation == "build_evidence_graph"

    reject = next(item for item in agent.workflows if item.id == "reject_unsupported")
    assert reject.node("reject").operation == "reject_research_request"

    dimension = next(item for item in agent.workflows if item.id == "research_dimension")
    assert dimension.start_node == "research"
    assert [dimension.node(node_id).execution for node_id in ("research", "commit_finding")] == [
        "autonomous",
        "operation",
    ]
    assert dimension.node("research").capability_tools == ("public_web_search",)
    assert dimension.node("research").max_steps == 8
    assert dimension.node("commit_finding").operation == "record_research_finding"
    assert "evidence" not in dimension.node("commit_finding").inputs
    assert dimension.node("commit_finding").inputs["task_id"] == {
        "$ref": "#/workflow/input/context_manifest/extensions/research_task/id"
    }
    finding_schema = dimension.node("research").completion_output_schema["properties"]["finding"]
    assert finding_schema["required"] == ("conclusion",)
    assert "evidence" not in finding_schema["properties"]
    assert "status" not in finding_schema["required"]
    assert "status" not in dimension.node("commit_finding").inputs


def test_research_dimension_commits_selected_search_sources(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "dimensions"
    conclusion = "Tesla Model Y 与小米 YU7 的车身尺寸已有公开来源。"
    model = _ScriptedResearchModel(
        [
            _search_call(
                task_id,
                '"Tesla Model Y" 2026 车身尺寸 轴距',
                entity="Tesla Model Y",
                aliases=["Model Y", "Tesla ModelY", "特斯拉 Model Y"],
                dimension="车身尺寸与轴距",
            ),
            _search_call(
                task_id,
                '"小米 YU7" 2026 车身尺寸 轴距',
                entity="小米 YU7",
                aliases=["小米YU7", "Xiaomi YU7", "小米YU"],
                dimension="车身尺寸与轴距",
            ),
            (
                "complete_node",
                {"finding": {"conclusion": conclusion}},
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
    assert response["output"]["verification_id"].startswith("search:")
    assert response["output"]["conclusion"] == conclusion
    assert response["output"]["implications"] == conclusion
    assert list(response["output"]["citations"]) == [_SOURCE_URL]
    evidence = next(iter(response["output"]["evidence"]))
    assert evidence["source_url"] == _SOURCE_URL
    assert evidence["excerpt"].startswith("Public information about")
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
        "get_current_time",
        "public_web_search",
        "record_research_finding",
    ]
    assert model._index == 3


def test_research_dimension_derives_status_after_follow_up_search_timeout(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "international-vla"
    conclusion = "Skild AI 和 TRI 正在推动不同的通用机器人模型路线。"
    draft = _dimension_finding_draft(
        task_id,
        "哪些国际企业在推动 VLA 或通用机器人基础模型?",
        conclusion,
        source_urls=[_SOURCE_URL],
    )
    del draft["finding"]["status"]
    model = _ScriptedResearchModel(
        [
            _search_call(task_id, "Skild AI robotic brain"),
            _search_call(task_id, "TRI large behavior model"),
            ("complete_node", draft),
        ]
    )
    session, agent = _session(
        tmp_path,
        model,
        calls,
        search_resolution=["sourced", "timeout"],
    )

    response = session.run_task(
        agent=agent.name,
        workflow_id="research_dimension",
        input={
            "context_manifest": {
                "extensions": {
                    "research_task": {
                        "id": task_id,
                        "question": "哪些国际企业在推动 VLA 或通用机器人基础模型?",
                        "verification_method": "single_source_sufficient",
                        "authority_bindings": [],
                    }
                }
            }
        },
        thread_id="research-dimension-derived-status-after-timeout",
    )

    assert response["status"] == "completed", response
    assert response["output"]["status"] == "sourced"
    assert response["output"]["conclusion"] == conclusion
    assert list(response["output"]["citations"]) == [_SOURCE_URL]
    assert model._index == 3
    assert len(calls) == 2


def test_research_dimension_stops_after_two_search_timeouts_and_blocks(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "vla-timeout"
    draft = _dimension_finding_draft(
        task_id,
        "端到端 VLA 模型有哪些最新进展?",
        "本次检索超时, 无法补充可靠的最新进展。",
        source_urls=[],
    )
    del draft["finding"]["status"]
    model = _ScriptedResearchModel(
        [
            _search_call(task_id, "VLA model progress"),
            _search_call(task_id, "VLA model institutions"),
            ("complete_node", draft),
        ]
    )
    session, agent = _session(
        tmp_path,
        model,
        calls,
        search_resolution=["timeout", "timeout"],
    )

    response = session.run_task(
        agent=agent.name,
        workflow_id="research_dimension",
        input={
            "context_manifest": {
                "extensions": {
                    "research_task": {
                        "id": task_id,
                        "question": "端到端 VLA 模型有哪些最新进展?",
                        "verification_method": "single_source_sufficient",
                        "authority_bindings": [],
                    }
                }
            }
        },
        thread_id="research-dimension-two-search-timeouts",
    )

    assert response["status"] == "completed", response
    assert response["output"]["status"] == "blocked"
    assert response["output"]["citations"] == ()
    assert response["output"]["limitations"]
    assert model._index == 3
    assert len(calls) == 2


def test_research_dimension_derives_blocked_status_without_usable_sources(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "unresolved-company"
    draft = _dimension_finding_draft(
        task_id,
        "这家公司有哪些可靠公开信息?",
        "本次检索未获得足以确认该公司情况的公开资料。",
        source_urls=[],
    )
    del draft["finding"]["status"]
    model = _ScriptedResearchModel(
        [
            _search_call(task_id, "company official information"),
            ("complete_node", draft),
        ]
    )
    session, agent = _session(
        tmp_path,
        model,
        calls,
        search_resolution="no_evidence",
    )

    response = session.run_task(
        agent=agent.name,
        workflow_id="research_dimension",
        input={
            "context_manifest": {
                "extensions": {
                    "research_task": {
                        "id": task_id,
                        "question": "这家公司有哪些可靠公开信息?",
                        "verification_method": "single_source_sufficient",
                        "authority_bindings": [],
                    }
                }
            }
        },
        thread_id="research-dimension-derived-blocked-status",
    )

    assert response["status"] == "completed", response
    assert response["output"]["status"] == "blocked"
    assert response["output"]["citations"] == ()
    assert response["output"]["limitations"]


def test_research_dimension_derives_unverifiable_status_and_limitation(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "private-information"
    draft = _dimension_finding_draft(
        task_id,
        "这个人的非公开经历是什么?",
        "该问题超出可公开验证的信息范围。",
        source_urls=[],
    )
    del draft["finding"]["status"]
    model = _ScriptedResearchModel([("complete_node", draft)])
    session, agent = _session(tmp_path, model, calls)

    response = session.run_task(
        agent=agent.name,
        workflow_id="research_dimension",
        input={
            "context_manifest": {
                "extensions": {
                    "research_task": {
                        "id": task_id,
                        "question": "这个人的非公开经历是什么?",
                        "verification_method": "unverifiable_flag",
                        "authority_bindings": [],
                    }
                }
            }
        },
        thread_id="research-dimension-derived-unverifiable-status",
    )

    assert response["status"] == "completed", response
    assert response["output"]["status"] == "blocked"
    assert response["output"]["limitations"] == (
        "该问题无法通过公开来源进行可靠验证。",
    )
    assert calls == []


def test_research_dimension_commits_no_source_draft_as_blocked(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "complete_node",
                {"finding": {"conclusion": "当前没有可用于支持该问题的公开来源。"}},
            )
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
                        "id": "no-public-source",
                        "question": "这个公开问题目前能否得到回答?",
                        "verification_method": "single_source_sufficient",
                        "authority_bindings": [],
                    }
                }
            }
        },
        thread_id="research-dimension-empty-source-draft",
    )

    assert response["status"] == "completed", response
    assert response["output"]["status"] == "blocked"
    assert response["output"]["citations"] == ()
    assert response["output"]["limitations"]
    assert calls == []


def test_research_dimension_combines_shared_and_task_search_sources(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "data-strategies"
    shared_url = "https://shared.example.test/exploration"
    conclusion = "探索资料和定向搜索共同说明了两条数据采集路线。"
    draft = _dimension_finding_draft(
        task_id,
        "具身智能企业采用了哪些数据策略?",
        conclusion,
        source_urls=[shared_url, _SOURCE_URL],
    )
    del draft["finding"]["status"]
    model = _ScriptedResearchModel(
        [
            _search_call(task_id, "embodied AI data strategy"),
            ("complete_node", draft),
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
                        "question": "具身智能企业采用了哪些数据策略?",
                        "verification_method": "single_source_sufficient",
                        "authority_bindings": [],
                    },
                    "research_context": {
                        "exploration_sources": [
                            {
                                "url": shared_url,
                                "title": "具身智能数据策略综述",
                                "excerpt": "探索阶段发现了低成本数据采集路线。",
                            }
                        ],
                        "committed_results": [],
                        "exploration_time": {
                            "issued_at": "2026-07-23T01:00:00Z",
                            "current_date": "2026-07-23",
                            "timezone": "Asia/Shanghai",
                        },
                    },
                }
            }
        },
        thread_id="mixed-shared-and-task-search-sources",
    )

    assert response["status"] == "completed", response
    assert response["output"]["status"] == "sourced"
    assert set(response["output"]["citations"]) == {_SOURCE_URL, shared_url}
    provenance = response["output"]["provenance"]
    assert len(provenance["search_ids"]) == 2
    assert any(str(item).startswith("shared:") for item in provenance["search_ids"])
    assert {item["search_id"] for item in provenance["searches"]} == set(
        provenance["search_ids"]
    )


def test_research_dimension_reuses_manifest_source_without_network_search(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    task_id = "roles"
    conclusion = "探索来源已经足以说明杭州存在公开招聘的 AI 岗位。"
    model = _ScriptedResearchModel(
        [
            (
                "complete_node",
                _dimension_finding_draft(
                    task_id,
                    "哪些 AI 岗位正在招聘?",
                    conclusion,
                    source_urls=[_SOURCE_URL],
                ),
            )
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
                        "question": "哪些 AI 岗位正在招聘?",
                        "verification_method": "single_source_sufficient",
                        "authority_bindings": [],
                    },
                    "research_context": {
                        "exploration_sources": [
                            {
                                "url": _SOURCE_URL,
                                "title": "杭州 AI 招聘",
                                "excerpt": "杭州企业发布了 AI 相关岗位。",
                            }
                        ],
                        "committed_results": [],
                        "exploration_time": {
                            "issued_at": "2026-07-22T01:00:00Z",
                            "current_date": "2026-07-22",
                            "timezone": "Asia/Shanghai",
                        },
                    },
                }
            }
        },
        thread_id="reuse-shared-research-source",
    )

    assert response["status"] == "completed", response
    assert response["output"]["conclusion"] == conclusion
    assert response["output"]["provenance"]["search_ids"][0].startswith("shared:")
    assert response["output"]["provenance"]["searches"][0]["current_time"] == {
        "issued_at": "2026-07-22T01:00:00Z",
        "current_date": "2026-07-22",
        "timezone": "Asia/Shanghai",
    }
    assert calls == []


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
    assert response["output"]["executive_summary"] == ("中控技术提供工业自动化相关产品和服务。")
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


def test_vague_deep_research_clarifies_before_unfocused_exploration(tmp_path: Path) -> None:
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


def test_clear_deep_research_searches_before_mapping_without_scope_review(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    model = _ScriptedResearchModel(
        [
            (
                "route__deep_research",
                {"request": "the AI job market in Hangzhou", "subject": "杭州 AI 就业"},
            ),
            _research_brief_call(
                "the AI job market in Hangzhou",
                entities=["杭州 AI 就业市场"],
            ),
            _research_map_call(
                subject="杭州 AI 就业市场",
                task_id="roles",
                title="招聘岗位",
                question="哪些 AI 岗位正在招聘?",
            ),
            ("get_current_time", {}),
            _search_call("roles", "杭州 AI 岗位招聘", time_index=2),
            (
                "complete_node",
                _dimension_finding_draft(
                    "roles",
                    "哪些 AI 岗位正在招聘?",
                    "公开招聘信息显示杭州存在 AI 相关岗位。",
                    source_urls=[_SOURCE_URL],
                ),
            ),
            (
                "complete_node",
                {"direct_answer": "杭州存在公开招聘的 AI 岗位。", "limitations": []},
            ),
        ]
    )
    session, agent = _session(tmp_path, model, calls)

    response = session.run_task(
        agent=agent.name,
        input={"prompt": "the AI job market in Hangzhou"},
        thread_id="single-scope-review",
    )

    assert response["status"] == "completed", response
    assert calls[0] == (
        "public_web_explore",
        "the AI job market in Hangzhou",
        "explore",
    )
    assert model._index == 7
    requested = [
        event
        for event in session.get_trace("single-scope-review")
        if event["event_type"] == "interaction_requested"
    ]
    assert requested == []
