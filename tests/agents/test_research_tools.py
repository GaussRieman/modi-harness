"""Trusted single-Operation public Web research tests."""

from __future__ import annotations

import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tool() -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    assert len(agent.tools) == 1
    return cast(Callable[..., dict[str, Any]], agent.tools[0].handler)


def _research_module() -> ModuleType:
    return sys.modules[_tool().__module__]


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


def test_query_variants_remove_city_and_generic_company_suffixes() -> None:
    research_tools = _research_module()
    queries = research_tools._query_variants(
        "杭州拉格朗日具身智能科技有限公司",
        "公司的技术实力如何",
    )

    assert queries[0] == "杭州拉格朗日具身智能科技有限公司"
    assert queries[1].startswith("拉格朗日具身智能")
    assert len(queries) == 2


def test_duckduckgo_html_results_unwrap_target_url() -> None:
    research_tools = _research_module()
    payload = b"""
    <html><body>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fcompany">
        Weican Technology Company
      </a>
    </body></html>
    """
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
            "title": "Weican Technology Company",
            "url": "https://example.test/company",
            "snippet": "",
        }
    ]
