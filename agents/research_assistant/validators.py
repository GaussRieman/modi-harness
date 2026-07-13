"""Trusted final completion predicate for the single research Node."""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

from modi_harness.workflow import CompletionValidator


def validate_research_briefing(value: Any) -> bool:
    """Require source-bound claims or an auditable, bounded negative result."""

    return _research_briefing_error(value) is None


def _research_briefing_error(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return "result must be an object"
    if not all(
        isinstance(value.get(field), str) and bool(str(value[field]).strip())
        for field in ("research_question", "executive_summary")
    ):
        return "research_question and executive_summary must be non-empty strings"

    sources = value.get("sources")
    if not isinstance(sources, list) or not all(_is_http_url(item) for item in sources):
        return "sources must contain only HTTP(S) URLs"
    source_urls = {str(item).strip() for item in sources}

    search_records = value.get("search_records")
    if not isinstance(search_records, list) or not search_records:
        return "search_records must contain the public research Operation records"
    valid_search_records = [
        record for record in search_records if _is_public_search_record(record)
    ]
    if len(valid_search_records) != len(search_records):
        return "each search record must match a supported provider query and search URL"

    task_results = value.get("task_results")
    if not isinstance(task_results, list) or not task_results:
        return "task_results must be a non-empty array"
    has_evidence = False
    for index, item in enumerate(task_results):
        if not isinstance(item, Mapping):
            return f"task_results[{index}] must be an object"
        if not all(
            isinstance(item.get(field), str) and bool(str(item[field]).strip())
            for field in ("task", "result")
        ):
            return f"task_results[{index}] requires non-empty task and result"
        evidence = item.get("evidence")
        limitations = item.get("limitations")
        if not isinstance(evidence, list):
            return f"task_results[{index}].evidence must be an array"
        if not isinstance(limitations, list):
            return f"task_results[{index}].limitations must be an array"
        if evidence:
            has_evidence = True
            if not all(_is_http_url(url) and str(url).strip() in source_urls for url in evidence):
                return f"task_results[{index}].evidence must resolve to final sources"
        elif not _has_meaningful_text(limitations):
            return f"task_results[{index}] without evidence requires a specific limitation"

    recommendations = value.get("recommendations")
    source_limitations = value.get("source_limitations")
    if not isinstance(recommendations, list) or not isinstance(source_limitations, list):
        return "recommendations and source_limitations must be arrays"

    if has_evidence:
        if not source_urls:
            return "source-bound task results require final sources"
        return None

    if source_urls:
        return "sources must be empty when no task result cites evidence"
    providers = {str(record["provider"]) for record in valid_search_records}
    if len(providers) < 2:
        return "negative research requires search records from at least two providers"
    if not _has_meaningful_text(source_limitations):
        return "negative research requires explicit source_limitations"
    summary = str(value["executive_summary"]).lower()
    forbidden = ("公司不存在", "企业不存在", "主体不存在", "does not exist")
    if any(phrase in summary for phrase in forbidden):
        return "a bounded public-search miss cannot prove that the subject does not exist"
    return None


def _is_public_search_record(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    provider = value.get("provider")
    query = value.get("query")
    search_url = value.get("search_url")
    results = value.get("results")
    if (
        provider not in {"bing_rss", "baidu", "duckduckgo"}
        or not isinstance(query, str)
        or not query.strip()
        or not _is_http_url(search_url)
        or not isinstance(results, list)
    ):
        return False
    try:
        parsed = urllib.parse.urlsplit(str(search_url).strip())
        parameters = urllib.parse.parse_qs(parsed.query)
    except ValueError:
        return False
    expected = {
        "bing_rss": ("www.bing.com", "/search", "q"),
        "baidu": ("www.baidu.com", "/s", "wd"),
        "duckduckgo": ("html.duckduckgo.com", "/html/", "q"),
    }[str(provider)]
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != expected[0]
        or parsed.path != expected[1]
        or parameters.get(expected[2]) != [query.strip()]
    ):
        return False
    if provider == "bing_rss" and parameters.get("format") != ["rss"]:
        return False
    return all(
        isinstance(item, Mapping)
        and isinstance(item.get("title"), str)
        and bool(str(item["title"]).strip())
        and _is_http_url(item.get("url"))
        for item in results
    )


def _has_meaningful_text(value: list[Any]) -> bool:
    return any(isinstance(item, str) and bool(item.strip()) for item in value)


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


RESEARCH_VALIDATORS = (
    CompletionValidator(
        id="validate_research_briefing",
        version="4",
        validate=validate_research_briefing,
        explain=_research_briefing_error,
    ),
)

__all__ = ["RESEARCH_VALIDATORS", "validate_research_briefing"]
