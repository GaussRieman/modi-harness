"""Trusted public Web research Operation tests."""

from __future__ import annotations

import datetime
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

import pytest

from modi_harness._utils import compute_fingerprint
from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]
_EMPTY_AUTHORITY_FINGERPRINT = "sha256:" + compute_fingerprint([])


@pytest.fixture(autouse=True)
def _disable_paid_search_for_legacy_provider_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep provider-count assertions independent of a developer's local .env."""

    import modi_harness.config.settings as settings_module

    collect_env = settings_module._collect_env

    def collect_without_doubao(env_file: str | Path | None) -> dict[str, str]:
        values = collect_env(env_file)
        values.pop("DOUBAO_SEARCH_API_KEY", None)
        return values

    monkeypatch.setattr(settings_module, "_collect_env", collect_without_doubao)
    module = _research_module()
    module._reset_search_provider_health()
    yield
    module._reset_search_provider_health()


def _tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent
    binding = next(item for item in agent.tools if item.spec["name"] == "public_web_research")
    return cast(Callable[..., dict[str, Any]], binding.handler)


def _discovery_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent
    binding = next(item for item in agent.tools if item.spec["name"] == "public_web_search")
    return cast(Callable[..., dict[str, Any]], binding.handler)


def _time_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent
    binding = next(item for item in agent.tools if item.spec["name"] == "get_current_time")
    return cast(Callable[..., dict[str, Any]], binding.handler)


def _verify_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent
    binding = next(item for item in agent.tools if item.spec["name"] == "verify_claim_evidence")
    handler = cast(Callable[..., dict[str, Any]], binding.handler)

    def call(**kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("authority_bindings", [])
        return handler(**kwargs)

    return call


def _search_item(
    query: str,
    *,
    entity: str = "Unitree Robotics",
    aliases: list[str] | None = None,
    dimension: str = "company research",
) -> dict[str, Any]:
    return {
        "query": query,
        "entity": entity,
        "aliases": aliases or [],
        "dimension": dimension,
    }


def _finding_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent
    binding = next(item for item in agent.tools if item.spec["name"] == "record_research_finding")
    handler = cast(Callable[..., dict[str, Any]], binding.handler)

    def call(**kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("verified_claim", kwargs.get("conclusion", ""))
        kwargs.setdefault("authority_binding_fingerprint", _EMPTY_AUTHORITY_FINGERPRINT)
        return handler(**kwargs)

    return call


def _graph_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve("research-assistant").agent
    binding = next(item for item in agent.tools if item.spec["name"] == "build_evidence_graph")
    return cast(Callable[..., dict[str, Any]], binding.handler)


def test_structured_search_allows_distinct_queries_for_one_broad_entity() -> None:
    normalized = _research_module()._normalize_search_intents(
        [
            _search_item(
                "优必选 工厂部署",
                entity="工业装配与制造",
                dimension="优必选商业部署",
            ),
            _search_item(
                "Figure AI BMW 工厂部署",
                entity="工业装配与制造",
                dimension="Figure AI商业部署",
            ),
        ]
    )

    assert len(normalized) == 2


def test_structured_search_rejects_exact_duplicate_intents() -> None:
    item = _search_item(
        "优必选 工厂部署",
        entity="工业装配与制造",
        dimension="商业部署",
    )

    with pytest.raises(ValueError, match="search intents must be distinct"):
        _research_module()._normalize_search_intents([item, dict(item)])


def _research_module() -> ModuleType:
    return sys.modules[_tool().__module__]


def _confidence_module() -> ModuleType:
    # research.py does `from .. import confidence`, so the loaded research
    # module already carries a bound reference to it under this discovery.
    return cast(ModuleType, _research_module().confidence)


def _record(
    provider: str,
    query: str,
    results: list[dict[str, str]],
) -> dict[str, Any]:
    if provider == "bing_rss":
        search_url = "https://www.bing.com/search?" + urllib.parse.urlencode(
            {"q": query, "format": "rss"}
        )
    elif provider == "baidu":
        search_url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
    else:
        search_url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    return {
        "provider": provider,
        "query": query,
        "search_url": search_url,
        "status": "ok" if results else "empty",
        "results": results,
        "error": None,
    }


def test_public_web_research_merges_ranks_and_fetches_strong_matches() -> None:
    research_tools = _research_module()

    def search(provider: str, query: str) -> dict[str, Any]:
        if provider == "bing_rss":
            results = [
                {
                    "title": "杭州拉格朗日具身智能科技有限公司 - 企业信息",
                    "url": "https://example.test/company",
                    "snippet": "机器人与具身智能技术公司",
                },
                {
                    "title": "拉格朗日乘数法词典释义",
                    "url": "https://dictionary.test/lagrange",
                    "snippet": "数学知识",
                },
            ]
        elif provider == "baidu":
            results = [
                {
                    "title": "杭州拉格朗日具身智能科技招聘",
                    "url": "https://jobs.test/lagrange",
                    "snippet": "公司招聘机器人算法工程师",
                }
            ]
        else:
            results = []
        return _record(provider, query, results)

    def fetch(url: str) -> dict[str, Any]:
        return {
            "requested_url": url,
            "url": url,
            "title": "Company source",
            "content_excerpt": "Public company and technology evidence " * 10,
            "usable": True,
            "error": None,
        }

    with (
        patch.object(research_tools, "_search_provider", side_effect=search) as search_mock,
        patch.object(research_tools, "_fetch_source", side_effect=fetch) as fetch_mock,
    ):
        result = _tool()(
            "杭州拉格朗日具身智能科技",
            question="公司的技术实力如何",
        )

    assert search_mock.call_count == 6
    assert 1 <= fetch_mock.call_count <= 12
    assert len(result["search_records"]) == 6
    assert len(result["sources"]) == 2
    assert all(item["usable"] for item in result["sources"])
    assert not any("does not prove" in item for item in result["limitations"])
    urls = {item["url"] for item in result["candidates"]}
    assert "https://example.test/company" in urls
    assert "https://jobs.test/lagrange" in urls
    assert "https://dictionary.test/lagrange" not in urls


def test_public_web_research_recovers_one_character_subject_typo_from_question() -> None:
    research_tools = _research_module()
    seen_queries: list[str] = []

    def search(provider: str, query: str) -> dict[str, Any]:
        seen_queries.append(query)
        results = []
        if "具身智能" in query:
            results = [
                {
                    "title": "杭州拉格朗日具身技术有限公司 - 企业信息",
                    "url": "https://example.test/lagrange-embodied",
                    "snippet": "公司聚焦具身智能和机器人系统。",
                }
            ]
        return _record(provider, query, results)

    def fetch(url: str) -> dict[str, Any]:
        return {
            "requested_url": url,
            "url": url,
            "title": "杭州拉格朗日具身技术有限公司",
            "content_excerpt": "公司的公开工商与业务信息。" * 20,
            "usable": True,
            "error": None,
        }

    with (
        patch.object(research_tools, "_search_provider", side_effect=search),
        patch.object(research_tools, "_fetch_source", side_effect=fetch),
    ):
        result = _tool()(
            "拉格朗日具身只能公司",
            question="拉格朗日具身智能公司的基本公开信息",
        )

    assert result["queries"] == [
        "拉格朗日具身只能公司",
        "拉格朗日具身智能公司",
    ]
    assert "拉格朗日具身智能公司" in seen_queries
    assert [item["url"] for item in result["sources"]] == ["https://example.test/lagrange-embodied"]
    assert not any("no result" in item for item in result["limitations"])


def test_public_web_research_matches_bilingual_concept_subject() -> None:
    research_tools = _research_module()
    seen_queries: list[str] = []

    def search(provider: str, query: str) -> dict[str, Any]:
        seen_queries.append(query)
        return _record(
            provider,
            query,
            [
                {
                    "title": "具身智能: 定义、发展与应用",
                    "url": "https://academic.test/embodied-ai",
                    "snippet": "具身智能是智能体通过身体与环境交互进行感知和行动。",
                }
            ],
        )

    def fetch(url: str) -> dict[str, Any]:
        return {
            "requested_url": url,
            "url": url,
            "title": "具身智能: 定义、发展与应用",
            "content_excerpt": "具身智能通过身体与环境交互形成感知、决策和行动闭环。" * 10,
            "usable": True,
            "error": None,
        }

    with (
        patch.object(research_tools, "_search_provider", side_effect=search),
        patch.object(research_tools, "_fetch_source", side_effect=fetch),
    ):
        result = _tool()(
            "具身智能 (Embodied Intelligence/AI)",
            question="具身智能是什么? 请解释其定义、核心概念和基本内涵。",
        )

    assert result["queries"] == [
        "具身智能 (Embodied Intelligence/AI)",
        "具身智能",
    ]
    assert "具身智能" in seen_queries
    assert [item["url"] for item in result["sources"]] == ["https://academic.test/embodied-ai"]
    assert not any("no result" in item for item in result["limitations"])


def test_bilingual_subject_variants_expand_parenthetical_slash_aliases() -> None:
    variants = _research_module()._subject_identity_variants(
        "具身智能 (Embodied Intelligence/AI)",
        "具身智能是什么?",
    )

    assert variants == [
        "具身智能 (Embodied Intelligence/AI)",
        "具身智能",
        "Embodied Intelligence",
        "Embodied AI",
    ]


def test_public_web_explore_keeps_its_stable_two_argument_contract() -> None:
    research_tools = _research_module()
    seen_queries: list[str] = []

    def search(provider: str, query: str) -> dict[str, Any]:
        seen_queries.append(query)
        return _record(provider, query, [])

    with patch.object(research_tools, "_search_provider", side_effect=search):
        result = research_tools.public_web_explore(
            "拉格朗日具身智能公司的基本公开信息",
            "time-token",
        )

    assert result["queries"] == ["拉格朗日具身智能公司的基本公开信息"]
    assert set(seen_queries) == set(result["queries"])


def test_public_web_explore_runs_complementary_query_plan() -> None:
    research_tools = _research_module()
    query_plan = [
        {"query": "具身智能 产业链 全景", "purpose": "direct overview"},
        {"query": "具身智能 上游 核心零部件", "purpose": "upstream"},
        {"query": "具身智能 下游 应用场景", "purpose": "downstream"},
        {"query": "具身智能 商业化 瓶颈", "purpose": "commercialization"},
    ]
    seen_queries: list[str] = []

    def search(provider: str, query: str) -> dict[str, Any]:
        seen_queries.append(query)
        return _record(provider, query, [])

    with patch.object(research_tools, "_search_provider", side_effect=search):
        result = research_tools.public_web_explore(
            "具身智能的完整产业链是什么?",
            "time-token",
            queries=query_plan,
        )

    assert result["query_plan"] == query_plan
    assert result["summary"]["query_count"] == 4
    assert set(seen_queries) == {item["query"] for item in query_plan}


def test_run_searches_returns_completed_records_at_deadline() -> None:
    research_tools = _research_module()

    def search(provider: str, query: str) -> dict[str, Any]:
        if provider == "baidu":
            time.sleep(0.2)
        return _record(provider, query, [])

    started = time.monotonic()
    with (
        patch.object(research_tools, "_active_providers", return_value=("bing_rss", "baidu")),
        patch.object(research_tools, "_search_provider", side_effect=search),
    ):
        records = research_tools._run_searches(
            ["赵立晨 李博杰 对比"],
            deadline=started + 0.05,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert next(item for item in records if item["provider"] == "bing_rss")["status"] == "empty"
    timed_out = next(item for item in records if item["provider"] == "baidu")
    assert timed_out["status"] == "failed"
    assert timed_out["error"] == research_tools._EXPLORATION_DEADLINE_ERROR
    assert research_tools._provider_circuit_is_open("baidu") is False


def test_public_web_explore_returns_partial_sources_after_deadline() -> None:
    research_tools = _research_module()
    request = "对比赵立晨和李博杰"
    candidate_url = "https://example.test/people-comparison"
    search_records = [
        {
            **_record(
                "bing_rss",
                request,
                [
                    {
                        "title": "对比赵立晨和李博杰: 公开资料",
                        "url": candidate_url,
                        "snippet": "两人的公开教育与职业经历对比。",
                    }
                ],
            ),
            "query_index": 0,
        },
        {
            **_record("baidu", request, []),
            "query_index": 0,
            "status": "failed",
            "error": research_tools._EXPLORATION_DEADLINE_ERROR,
        },
    ]

    def fetch(candidates: list[dict[str, Any]], **_: Any) -> list[dict[str, Any]]:
        assert candidates[0]["url"] == candidate_url
        return [
            {
                "requested_url": candidate_url,
                "url": candidate_url,
                "title": "对比赵立晨和李博杰: 公开资料",
                "content_type": "text/html",
                "content_excerpt": "赵立晨与李博杰的教育、研究方向和职业经历公开资料。" * 20,
                "usable": True,
                "error": None,
                "search_index": 0,
                "providers": ["bing_rss"],
                "score": 20,
            }
        ]

    with (
        patch.object(research_tools, "_run_searches", return_value=search_records),
        patch.object(research_tools, "_fetch_candidates", side_effect=fetch),
    ):
        result = research_tools.public_web_explore(request, "time-token")

    assert [item["url"] for item in result["sources"]] == [candidate_url]
    assert research_tools._EXPLORATION_DEADLINE_ERROR in result["limitations"]
    assert not any(item.startswith("search provider failures") for item in result["limitations"])


def test_public_web_explore_falls_back_when_search_step_times_out() -> None:
    research_tools = _research_module()
    query_plan = [
        {"query": f"对比赵立晨和李博杰 方向 {index}", "purpose": f"方向 {index}"}
        for index in range(4)
    ]

    with (
        patch.object(
            research_tools,
            "_run_searches",
            side_effect=TimeoutError("search batch timed out"),
        ),
        patch.object(research_tools, "_fetch_candidates") as fetch_mock,
    ):
        result = research_tools.public_web_explore(
            "对比赵立晨和李博杰",
            "time-token",
            queries=query_plan,
        )

    fetch_mock.assert_not_called()
    assert result["query_plan"] == query_plan
    assert result["sources"] == []
    assert result["summary"]["candidate_count_by_query"] == [0, 0, 0, 0]
    assert research_tools._EXPLORATION_DEADLINE_ERROR in result["limitations"]
    assert result["operation_summary"]["task_id"] == "explore"


def test_public_web_explore_falls_back_when_fetch_step_times_out() -> None:
    research_tools = _research_module()
    request = "对比赵立晨和李博杰"
    candidate_url = "https://example.test/people-comparison"
    search_records = [
        {
            **_record(
                "bing_rss",
                request,
                [
                    {
                        "title": "对比赵立晨和李博杰: 公开资料",
                        "url": candidate_url,
                        "snippet": "两人的公开教育与职业经历对比。",
                    }
                ],
            ),
            "query_index": 0,
        }
    ]

    with (
        patch.object(research_tools, "_run_searches", return_value=search_records),
        patch.object(
            research_tools,
            "_fetch_candidates",
            side_effect=TimeoutError("fetch batch timed out"),
        ),
    ):
        result = research_tools.public_web_explore(request, "time-token")

    assert [item["url"] for item in result["candidates"]] == [candidate_url]
    assert result["sources"] == []
    assert result["fetch_records"][0]["url"] == candidate_url
    assert result["fetch_records"][0]["error"] == research_tools._EXPLORATION_DEADLINE_ERROR
    assert research_tools._EXPLORATION_DEADLINE_ERROR in result["limitations"]


def _deep_research_fixture(request: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    brief = {
        "original_request": request,
        "objective": request,
        "task_type": "landscape",
        "entities": ["具身智能"],
        "freshness": "current_or_unspecified",
        "constraints": [],
        "material_ambiguities": [],
    }
    exploration = {
        "search_id": "search-explore",
        "query_plan": [
            {"query": "具身智能产业链", "purpose": "overview"},
            {"query": "具身智能上游", "purpose": "upstream"},
            {"query": "具身智能下游", "purpose": "downstream"},
            {"query": "具身智能商业化", "purpose": "commercialization"},
        ],
        "sources": [],
        "limitations": [],
    }
    research_map = {
        "subject": "具身智能产业链",
        "landscape_map": {
            "summary": "已定位产业链资料。",
            "themes": [],
            "early_conflicts": [],
            "unresolved_terms": [],
        },
        "coverage_map": {
            "items": [
                {
                    "id": "whole-chain",
                    "label": "上游、中游、下游与支撑生态",
                    "question": "各环节分别有哪些技术和参与者?",
                    "rationale": "建立完整产业链结构。",
                    "required": True,
                    "status": "partial",
                },
                {
                    "id": "market",
                    "label": "龙头企业、竞争格局、商业化进展与瓶颈",
                    "question": "龙头企业如何竞争、落地并受到哪些瓶颈制约?",
                    "rationale": "判断产业成熟度。",
                    "required": True,
                    "status": "unexplored",
                },
            ]
        },
        "tasks": [
            {
                "id": "chain",
                "title": "产业链结构",
                "question": "上游、中游、下游与支撑生态分别是什么?",
                "rationale": "补齐产业结构。",
                "information_gap": "各环节及其关系。",
                "coverage_ids": ["whole-chain"],
                "entities": [{"name": "具身智能", "aliases": []}],
                "priority": 90,
            },
            {
                "id": "market",
                "title": "竞争与商业化",
                "question": "竞争格局、商业化进展与瓶颈是什么?",
                "rationale": "补齐产业状态。",
                "information_gap": "竞争、商业化和瓶颈。",
                "coverage_ids": ["market"],
                "entities": [{"name": "具身智能", "aliases": []}],
                "priority": 85,
            },
        ],
    }
    return brief, exploration, research_map


def test_initialize_deep_research_rejects_rewritten_original_request() -> None:
    research_tools = _research_module()
    request = "具身智能的完整产业链是什么?"
    brief, exploration, research_map = _deep_research_fixture(request)
    brief["original_request"] = "只研究上游零部件"

    with pytest.raises(ValueError, match="exactly preserve request"):
        research_tools.initialize_deep_research(
            request,
            brief,
            exploration,
            research_map,
        )


def test_complete_industry_chain_fills_missing_downstream_coverage() -> None:
    research_tools = _research_module()
    request = "具身智能的完整产业链是什么?"
    brief, exploration, research_map = _deep_research_fixture(request)
    research_map["coverage_map"]["items"][0]["label"] = "上游、中游与支撑生态"
    research_map["coverage_map"]["items"][0]["question"] = "上游和中游有哪些参与者?"

    result = research_tools.initialize_deep_research(
        request,
        brief,
        exploration,
        research_map,
    )

    coverage = result["research_context"]["coverage_map"]["items"]
    coverage_text = " ".join(f"{item['label']} {item['question']}" for item in coverage)
    assert "下游" in coverage_text
    assert "龙头" in coverage_text


def test_initialize_deep_research_enriches_compact_model_map() -> None:
    research_tools = _research_module()
    request = "给我完整的具身智能产业链, 尤其要有龙头企业"
    brief, exploration, _ = _deep_research_fixture(request)
    compact_map = {
        "subject": "具身智能产业链",
        "landscape_summary": "首轮资料覆盖产业概览和部分公司信息。",
        "themes": ["产业结构", "市场参与者"],
        "coverage": [
            {"label": "上游", "question": "核心零部件和供应商有哪些?"},
            {"label": "龙头企业", "question": "各环节龙头企业有哪些?"},
        ],
        "tasks": [
            {
                "title": "上游和龙头企业",
                "question": "搜索核心零部件及各环节龙头企业",
                "coverage_labels": ["上游", "龙头企业"],
            }
        ],
    }

    result = research_tools.initialize_deep_research(
        request,
        brief,
        exploration,
        compact_map,
    )

    context = result["research_context"]
    coverage = context["coverage_map"]["items"]
    coverage_text = " ".join(f"{item['label']} {item['question']}" for item in coverage)
    for required_term in ("上游", "中游", "下游", "支撑生态", "龙头", "商业化", "瓶颈"):
        assert required_term in coverage_text
    assert 1 <= len(context["task_map"]) <= 4
    assigned = {coverage_id for task in context["task_map"] for coverage_id in task["coverage_ids"]}
    assert {item["id"] for item in coverage if item["required"]} <= assigned


def test_initialize_deep_research_falls_back_from_empty_model_map() -> None:
    research_tools = _research_module()
    request = "给我完整的具身智能产业链, 尤其要有龙头企业"
    brief, exploration, _ = _deep_research_fixture(request)

    result = research_tools.initialize_deep_research(
        request,
        brief,
        exploration,
        {},
    )

    assert result["operation_summary"]["coverage_count"] >= 7
    assert 1 <= result["operation_summary"]["task_count"] <= 4
    assert any(
        "龙头" in item["label"] for item in result["research_context"]["coverage_map"]["items"]
    )


def test_coverage_map_remains_distinct_from_task_graph_seed() -> None:
    research_tools = _research_module()
    request = "具身智能的完整产业链是什么?"
    brief, exploration, research_map = _deep_research_fixture(request)

    result = research_tools.initialize_deep_research(
        request,
        brief,
        exploration,
        research_map,
    )

    planning_context = result["intent"]["planning_context"]
    assert planning_context["coverage_map"] == research_map["coverage_map"]
    assert planning_context["candidate_dimensions"] != planning_context["coverage_map"]["items"]
    assert planning_context["candidate_dimensions"][0]["coverage_ids"] == ["whole-chain"]
    assert result["research_context"]["coverage_map"] == research_map["coverage_map"]
    assert result["research_context"]["task_map"][0]["coverage_ids"] == ["whole-chain"]


def test_initialize_deep_research_preserves_explicit_user_constraints() -> None:
    research_tools = _research_module()
    request = "具身智能的完整产业链是什么?"
    brief, exploration, research_map = _deep_research_fixture(request)
    brief["constraints"] = ["只使用中文和英文资料", "重点关注中国市场"]

    result = research_tools.initialize_deep_research(
        request,
        brief,
        exploration,
        research_map,
    )

    assert result["intent"]["constraints"] == [
        "仅使用公开可访问资料",
        "只使用中文和英文资料",
        "重点关注中国市场",
    ]


def test_public_web_research_does_not_fetch_irrelevant_results() -> None:
    research_tools = _research_module()

    def search(provider: str, query: str) -> dict[str, Any]:
        return _record(
            provider,
            query,
            [
                {
                    "title": "杭州旅游攻略与热门游戏",
                    "url": "https://irrelevant.test/page",
                    "snippet": "景点门票和游戏资讯",
                }
            ],
        )

    with (
        patch.object(research_tools, "_search_provider", side_effect=search),
        patch.object(research_tools, "_fetch_source") as fetch_mock,
    ):
        result = _tool()("威灿科技")

    fetch_mock.assert_not_called()
    assert result["candidates"] == []
    assert result["sources"] == []
    assert any("does not prove" in item for item in result["limitations"])


def test_public_web_research_isolates_provider_failure() -> None:
    research_tools = _research_module()

    def search(provider: str, query: str) -> dict[str, Any]:
        if provider == "baidu":
            raise OSError("provider blocked")
        return _record(provider, query, [])

    with patch.object(research_tools, "_search_provider", side_effect=search):
        result = _tool()("威灿科技")

    failed = [record for record in result["search_records"] if record["error"]]
    assert len(failed) == 2
    assert {record["provider"] for record in failed} == {"baidu"}
    assert any("baidu" in item for item in result["limitations"])


def test_provider_circuit_skips_a_fully_blocked_provider_temporarily() -> None:
    research_tools = _research_module()
    calls: list[str] = []

    def search(provider: str, query: str) -> dict[str, Any]:
        calls.append(provider)
        record = _record(provider, query, [])
        if provider == "baidu":
            record.update(status="blocked", error="access denied")
        return record

    with patch.object(research_tools, "_search_provider", side_effect=search):
        research_tools._run_searches(["first", "second"])
        first_batch_calls = list(calls)
        calls.clear()
        research_tools._run_searches(["third", "fourth"])

    assert first_batch_calls.count("baidu") == 2
    assert "baidu" not in calls
    assert set(calls) == {"bing_rss", "duckduckgo"}


def test_public_web_search_discovers_candidates_without_entity_name_filter() -> None:
    research_tools = _research_module()

    def search(provider: str, query: str) -> dict[str, Any]:
        results = (
            [
                {
                    "title": "Unitree Robotics launches new humanoid platform",
                    "url": "https://unitree.test/humanoid",
                    "snippet": "Hangzhou robotics company building embodied AI systems",
                }
            ]
            if provider == "duckduckgo"
            else []
        )
        return _record(provider, query, results)

    def fetch(url: str) -> dict[str, Any]:
        return {
            "requested_url": url,
            "url": url,
            "title": "Unitree Robotics",
            "content_excerpt": "Hangzhou humanoid robotics and embodied AI evidence " * 20,
            "usable": True,
            "error": None,
        }

    with (
        patch.object(research_tools, "_search_provider", side_effect=search) as search_mock,
        patch.object(research_tools, "_fetch_source", side_effect=fetch) as fetch_mock,
    ):
        result = _discovery_tool()(
            [_search_item("embodied AI companies in Hangzhou")],
            task_id="discover_companies",
            time_token="test-token",
        )

    assert search_mock.call_count == 6
    fetch_mock.assert_called_once_with("https://unitree.test/humanoid")
    assert result["resolution"] == "sourced"
    assert result["sources"][0]["title"] == "Unitree Robotics"


def test_public_web_search_skips_low_value_portal_candidates() -> None:
    research_tools = _research_module()

    def search(provider: str, query: str) -> dict[str, Any]:
        results = (
            [
                {
                    "title": "hao123",
                    "url": "https://www.hao123.com",
                    "snippet": "",
                },
                {
                    "title": "科大讯飞 2025 年度报告摘要",
                    "url": "https://official.test/iflytek-2025",
                    "snippet": "科大讯飞营业收入和归母净利润",
                },
            ]
            if provider == "bing_rss"
            else []
        )
        return _record(provider, query, results)

    def fetch(url: str) -> dict[str, Any]:
        return {
            "requested_url": url,
            "url": url,
            "title": "科大讯飞 2025 年度报告摘要",
            "content_excerpt": "科大讯飞营业收入和归母净利润 " * 20,
            "usable": True,
            "error": None,
        }

    with (
        patch.object(research_tools, "_search_provider", side_effect=search),
        patch.object(research_tools, "_fetch_source", side_effect=fetch) as fetch_mock,
    ):
        result = _discovery_tool()(
            [_search_item("科大讯飞 2025 年报", entity="科大讯飞")],
            task_id="financial",
            time_token="test-token",
        )

    fetch_mock.assert_called_once_with("https://official.test/iflytek-2025")
    assert [item["url"] for item in result["sources"]] == ["https://official.test/iflytek-2025"]


def test_fetch_candidates_uses_candidates_beyond_the_first_five_failures() -> None:
    research_tools = _research_module()
    candidates = [
        {
            "title": f"Candidate {index}",
            "url": f"https://source-{index}.test/page",
            "entity": "Example Company",
            "search_index": 0,
        }
        for index in range(8)
    ]

    def fetch(url: str) -> dict[str, Any]:
        index = int(url.split("source-")[1].split(".")[0])
        usable = index >= 5
        return {
            "requested_url": url,
            "url": url,
            "title": url,
            "content_excerpt": "usable evidence " * 20 if usable else "",
            "usable": usable,
            "error": None if usable else "blocked",
        }

    with patch.object(research_tools, "_fetch_source", side_effect=fetch) as fetch_mock:
        records = research_tools._fetch_candidates(candidates)

    assert fetch_mock.call_count == 8
    assert any(item["usable"] for item in records[5:])


def test_fetch_candidates_returns_completed_pages_at_deadline() -> None:
    research_tools = _research_module()
    candidates = [
        {
            "title": "Fast source",
            "url": "https://fast.test/page",
            "entity": "Comparison",
            "search_index": 0,
        },
        {
            "title": "Slow source",
            "url": "https://slow.test/page",
            "entity": "Comparison",
            "search_index": 0,
        },
    ]

    def fetch(url: str) -> dict[str, Any]:
        if "slow.test" in url:
            time.sleep(0.2)
        return {
            "requested_url": url,
            "url": url,
            "title": url,
            "content_excerpt": "usable public evidence " * 20,
            "usable": True,
            "error": None,
        }

    started = time.monotonic()
    with patch.object(research_tools, "_fetch_source", side_effect=fetch):
        records = research_tools._fetch_candidates(
            candidates,
            deadline=started + 0.05,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 0.15
    assert records[0]["usable"] is True
    assert records[1]["usable"] is False
    assert records[1]["error"] == research_tools._EXPLORATION_DEADLINE_ERROR


def test_fetch_source_rejects_pdf_binary_as_unreadable_evidence() -> None:
    research_tools = _research_module()
    with patch.object(
        research_tools,
        "_read_url",
        return_value=(
            b"%PDF-1.7 binary payload",
            "https://example.test/report.pdf",
            "application/pdf",
        ),
    ):
        result = research_tools._fetch_source("https://example.test/report.pdf")

    assert result["usable"] is False
    assert "PDF content requires text extraction" in result["error"]


def test_fetch_candidates_uses_doubao_excerpt_for_unreadable_authority_pdf() -> None:
    research_tools = _research_module()
    candidates = [
        {
            "title": "What is a Robot? What Types of Robots Are Defined?",
            "url": "https://www.ieee-ras.org/standards/robot-definition.pdf",
            "snippet": "An IEEE standards presentation defines a robot as " * 12,
            "providers": ["doubao"],
            "entity": "机器人",
            "search_index": 0,
        }
    ]

    with patch.object(
        research_tools,
        "_fetch_source",
        return_value={
            "requested_url": candidates[0]["url"],
            "url": candidates[0]["url"],
            "title": "",
            "content_excerpt": "",
            "usable": False,
            "error": "PDF content requires text extraction",
        },
    ):
        records = research_tools._fetch_candidates(candidates)

    assert records[0]["usable"] is True
    assert records[0]["content_type"] == "text/search-snippet"
    assert records[0]["content_excerpt"].startswith("An IEEE standards presentation")


def test_fetch_candidates_uses_bound_official_html_excerpt_when_page_is_blocked() -> None:
    research_tools = _research_module()
    candidate = {
        "title": "设计您的 Model Y | Tesla",
        "url": "https://www.tesla.cn/modely/design",
        "snippet": "Model Y 后轮驱动版 ¥263,500, CLTC 续航 593 公里。" * 6,
        "providers": ["doubao"],
        "entity": "Tesla Model Y",
        "search_index": 0,
        "score": 80,
    }
    bindings = [
        {
            "host": "tesla.cn",
            "source_type": "official",
            "include_subdomains": True,
        }
    ]

    with patch.object(
        research_tools,
        "_fetch_source",
        return_value={
            "requested_url": candidate["url"],
            "url": candidate["url"],
            "title": "",
            "content_excerpt": "",
            "usable": False,
            "error": "page returned too little readable public content",
        },
    ):
        records = research_tools._fetch_candidates([candidate], bindings)

    assert records[0]["usable"] is True
    assert records[0]["content_type"] == "text/search-snippet"
    assert records[0]["url"] == candidate["url"]
    assert records[0]["score"] == 80


def test_public_web_search_reports_no_evidence_after_healthy_empty_searches() -> None:
    research_tools = _research_module()

    with patch.object(
        research_tools,
        "_search_provider",
        side_effect=lambda provider, query: _record(provider, query, []),
    ) as search_mock:
        result = _discovery_tool()(
            [
                _search_item(
                    "unknown robotics market",
                    entity="Unknown Robotics Market",
                ),
                _search_item(
                    "unknown robotics hiring",
                    entity="Unknown Robotics Hiring",
                ),
            ],
            task_id="market",
            time_token="test-token",
        )

    assert search_mock.call_count == 12
    assert result["resolution"] == "no_evidence"
    assert result["sources"] == []
    assert result["summary"]["healthy_provider_count"] == 3


def test_structured_search_expands_alias_and_prioritizes_scholarly_sources() -> None:
    research_tools = _research_module()
    variants = research_tools._structured_query_variants(
        _search_item(
            "黑格尔 辩证法",
            entity="黑格尔",
            aliases=["G.W.F. Hegel"],
            dimension="方法论",
        )
    )

    assert variants == ["黑格尔 辩证法", "G.W.F. Hegel 方法论"]
    assert research_tools._source_quality_hint_score(
        "https://plato.stanford.edu/entries/hegel/", "Hegel"
    ) > research_tools._source_quality_hint_score("https://blog.csdn.net/example/hegel", "Hegel")


def test_structured_search_targets_matching_reviewed_authority_host() -> None:
    research_tools = _research_module()
    variants = research_tools._structured_query_variants(
        _search_item(
            "Tesla Model Y 中国 2026 售价 配置版本",
            entity="Tesla Model Y",
            aliases=["Model Y", "特斯拉 Model Y"],
            dimension="售价与配置等级",
        ),
        [
            {"host": "tesla.com", "source_type": "official"},
            {"host": "tesla.cn", "source_type": "official"},
            {"host": "xiaomiev.com", "source_type": "official"},
        ],
    )

    assert variants == [
        "Tesla Model Y 中国 2026 售价 配置版本",
        "site:tesla.cn Tesla Model Y 售价与配置等级",
    ]
    assert research_tools._source_quality_hint_score(
        "https://www.tesla.cn/modely",
        "Model Y",
        [{"host": "tesla.cn", "source_type": "official", "include_subdomains": True}],
    ) > research_tools._source_quality_hint_score("https://auto.sina.cn/model-y", "Model Y 参数")


def test_search_quality_gap_recommends_one_authority_follow_up_per_missing_entity() -> None:
    research_tools = _research_module()
    searches = [
        _search_item("Tesla Model Y 参数", entity="Tesla Model Y"),
        _search_item("小米YU7 参数", entity="小米YU7", aliases=["Xiaomi YU7"]),
    ]
    bindings = [
        {"host": "tesla.cn", "source_type": "official", "include_subdomains": True},
        {"host": "xiaomiev.com", "source_type": "official", "include_subdomains": True},
    ]
    sources = [
        {
            "url": "https://www.xiaomiev.com/yu7",
            "search_index": 1,
            "entity": "小米YU7",
            "usable": True,
        }
    ]

    gaps, follow_ups = research_tools._search_quality_gaps(
        searches,
        sources,
        bindings,
        "official_primary_required",
    )

    assert gaps == ["no usable official or primary source was retained for Tesla Model Y"]
    assert [item["entity"] for item in follow_ups] == ["Tesla Model Y"]
    assert follow_ups[0]["query"].startswith("site:tesla.cn")


def test_search_quality_gap_recommends_different_source_strategy_when_no_source() -> None:
    research_tools = _research_module()
    searches = [
        _search_item(
            "向亚运 基本信息",
            entity="向亚运",
            dimension="人物身份与经历",
        )
    ]

    gaps, follow_ups = research_tools._search_quality_gaps(
        searches,
        [],
        [],
        "single_source_sufficient",
    )

    assert gaps == ["no usable public source was retained for 向亚运"]
    assert len(follow_ups) == 1
    assert follow_ups[0]["query"] != searches[0]["query"]
    assert "机构" in follow_ups[0]["query"]
    assert follow_ups[0]["entity"] == "向亚运"


def test_compact_search_output_hides_uncitable_candidate_and_fetch_text() -> None:
    research_tools = _research_module()

    assert (
        "results"
        not in research_tools._compact_search_records(
            [{"provider": "doubao", "results": [{"snippet": "raw"}], "status": "ok"}]
        )[0]
    )
    assert (
        "snippet"
        not in research_tools._compact_candidates(
            [{"url": "https://example.test", "snippet": "uncitable"}]
        )[0]
    )
    assert (
        "content_excerpt"
        not in research_tools._compact_fetch_records(
            [{"url": "https://example.test", "content_excerpt": "uncitable"}]
        )[0]
    )


def test_structured_search_translates_internal_dimension_names() -> None:
    variants = _research_module()._structured_query_variants(
        _search_item(
            "具身智能 学术定义",
            entity="具身智能",
            aliases=["Embodied Intelligence"],
            dimension="academic_usage_patterns",
        )
    )

    assert variants == [
        "具身智能 学术定义",
        "Embodied Intelligence academic terminology research",
    ]
    assert "_" not in " ".join(variants)


def test_public_web_search_rejects_the_removed_flat_query_contract() -> None:
    with pytest.raises(ValueError, match="search items must be objects"):
        _discovery_tool()(
            cast(Any, ["https://example.test/report"]),
            task_id="market",
            time_token="test-token",
        )


def test_get_current_time_returns_fresh_shanghai_context_without_leaking_token() -> None:
    first = _time_tool()()
    second = _time_tool()()

    assert first["timezone"] == "Asia/Shanghai"
    assert first["current_year"] == int(first["current_date"][:4])
    assert first["issued_at"].endswith("Z")
    assert first["expires_at"].endswith("Z")
    assert first["time_token"] != second["time_token"]
    assert first["time_token"] not in str(first["operation_summary"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Tesla Model Y", "teslamodely"),
        ("Tesla ModelY", "teslamodely"),
        ("Tesla Model-Y", "teslamodely"),
        ("小米 YU7", "小米yu7"),
        ("小米YU7", "小米yu7"),
    ],
)
def test_entity_key_preserves_short_vehicle_model_identity(
    value: str,
    expected: str,
) -> None:
    assert _research_module()._entity_key(value) == expected


def test_model_y_identity_outranks_model_3_and_generic_tesla_results() -> None:
    search = _search_item(
        '"Tesla Model Y" 2026 dimensions',
        entity="Tesla Model Y",
        aliases=["Model Y", "Tesla ModelY", "特斯拉 Model Y"],
        dimension="dimensions",
    )
    records = [
        _record(
            "bing_rss",
            search["query"],
            [
                {
                    "title": "Tesla Model 3 official dimensions",
                    "url": "https://tesla.test/model3",
                    "snippet": "Tesla dimensions",
                },
                {
                    "title": "Tesla vehicle lineup",
                    "url": "https://tesla.test/cars",
                    "snippet": "Tesla vehicles",
                },
                {
                    "title": "Tesla ModelY official dimensions",
                    "url": "https://tesla.test/model-y",
                    "snippet": "Model Y dimensions and wheelbase",
                },
            ],
        )
    ]

    ranked = _research_module()._rank_structured_candidates(
        search,
        records,
        search_index=0,
    )

    assert ranked[0]["url"] == "https://tesla.test/model-y"
    assert ranked[0]["entity_match"] is True


def test_standalone_y_alias_receives_no_identity_bonus() -> None:
    module = _research_module()
    records = [
        _record(
            "bing_rss",
            "Tesla vehicle dimensions",
            [
                {
                    "title": "Y vehicle dimensions",
                    "url": "https://example.test/y-car",
                    "snippet": "vehicle dimensions",
                }
            ],
        )
    ]
    with_y = module._rank_structured_candidates(
        _search_item(
            "Tesla vehicle dimensions",
            entity="Tesla",
            aliases=["Y"],
            dimension="range",
        ),
        records,
        search_index=0,
    )
    without_y = module._rank_structured_candidates(
        _search_item(
            "Tesla vehicle dimensions",
            entity="Tesla",
            aliases=[],
            dimension="range",
        ),
        records,
        search_index=0,
    )

    assert with_y[0]["score"] == without_y[0]["score"]
    assert with_y[0]["entity_match"] is False


def test_ranked_candidates_prefer_company_introduction_over_encyclopedia() -> None:
    module = _research_module()
    search = _search_item("科大讯飞 公司简介", entity="科大讯飞")
    records = [
        _record(
            "bing_rss",
            search["query"],
            [
                {
                    "title": "科大讯飞股份有限公司 - 百度百科",
                    "url": "https://baike.baidu.com/item/iflytek",
                    "snippet": "科大讯飞公司资料",
                },
                {
                    "title": "行业资讯",
                    "url": "https://news.test/one",
                    "snippet": "科大讯飞公司资料",
                },
                {
                    "title": "行业资讯二",
                    "url": "https://news.test/two",
                    "snippet": "科大讯飞公司资料",
                },
                {
                    "title": "公司简介 - 科大讯飞",
                    "url": "https://www.xfyun.cn/introduction",
                    "snippet": "科大讯飞公司简介",
                },
            ],
        )
    ]

    ranked = module._rank_structured_candidates(search, records, search_index=0)

    assert ranked[0]["url"] == "https://www.xfyun.cn/introduction"


def test_alias_matched_authority_outranks_exact_name_encyclopedia() -> None:
    module = _research_module()
    search = _search_item(
        "具身智能 定义",
        entity="具身智能",
        aliases=["Embodied Intelligence", "Embodied AI", "具身人工智能"],
        dimension="definitions_and_core_features",
    )
    records = [
        _record(
            "bing_rss",
            search["query"],
            [
                {
                    "title": "具身智能 - 快懂百科",
                    "url": "https://m.baike.com/wiki/embodied-ai",
                    "snippet": "具身智能是人工智能与机器人学交叉领域。",
                },
                {
                    "title": "Requirements and framework for Embodied AI systems",
                    "url": "https://www.itu.int/rec/T-REC-F.748.66",
                    "snippet": "Embodied AI integrates intelligence into physical systems.",
                },
            ],
        )
    ]

    ranked = module._rank_structured_candidates(search, records, search_index=0)

    assert [item["url"] for item in ranked] == [
        "https://www.itu.int/rec/T-REC-F.748.66",
        "https://m.baike.com/wiki/embodied-ai",
    ]
    assert all(item["entity_match"] is True for item in ranked)


def test_round_robin_skips_duplicate_urls_before_second_entity_loses_coverage() -> None:
    module = _research_module()
    shared = {"url": "https://shared.test/spec", "entity": "Tesla Model Y"}
    tesla_second = {"url": "https://tesla.test/model-y", "entity": "Tesla Model Y"}
    xiaomi_second = {"url": "https://xiaomi.test/yu7", "entity": "小米 YU7"}

    selected = module._round_robin_candidates([[shared, tesla_second], [shared, xiaomi_second]])

    assert [item["url"] for item in selected[:3]] == [
        "https://shared.test/spec",
        "https://xiaomi.test/yu7",
        "https://tesla.test/model-y",
    ]


def test_round_robin_prioritizes_distinct_domains_within_one_entity() -> None:
    module = _research_module()
    selected = module._round_robin_candidates(
        [
            [
                {"url": "https://portal.test/first", "entity": "Example"},
                {"url": "https://portal.test/second", "entity": "Example"},
                {"url": "https://official.test/company", "entity": "Example"},
            ]
        ]
    )

    assert [item["url"] for item in selected] == [
        "https://portal.test/first",
        "https://official.test/company",
        "https://portal.test/second",
    ]


def test_two_entity_search_fetches_both_pools_after_one_page_is_blocked() -> None:
    research_tools = _research_module()

    def search(provider: str, query: str) -> dict[str, Any]:
        if provider != "bing_rss":
            return _record(provider, query, [])
        if "Tesla" in query:
            results = [
                {
                    "title": "Tesla Model Y official specifications",
                    "url": "https://tesla.test/blocked",
                    "snippet": "Model Y dimensions",
                },
                {
                    "title": "Tesla Model Y dimensions review",
                    "url": "https://media.test/model-y",
                    "snippet": "Model Y wheelbase and dimensions",
                },
            ]
        else:
            results = [
                {
                    "title": "小米 YU7 官方参数",
                    "url": "https://xiaomi.test/yu7",
                    "snippet": "小米 YU7 车身尺寸 轴距",
                }
            ]
        return _record(provider, query, results)

    def fetch(url: str) -> dict[str, Any]:
        blocked = url.endswith("/blocked")
        return {
            "requested_url": url,
            "url": url,
            "title": url,
            "content_excerpt": "" if blocked else "usable vehicle evidence " * 20,
            "usable": not blocked,
            "error": "HTTP 403" if blocked else None,
        }

    with (
        patch.object(research_tools, "_search_provider", side_effect=search),
        patch.object(research_tools, "_fetch_source", side_effect=fetch),
    ):
        result = _discovery_tool()(
            [
                _search_item(
                    '"Tesla Model Y" dimensions',
                    entity="Tesla Model Y",
                    aliases=["Model Y"],
                    dimension="车身尺寸与轴距",
                ),
                _search_item(
                    '"小米 YU7" 车身尺寸',
                    entity="小米 YU7",
                    aliases=["小米YU7", "Xiaomi YU7"],
                    dimension="车身尺寸与轴距",
                ),
            ],
            task_id="dimensions",
            time_token="test-token",
        )

    source_urls = {item["url"] for item in result["sources"]}
    assert source_urls == {
        "https://media.test/model-y",
        "https://xiaomi.test/yu7",
    }
    assert result["summary"]["candidate_count_by_search"] == [2, 1]
    assert "content_excerpt" not in str(result["operation_summary"])


def _evidence_item(
    url: str,
    *,
    claim: str = "Unitree is headquartered in Hangzhou.",
    source_type: str = "primary",
    stance: str = "supporting",
    independence: str = "independent",
    directness: str = "direct",
    as_of: str | None = None,
) -> dict[str, str]:
    item = {
        "claim": claim,
        "source_url": url,
        "source_type": source_type,
        "stance": stance,
        "independence": independence,
        "directness": directness,
    }
    if as_of is not None:
        item["as_of"] = as_of
    return item


def test_record_research_finding_explicitly_resolves_one_question() -> None:
    today = datetime.date.today().isoformat()
    result = _finding_tool()(
        task_id="companies",
        question="Which companies are based in Hangzhou?",
        conclusion="Unitree is headquartered in Hangzhou.",
        implications="The company is relevant to a Hangzhou robotics market map.",
        verification_method="dual_independent_required",
        verification_id="verification-1",
        status="sourced",
        evidence=[
            _evidence_item("https://example.test/unitree", source_type="primary", as_of=today),
            _evidence_item("https://official.test/unitree", source_type="official", as_of=today),
        ],
        limitations=[],
    )

    assert result["task_resolution"] == "completed"
    assert result["citations"] == [
        "https://example.test/unitree",
        "https://official.test/unitree",
    ]
    assert result["verification_method"] == "dual_independent_required"
    assert result["confidence"] == "high"


def test_record_research_finding_scores_against_persisted_search_date() -> None:
    evidence = [
        _evidence_item(
            f"https://source-{index}.example/record",
            source_type="official",
            as_of="2020-01-02",
        )
        for index in (1, 2)
    ]
    result = _finding_tool()(
        task_id="historical-record",
        question="What did the historical records establish?",
        conclusion="Unitree is headquartered in Hangzhou.",
        implications="The conclusion is evaluated at the recorded search date.",
        verification_method="dual_independent_required",
        verification_id="verification-1",
        status="sourced",
        evidence=evidence,
        limitations=[],
        provenance={
            "verification_id": "verification-1",
            "search_ids": ["search-1"],
            "evaluated_urls": [item["source_url"] for item in evidence],
            "evaluations": evidence,
            "searches": [
                {
                    "search_id": "search-1",
                    "structured_searches": [
                        {
                            "query": "historical record",
                            "entity": "Unitree",
                            "aliases": [],
                            "dimension": "headquarters",
                        }
                    ],
                    "usable_urls": [item["source_url"] for item in evidence],
                    "current_time": {
                        "issued_at": "2020-04-01T00:00:00Z",
                        "current_date": "2020-04-01",
                        "timezone": "Asia/Shanghai",
                    },
                }
            ],
        },
    )

    assert result["confidence"] == "high"


def test_record_research_finding_deduplicates_repeated_evidence() -> None:
    result = _finding_tool()(
        task_id="companies",
        question="Which companies are based in Hangzhou?",
        conclusion="Unitree is headquartered in Hangzhou.",
        implications="The company is relevant to a Hangzhou robotics market map.",
        verification_method="single_source_sufficient",
        verification_id="verification-1",
        status="sourced",
        evidence=[
            _evidence_item("https://example.test/unitree"),
            _evidence_item("https://example.test/unitree"),
        ],
        limitations=[],
    )

    assert result["task_resolution"] == "completed"
    assert result["citations"] == ["https://example.test/unitree"]


def test_record_research_finding_preserves_canonical_provenance_without_time_token() -> None:
    result = _finding_tool()(
        task_id="companies",
        question="Which companies are based in Hangzhou?",
        conclusion="Unitree is headquartered in Hangzhou.",
        implications="The company is relevant to the market map.",
        verification_method="single_source_sufficient",
        verification_id="verification-1",
        status="sourced",
        evidence=[_evidence_item("https://example.test/unitree")],
        limitations=[],
        provenance={
            "verification_id": "verification-1",
            "search_ids": ["search-1"],
            "evaluated_urls": ["https://example.test/unitree"],
            "searches": [
                {
                    "search_id": "search-1",
                    "structured_searches": [
                        {
                            "query": '"Unitree" headquarters',
                            "entity": "Unitree",
                            "aliases": ["宇树科技"],
                            "dimension": "headquarters",
                        }
                    ],
                    "usable_urls": ["https://example.test/unitree"],
                    "current_time": {
                        "issued_at": "2026-07-18T10:00:00Z",
                        "current_date": "2026-07-18",
                        "timezone": "Asia/Shanghai",
                    },
                }
            ],
        },
    )

    assert result["provenance"]["search_ids"] == ["search-1"]
    assert result["provenance"]["searches"][0]["current_time"] == {
        "issued_at": "2026-07-18T10:00:00Z",
        "current_date": "2026-07-18",
        "timezone": "Asia/Shanghai",
    }
    assert "time_token" not in str(result["provenance"])


def test_record_research_finding_downgrades_an_unmet_method_to_blocked() -> None:
    requested_sourced = _finding_tool()(
        task_id="companies",
        question="Which companies are based in Hangzhou?",
        conclusion="Unitree is headquartered in Hangzhou.",
        implications="The company is relevant to the market map.",
        verification_method="single_source_sufficient",
        verification_id="verification-1",
        status="sourced",
        evidence=[],
        limitations=[],
    )
    assert requested_sourced["status"] == "blocked"
    assert requested_sourced["task_resolution"] == "blocked"
    assert requested_sourced["confidence"] == "low"
    assert "requires at least one supporting source" in requested_sourced["limitations"][0]

    blocked = _finding_tool()(
        task_id="companies",
        question="Which companies are based in Hangzhou?",
        conclusion="The question remains unresolved.",
        implications="The company list may be incomplete.",
        verification_method="dual_independent_required",
        verification_id="verification-1",
        status="blocked",
        evidence=[],
        limitations=["Two distinct public searches returned no usable source."],
    )
    assert blocked["task_resolution"] == "blocked"
    assert blocked["confidence"] == "low"


def test_record_research_finding_rejects_unsupported_verification_method() -> None:
    with pytest.raises(ValueError, match="verification_method is unsupported"):
        _finding_tool()(
            task_id="companies",
            question="Q",
            conclusion="C",
            implications="I",
            verification_method="guesswork",
            status="sourced",
            evidence=[_evidence_item("https://example.test/x", claim="C")],
            limitations=[],
        )


def test_record_research_finding_requires_unverifiable_flag_to_be_blocked() -> None:
    with pytest.raises(ValueError, match="unverifiable_flag tasks must be recorded as blocked"):
        _finding_tool()(
            task_id="companies",
            question="Q",
            conclusion="C",
            implications="I",
            verification_method="unverifiable_flag",
            status="sourced",
            evidence=[_evidence_item("https://example.test/x", claim="C")],
            limitations=[],
        )

    blocked = _finding_tool()(
        task_id="companies",
        question="Q",
        conclusion="This claim cannot be settled through public search.",
        implications="The report notes this as an open question.",
        verification_method="unverifiable_flag",
        status="blocked",
        evidence=[],
        limitations=["No public search can establish this claim."],
    )
    assert blocked["task_resolution"] == "blocked"
    assert blocked["confidence"] == "low"


def test_verify_claim_evidence_dedups_and_drops_unrelated_items() -> None:
    result = _verify_tool()(
        task_id="companies",
        claim="Unitree makes humanoid robots.",
        search_ids=["search-1"],
        items=[
            {
                "source_url": "https://a.test/1",
                "source_type": "official",
                "stance": "supporting",
                "independent": True,
                "directness": "direct",
            },
            {
                "source_url": "https://a.test/1",
                "source_type": "official",
                "stance": "supporting",
                "independent": True,
                "directness": "direct",
            },
            {
                "source_url": "https://b.test/2",
                "source_type": "reputable_media",
                "stance": "unrelated",
                "independent": True,
                "directness": "indirect",
            },
        ],
    )

    assert [item["source_url"] for item in result["evidence"]] == ["https://a.test/1"]
    assert [item["stance"] for item in result["evaluations"]] == [
        "supporting",
        "unrelated",
    ]
    assert result["evidence"][0]["independence"] == "independent"
    assert result["claim"] == "Unitree makes humanoid robots."


def test_verify_claim_evidence_rejects_two_independent_items_sharing_a_domain() -> None:
    with pytest.raises(ValueError, match="share the domain"):
        _verify_tool()(
            task_id="companies",
            claim="Unitree makes humanoid robots.",
            search_ids=["search-1"],
            items=[
                {
                    "source_url": "https://a.test/page1",
                    "source_type": "official",
                    "stance": "supporting",
                    "independent": True,
                    "directness": "direct",
                },
                {
                    "source_url": "https://a.test/page2",
                    "source_type": "primary",
                    "stance": "supporting",
                    "independent": True,
                    "directness": "direct",
                },
            ],
        )


def test_verify_claim_evidence_requires_task_id_and_claim() -> None:
    with pytest.raises(ValueError, match="task_id, claim, and search_ids are required"):
        _verify_tool()(task_id="", claim="", search_ids=[], items=[])


@pytest.mark.parametrize(
    ("url", "claimed_type"),
    [
        ("https://en.wikipedia.org/wiki/Immanuel_Kant", "reputable_media"),
        ("https://plato.stanford.edu/entries/kant/", "official"),
        ("https://iep.utm.edu/kantview/", "primary"),
        ("https://www.britannica.com/biography/Immanuel-Kant", "official"),
        ("https://study.com/academy/lesson/kant.html", "reputable_media"),
        ("https://unlisted-blog.example/kant", "primary"),
    ],
)
def test_verify_claim_evidence_demotes_untrusted_authority_claims(
    url: str,
    claimed_type: str,
) -> None:
    result = _verify_tool()(
        task_id="kant",
        claim="Kant influenced German idealism.",
        search_ids=["search-1"],
        items=[
            {
                "source_url": url,
                "source_type": claimed_type,
                "stance": "supporting",
                "independent": True,
                "directness": "direct",
            }
        ],
        authority_bindings=[],
    )

    assert result["evidence"][0]["source_type"] == "secondary"
    assert result["authority_binding_fingerprint"] == _EMPTY_AUTHORITY_FINGERPRINT


def test_verify_claim_evidence_honors_exact_binding_without_widening_subdomains() -> None:
    binding = [{"host": "archive.example", "source_type": "primary"}]
    exact = _verify_tool()(
        task_id="kant",
        claim="The archive contains the primary text.",
        search_ids=["search-1"],
        items=[
            {
                "source_url": "https://archive.example/text",
                "source_type": "primary",
                "stance": "supporting",
                "independent": True,
                "directness": "direct",
            }
        ],
        authority_bindings=binding,
    )
    subdomain = _verify_tool()(
        task_id="kant",
        claim="The archive contains the primary text.",
        search_ids=["search-1"],
        items=[
            {
                "source_url": "https://mirror.archive.example/text",
                "source_type": "primary",
                "stance": "supporting",
                "independent": True,
                "directness": "direct",
            }
        ],
        authority_bindings=binding,
    )

    assert exact["evidence"][0]["source_type"] == "primary"
    assert subdomain["evidence"][0]["source_type"] == "secondary"


def test_verify_claim_evidence_honors_builtin_government_authority() -> None:
    result = _verify_tool()(
        task_id="public-record",
        claim="The agency published the public record.",
        search_ids=["search-1"],
        items=[
            {
                "source_url": "https://records.example.gov/publication",
                "source_type": "official",
                "stance": "supporting",
                "independent": True,
                "directness": "direct",
            }
        ],
        authority_bindings=[],
    )

    assert result["evidence"][0]["source_type"] == "official"
    assert result["authority_binding_fingerprint"] == _EMPTY_AUTHORITY_FINGERPRINT


@pytest.mark.parametrize(
    ("method", "evidence"),
    [
        ("single_source_sufficient", []),
        (
            "dual_independent_required",
            [_evidence_item("https://one.example/source")],
        ),
        (
            "official_primary_required",
            [_evidence_item("https://reference.example/source", source_type="secondary")],
        ),
        (
            "contradiction_sensitive",
            [_evidence_item("https://one.example/source")],
        ),
    ],
)
def test_record_research_finding_hard_downgrade_preserves_partial_evidence(
    method: str,
    evidence: list[dict[str, str]],
) -> None:
    result = _finding_tool()(
        task_id="dimension",
        question="What can be established?",
        conclusion="The bounded evidence supports only a partial answer.",
        implications="No additional claim is published.",
        verification_method=method,
        verification_id="verification-1",
        status="sourced",
        evidence=evidence,
        limitations=[],
    )

    assert result["status"] == "blocked"
    assert result["task_resolution"] == "blocked"
    assert result["confidence"] == "low"
    assert result["evidence"] == evidence
    assert result["limitations"]


def test_record_research_finding_rejects_conclusion_claim_substitution() -> None:
    with pytest.raises(ValueError, match="exactly match the verified claim"):
        _finding_tool()(
            task_id="kant",
            question="What did the source establish?",
            conclusion="A stronger conclusion than the source established.",
            implications="This must not be published.",
            verification_method="single_source_sufficient",
            verification_id="verification-1",
            status="sourced",
            evidence=[
                _evidence_item(
                    "https://archive.example/text",
                    claim="The source established a narrower conclusion.",
                )
            ],
            limitations=[],
            verified_claim="The source established a narrower conclusion.",
        )


def test_build_evidence_graph_renders_nodes_and_edges_from_key_findings() -> None:
    report = {
        "direct_answer": "answer",
        "key_findings": [
            {
                "task_id": "business",
                "question": "What does the company do?",
                "status": "sourced",
                "evidence": [
                    {"source_url": "https://a.test/1", "stance": "supporting"},
                    {"source_url": "https://b.test/2", "stance": "contradicting"},
                ],
            },
            {
                "task_id": "market",
                "question": "unresolved",
                "status": "limited",
                "evidence": [],
            },
        ],
        "citations": ["https://a.test/1", "https://b.test/2"],
        "limitations": [],
    }
    result = _graph_tool()(report=report)
    graph = result["evidence_graph"]

    assert graph.startswith("flowchart LR")
    assert "T_business" in graph and ":::sourced" in graph
    assert "T_market" in graph and ":::limited" in graph
    assert "-->" in graph
    assert "-.->" in graph
    assert result["direct_answer"] == "answer"


def test_build_evidence_graph_requires_an_object() -> None:
    with pytest.raises(ValueError, match="report must be an object"):
        _graph_tool()(report="not a report")


def test_build_evidence_graph_assembles_only_committed_results() -> None:
    result = _graph_tool()(
        report={
            "direct_answer": "Model Y and YU7 have different strengths.",
            "limitations": ["Prices may change."],
            "key_findings": [{"task_id": "forged"}],
        },
        committed_results=[
            {
                "task_id": "dimensions",
                "result": {
                    "task_id": "dimensions",
                    "question": "How large are the vehicles?",
                    "conclusion": "YU7 has the longer wheelbase.",
                    "implications": "It may offer more cabin space.",
                    "confidence": "high",
                    "verification_method": "dual_independent_required",
                    "status": "sourced",
                    "evidence": [
                        {
                            "claim": "YU7 has the longer wheelbase.",
                            "source_url": "https://example.test/specs",
                            "source_type": "official",
                            "stance": "supporting",
                            "independence": "independent",
                            "directness": "direct",
                        }
                    ],
                    "citations": ["https://example.test/specs"],
                    "limitations": ["The ISO wording was available only indirectly."],
                    "provenance": {
                        "verification_id": "verification-1",
                        "search_ids": ["search-1"],
                        "evaluated_urls": ["https://example.test/specs"],
                        "evaluations": [
                            {
                                "claim": "YU7 has the longer wheelbase.",
                                "source_url": "https://example.test/specs",
                            }
                        ],
                        "searches": [],
                    },
                },
            }
        ],
    )

    assert [item["task_id"] for item in result["key_findings"]] == ["dimensions"]
    assert result["citations"] == ["https://example.test/specs"]
    assert result["limitations"] == ["Prices may change."]
    assert result["direct_answer"] == "Model Y and YU7 have different strengths."
    assert "implications" not in result["key_findings"][0]
    assert "claim" not in result["key_findings"][0]["evidence"][0]
    assert "evaluations" not in result["key_findings"][0]["provenance"]
    assert "forged" not in str(result)


def test_build_evidence_graph_never_asserts_a_blocked_conclusion() -> None:
    result = _graph_tool()(
        committed_results=[
            {
                "task_id": "influence",
                "result": {
                    "task_id": "influence",
                    "question": "Did Hegel directly make this claim?",
                    "conclusion": "Hegel definitely made the claim.",
                    "implications": "Unsupported downstream influence prose.",
                    "confidence": "low",
                    "verification_method": "official_primary_required",
                    "status": "blocked",
                    "evidence": [
                        {
                            "claim": "A reference work discusses the topic.",
                            "source_url": "https://plato.stanford.edu/entries/hegel/",
                            "source_type": "secondary",
                            "stance": "supporting",
                            "independence": "independent",
                            "directness": "indirect",
                        }
                    ],
                    "citations": ["https://plato.stanford.edu/entries/hegel/"],
                    "limitations": ["No bound primary source was found."],
                    "provenance": {
                        "verification_id": "verification-1",
                        "search_ids": ["search-1"],
                        "evaluated_urls": ["https://plato.stanford.edu/entries/hegel/"],
                        "searches": [],
                        "authority_binding_fingerprint": _EMPTY_AUTHORITY_FINGERPRINT,
                    },
                },
            }
        ],
        report={"direct_answer": "Forged synthesis prose."},
    )

    assert result["direct_answer"] == (
        "Did Hegel directly make this claim?: 未达到验证要求，详见限制"  # noqa: RUF001
    )
    assert "Hegel definitely made" not in result["direct_answer"]
    assert "Forged synthesis" not in str(result)
    assert "implications" not in result["key_findings"][0]
    assert result["key_findings"][0]["status"] == "limited"


def test_confidence_combine_takes_the_lowest_factor() -> None:
    confidence = _confidence_module()
    assert confidence.combine({"a": "high", "b": "high"}) == "high"
    assert confidence.combine({"a": "high", "b": "low"}) == "low"
    assert confidence.combine({}) == "low"


def test_confidence_score_finding_caps_on_one_bad_factor() -> None:
    confidence = _confidence_module()
    today = datetime.date(2026, 7, 16)
    high_evidence = [
        {
            "stance": "supporting",
            "source_type": "official",
            "independence": "independent",
            "directness": "direct",
            "as_of": "2026-07",
        },
        {
            "stance": "supporting",
            "source_type": "primary",
            "independence": "independent",
            "directness": "direct",
            "as_of": "2026-06",
        },
    ]
    all_high = confidence.score_finding(high_evidence, "dual_independent_required", today=today)
    assert all_high["overall"] == "high"

    stale_evidence = [dict(item) for item in high_evidence]
    stale_evidence[0]["as_of"] = "2020-01"
    capped = confidence.score_finding(stale_evidence, "dual_independent_required", today=today)
    assert capped["recency"] == "low"
    assert capped["overall"] == "low"


def test_confidence_coverage_gap_message_reports_unmet_verification_method() -> None:
    confidence = _confidence_module()
    single_source = [
        {
            "stance": "supporting",
            "source_type": "job_board",
            "independence": "same_origin",
            "directness": "indirect",
        }
    ]
    message = confidence.coverage_gap_message(single_source, "dual_independent_required")
    assert message is not None
    assert "dual_independent_required" in message
    assert confidence.coverage_gap_message(single_source, "single_source_sufficient") is None


def test_query_variants_remove_city_and_generic_company_suffixes() -> None:
    research_tools = _research_module()
    queries = research_tools._query_variants(
        "杭州拉格朗日具身智能科技有限公司",
        "公司的技术实力如何",
    )

    assert queries[0] == "杭州拉格朗日具身智能科技有限公司"
    assert queries[1].startswith('"杭州拉格朗日具身智能科技有限公司"')
    assert len(queries) == 2


def test_short_subject_query_variants_preserve_the_full_identity() -> None:
    research_tools = _research_module()

    assert research_tools._query_variants("威灿科技", "") == [
        "威灿科技",
        '"威灿科技" 公司',
    ]


def test_query_variants_use_router_question_for_one_character_typo_recovery() -> None:
    research_tools = _research_module()

    assert research_tools._query_variants(
        "拉格朗日具身只能公司",
        "拉格朗日具身智能公司的基本公开信息",
    ) == [
        "拉格朗日具身只能公司",
        "拉格朗日具身智能公司",
    ]


def test_duckduckgo_html_results_recover_live_company_target() -> None:
    research_tools = _research_module()
    payload = """
    <html><body>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=http%3A%2F%2Fwww.hitopking.com%2F">
        杭州威灿科技有限公司
      </a>
    </body></html>
    """.encode()
    with patch.object(
        research_tools,
        "_read_url",
        return_value=(payload, "https://html.duckduckgo.com/html/", "text/html"),
    ):
        results = research_tools._search_html(
            "duckduckgo",
            "https://html.duckduckgo.com/html/?q=weican",
        )

    assert results == [
        {
            "title": "杭州威灿科技有限公司",
            "url": "http://www.hitopking.com/",
            "snippet": "",
        }
    ]


def test_duckduckgo_uses_browser_headers_and_lite_fallback() -> None:
    research_tools = _research_module()
    company_result = {
        "title": "杭州威灿科技有限公司",
        "url": "http://www.hitopking.com/",
        "snippet": "",
    }
    with patch.object(
        research_tools,
        "_search_html_page",
        side_effect=[
            ([], "<html>Unfortunately, bots use DuckDuckGo too.</html>"),
            ([company_result], "<html>result</html>"),
        ],
    ) as search_page:
        record = research_tools._search_provider("duckduckgo", "威灿科技")

    assert search_page.call_count == 2
    assert record["search_url"].startswith("https://lite.duckduckgo.com/lite/")
    assert record["status"] == "ok"
    assert record["results"] == [company_result]
    assert "Mozilla/5.0" in research_tools._BROWSER_USER_AGENT
    assert research_tools._HTML_SEARCH_HEADERS["Accept-Language"].startswith("zh-CN")


def test_duckduckgo_recognized_empty_page_does_not_fallback() -> None:
    research_tools = _research_module()
    with patch.object(
        research_tools,
        "_search_html_page",
        return_value=([], "<html>No results found for 威灿科技</html>"),
    ) as search_page:
        record = research_tools._search_provider("duckduckgo", "威灿科技")

    search_page.assert_called_once()
    assert record["search_url"].startswith("https://html.duckduckgo.com/html/")
    assert record["status"] == "empty"
    assert record["error"] is None


def test_search_shells_are_not_misreported_as_empty_results() -> None:
    research_tools = _research_module()

    assert (
        research_tools._classify_html_search(
            "duckduckgo",
            [],
            '<html><div class="anomaly-modal">Please verify you are human</div></html>',
        )[0]
        == "blocked"
    )
    assert (
        research_tools._classify_html_search(
            "duckduckgo",
            [],
            "<html><body>DuckDuckGo search shell without result markup</body></html>" * 4,
        )[0]
        == "failed"
    )
