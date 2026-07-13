"""Trusted semantic completion predicates for Research Assistant Nodes."""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

from modi_harness.workflow import CompletionValidator


def validate_evidence_bundle(value: Any) -> bool:
    """Accept source-bound evidence or a traceable negative search result."""

    if not isinstance(value, Mapping):
        return False
    sources = value.get("sources")
    source_records = value.get("source_records")
    evidence = value.get("evidence")
    limitations = value.get("limitations")
    if not isinstance(sources, list):
        return False
    if not isinstance(source_records, list):
        return False
    if not isinstance(evidence, list):
        return False
    if not isinstance(limitations, list):
        return False

    if not sources:
        return (
            not evidence
            and _has_meaningful_text(limitations)
            and any(_is_search_record(item) for item in source_records)
        )
    if not evidence:
        return False

    source_urls = {
        str(item.get("url") or "").strip()
        for item in sources
        if isinstance(item, Mapping) and _is_http_url(item.get("url"))
    }
    if not source_urls:
        return False
    for item in evidence:
        if not isinstance(item, Mapping):
            return False
        text = str(item.get("text") or item.get("claim") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        if not text or source_url not in source_urls:
            return False
    return True


def validate_research_briefing(value: Any) -> bool:
    """Accept source-bound results or explicit evidence-unavailable results."""

    if not isinstance(value, Mapping):
        return False
    if not all(
        isinstance(value.get(field), str) and bool(str(value[field]).strip())
        for field in ("research_question", "executive_summary")
    ):
        return False
    task_results = value.get("task_results")
    if not isinstance(task_results, list) or not task_results:
        return False
    has_negative_result = False
    for item in task_results:
        if not isinstance(item, Mapping):
            return False
        if not isinstance(item.get("result"), str) or not str(item["result"]).strip():
            return False
        evidence = item.get("evidence")
        if not isinstance(evidence, list):
            return False
        if evidence:
            if not all(_is_http_url(source) for source in evidence):
                return False
            continue
        if not isinstance(item.get("limitations"), list) or not _has_meaningful_text(
            item["limitations"]
        ):
            return False
        has_negative_result = True

    recommendations = value.get("recommendations")
    source_limitations = value.get("source_limitations")
    if not isinstance(recommendations, list) or not isinstance(source_limitations, list):
        return False
    return not has_negative_result or _has_meaningful_text(source_limitations)


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
        version="2",
        validate=validate_evidence_bundle,
    ),
    CompletionValidator(
        id="validate_research_briefing",
        version="2",
        validate=validate_research_briefing,
    ),
)

__all__ = [
    "RESEARCH_VALIDATORS",
    "validate_evidence_bundle",
    "validate_research_briefing",
]
