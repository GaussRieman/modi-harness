"""Trusted public Web research Operation tests."""

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
            "embodied AI companies in Hangzhou",
            task_id="discover_companies",
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
        result = _discovery_tool()("unknown robotics market", task_id="market")

    assert search_mock.call_count == 3
    assert result["resolution"] == "no_evidence"
    assert result["sources"] == []
    assert result["summary"]["healthy_provider_count"] == 3


def test_public_web_search_fetches_a_user_supplied_url_directly() -> None:
    research_tools = _research_module()
    fetched = {
        "requested_url": "https://example.test/report",
        "url": "https://example.test/report",
        "title": "Research report",
        "content_excerpt": "evidence " * 500,
        "usable": True,
        "error": None,
    }

    with (
        patch.object(research_tools, "_fetch_source", return_value=fetched) as fetch_mock,
        patch.object(research_tools, "_search_provider") as search_mock,
    ):
        result = _discovery_tool()(
            "https://example.test/report",
            task_id="market",
        )

    fetch_mock.assert_called_once_with("https://example.test/report")
    search_mock.assert_not_called()
    assert result["resolution"] == "sourced"
    assert len(result["sources"][0]["content_excerpt"]) == 2_000


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
