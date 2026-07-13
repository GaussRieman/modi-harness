"""Trusted Research Assistant Web Operations."""

from __future__ import annotations

import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tool(name: str) -> Callable[..., dict[str, Any]]:
    agent = discover_agents(cwd=REPO_ROOT, plugins=[]).registry.resolve(
        "research-assistant"
    ).agent
    return cast(
        Callable[..., dict[str, Any]],
        next(binding.handler for binding in agent.tools if binding.spec["name"] == name),
    )


class _Response:
    def __init__(
        self,
        payload: bytes,
        *,
        url: str = "https://example.test/source",
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        self._payload = payload
        self._url = url
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._payload

    def geturl(self) -> str:
        return self._url


def test_web_search_encodes_unicode_query_and_returns_candidates() -> None:
    payload = b"""<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>Company record</title>
        <link>https://example.test/company</link>
        <description>&lt;b&gt;Public&lt;/b&gt; company information</description>
      </item>
      <item>
        <title>Embodied AI</title>
        <link>https://example.test/technology</link>
        <description>Technical overview</description>
      </item>
    </channel></rss>"""
    seen_urls: list[str] = []

    def open_search(request, *, timeout):
        assert timeout == 15
        seen_urls.append(request.full_url)
        return _Response(payload)

    with patch.object(urllib.request, "urlopen", side_effect=open_search):
        result = _tool("web_search")("拉格朗日 具身智能", limit=1)

    assert len(seen_urls) == 1
    assert "拉格朗日" not in seen_urls[0]
    assert "%E6%8B%89" in seen_urls[0]
    assert result == {
        "query": "拉格朗日 具身智能",
        "provider": "bing_rss",
        "search_url": seen_urls[0],
        "results": [
            {
                "title": "Company record",
                "url": "https://example.test/company",
                "snippet": "Public company information",
            }
        ],
        "error": None,
        "guidance": (
            "Fetch relevant candidates. If the search budget yields no traceable source, "
            "complete with these search records and explicit limitations."
        ),
    }


def test_fetch_url_rejects_non_url_without_dispatching() -> None:
    with patch.object(urllib.request, "urlopen") as urlopen:
        result = _tool("fetch_url")("没有")

    urlopen.assert_not_called()
    assert result == {
        "url": "没有",
        "error": "fetch_url requires an absolute http(s) URL",
    }


def test_fetch_url_encodes_unicode_iri_before_dispatch() -> None:
    seen_urls: list[str] = []

    def open_url(request, *, timeout):
        assert timeout == 20
        seen_urls.append(request.full_url)
        return _Response(b"source text", url=request.full_url)

    with patch.object(urllib.request, "urlopen", side_effect=open_url):
        result = _tool("fetch_url")("https://example.test/搜索?q=具身智能")

    assert len(seen_urls) == 1
    assert seen_urls[0].isascii()
    assert "%E6%90%9C%E7%B4%A2" in seen_urls[0]
    assert result["requested_url"] == "https://example.test/搜索?q=具身智能"
    assert result["content"] == "source text"


def test_digest_and_judge_accept_traceable_negative_research() -> None:
    query = "杭州拉格朗日具身智能科技有限公司"
    search_record = {
        "query": query,
        "provider": "bing_rss",
        "search_url": "https://www.bing.com/search?"
        + urllib.parse.urlencode({"q": query, "format": "rss"}),
        "results": [],
        "error": "search returned no results",
    }

    generated = _tool("generate_research_digest")(
        "这家公司的技术实力怎么样?",
        [search_record],
    )
    digest = generated["digest"]

    assert digest["evidence"] == []
    assert digest["quality_signals"]["search_count"] == 1
    assert digest["source_coverage"] == []
    assert all(item["evidence"] == [] for item in digest["task_results"])
    assert all(item["limitations"] for item in digest["task_results"])
    judgment = _tool("judge_research_digest")(digest)
    assert judgment["judgment"]["status"] == "passed"
    assert judgment["judgment"]["can_finalize"] is True
