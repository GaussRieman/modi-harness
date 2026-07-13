"""Trusted semantic completion predicates for Research Assistant Nodes."""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

from modi_harness.workflow import CompletionValidator


def validate_evidence_bundle(value: Any) -> bool:
    """Accept source-bound evidence or a traceable negative search result."""

    return _evidence_bundle_error(value) is None


def _evidence_bundle_error(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return "result must be an object"
    sources = value.get("sources")
    source_records = value.get("source_records")
    evidence = value.get("evidence")
    limitations = value.get("limitations")
    if not isinstance(sources, list):
        return "sources must be an array"
    if not isinstance(source_records, list):
        return "source_records must be an array"
    if not isinstance(evidence, list):
        return "evidence must be an array"
    if not isinstance(limitations, list):
        return "limitations must be an array"

    if not sources:
        if evidence:
            return "evidence must be empty when sources is empty"
        if not _has_meaningful_text(limitations):
            return "negative research requires at least one specific limitation"
        if not any(_is_search_record(item) for item in source_records):
            return "negative research requires one unmodified web_search result"
        return None
    if not evidence:
        return "positive research requires at least one evidence item"

    resolved_source_urls = [_source_url(item) for item in sources]
    if any(item is None for item in resolved_source_urls):
        return "each source must be an HTTP(S) URL or an object containing url"
    source_urls = {item for item in resolved_source_urls if item is not None}
    for index, item in enumerate(evidence):
        if not isinstance(item, Mapping):
            return f"evidence[{index}] must be an object"
        text = str(item.get("text") or item.get("claim") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        if not text:
            return f"evidence[{index}] requires text or claim"
        if source_url not in source_urls:
            return f"evidence[{index}].source_url must match a declared source"
    return None


def validate_research_briefing(value: Any) -> bool:
    """Accept source-bound results or explicit evidence-unavailable results."""

    return _research_briefing_error(value) is None


def _research_briefing_error(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return "result must be an object"
    if not all(
        isinstance(value.get(field), str) and bool(str(value[field]).strip())
        for field in ("research_question", "executive_summary")
    ):
        return "research_question and executive_summary must be non-empty strings"
    task_results = value.get("task_results")
    if not isinstance(task_results, list) or not task_results:
        return "task_results must be a non-empty array"
    has_negative_result = False
    for index, item in enumerate(task_results):
        if not isinstance(item, Mapping):
            return f"task_results[{index}] must be an object"
        if not isinstance(item.get("result"), str) or not str(item["result"]).strip():
            return f"task_results[{index}].result must be non-empty"
        evidence = item.get("evidence")
        if not isinstance(evidence, list):
            return f"task_results[{index}].evidence must be an array"
        if evidence:
            if not all(_is_http_url(source) for source in evidence):
                return f"task_results[{index}].evidence must contain only HTTP(S) URLs"
            continue
        if not isinstance(item.get("limitations"), list) or not _has_meaningful_text(
            item["limitations"]
        ):
            return f"task_results[{index}] without evidence requires limitations"
        has_negative_result = True

    recommendations = value.get("recommendations")
    source_limitations = value.get("source_limitations")
    if not isinstance(recommendations, list) or not isinstance(source_limitations, list):
        return "recommendations and source_limitations must be arrays"
    if has_negative_result and not _has_meaningful_text(source_limitations):
        return "source_limitations is required when any task has no evidence"
    return None


def _source_url(value: Any) -> str | None:
    candidate = value.get("url") if isinstance(value, Mapping) else value
    if not _is_http_url(candidate):
        return None
    return str(candidate).strip()


def _is_search_record(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    query = value.get("query")
    if not isinstance(query, str) or not query.strip():
        return False
    if value.get("provider") != "bing_rss":
        return False
    search_url = value.get("search_url")
    if not _is_http_url(search_url):
        return False
    try:
        parsed = urllib.parse.urlsplit(str(search_url).strip())
        parameters = urllib.parse.parse_qs(parsed.query)
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != "www.bing.com"
        or parsed.path != "/search"
        or parameters.get("format") != ["rss"]
        or parameters.get("q") != [query.strip()]
    ):
        return False
    results = value.get("results")
    if not isinstance(results, list):
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
        id="validate_evidence_bundle",
        version="3",
        validate=validate_evidence_bundle,
        explain=_evidence_bundle_error,
    ),
    CompletionValidator(
        id="validate_research_briefing",
        version="3",
        validate=validate_research_briefing,
        explain=_research_briefing_error,
    ),
)

__all__ = [
    "RESEARCH_VALIDATORS",
    "validate_evidence_bundle",
    "validate_research_briefing",
]
