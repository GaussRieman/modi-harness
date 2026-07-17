"""Trusted public Web research Operation tests."""

from __future__ import annotations

import datetime
import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

import pytest

from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    binding = next(
        item for item in agent.tools if item.spec["name"] == "public_web_research"
    )
    return cast(Callable[..., dict[str, Any]], binding.handler)


def _discovery_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    binding = next(
        item for item in agent.tools if item.spec["name"] == "public_web_search"
    )
    return cast(Callable[..., dict[str, Any]], binding.handler)


def _time_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    binding = next(item for item in agent.tools if item.spec["name"] == "get_current_time")
    return cast(Callable[..., dict[str, Any]], binding.handler)


def _verify_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    binding = next(
        item for item in agent.tools if item.spec["name"] == "verify_claim_evidence"
    )
    return cast(Callable[..., dict[str, Any]], binding.handler)


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
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    binding = next(
        item for item in agent.tools if item.spec["name"] == "record_research_finding"
    )
    return cast(Callable[..., dict[str, Any]], binding.handler)


def _graph_tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    binding = next(
        item for item in agent.tools if item.spec["name"] == "build_evidence_graph"
    )
    return cast(Callable[..., dict[str, Any]], binding.handler)


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
        search_url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(
            {"q": query}
        )
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
    assert 1 <= fetch_mock.call_count <= 5
    assert len(result["search_records"]) == 6
    assert len(result["sources"]) == 2
    assert all(item["usable"] for item in result["sources"])
    assert not any("does not prove" in item for item in result["limitations"])
    urls = {item["url"] for item in result["candidates"]}
    assert "https://example.test/company" in urls
    assert "https://jobs.test/lagrange" in urls
    assert "https://dictionary.test/lagrange" not in urls


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

    assert search_mock.call_count == 3
    fetch_mock.assert_called_once_with("https://unitree.test/humanoid")
    assert result["resolution"] == "sourced"
    assert result["sources"][0]["title"] == "Unitree Robotics"


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

    assert search_mock.call_count == 6
    assert result["resolution"] == "no_evidence"
    assert result["sources"] == []
    assert result["summary"]["healthy_provider_count"] == 3


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


def test_round_robin_skips_duplicate_urls_before_second_entity_loses_coverage() -> None:
    module = _research_module()
    shared = {"url": "https://shared.test/spec", "entity": "Tesla Model Y"}
    tesla_second = {"url": "https://tesla.test/model-y", "entity": "Tesla Model Y"}
    xiaomi_second = {"url": "https://xiaomi.test/yu7", "entity": "小米 YU7"}

    selected = module._round_robin_candidates(
        [[shared, tesla_second], [shared, xiaomi_second]]
    )

    assert [item["url"] for item in selected[:3]] == [
        "https://shared.test/spec",
        "https://xiaomi.test/yu7",
        "https://tesla.test/model-y",
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
            _evidence_item(
                "https://example.test/unitree", source_type="primary", as_of=today
            ),
            _evidence_item(
                "https://official.test/unitree", source_type="official", as_of=today
            ),
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


def test_record_research_finding_requires_evidence_or_a_blocker() -> None:
    with pytest.raises(ValueError, match="requires at least one evidence item"):
        _finding_tool()(
            task_id="companies",
            question="Which companies are based in Hangzhou?",
            conclusion="Unitree is headquartered in Hangzhou.",
            implications="The company is relevant to the market map.",
            verification_method="single_source_sufficient",
            status="sourced",
            evidence=[],
            limitations=[],
        )

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
    with pytest.raises(
        ValueError, match="unverifiable_flag tasks must be recorded as blocked"
    ):
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
    all_high = confidence.score_finding(
        high_evidence, "dual_independent_required", today=today
    )
    assert all_high["overall"] == "high"

    stale_evidence = [dict(item) for item in high_evidence]
    stale_evidence[0]["as_of"] = "2020-01"
    capped = confidence.score_finding(
        stale_evidence, "dual_independent_required", today=today
    )
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
    assert (
        confidence.coverage_gap_message(single_source, "single_source_sufficient")
        is None
    )


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

    assert research_tools._classify_html_search(
        "duckduckgo",
        [],
        '<html><div class="anomaly-modal">Please verify you are human</div></html>',
    )[0] == "blocked"
    assert research_tools._classify_html_search(
        "duckduckgo",
        [],
        "<html><body>DuckDuckGo search shell without result markup</body></html>" * 4,
    )[0] == "failed"
