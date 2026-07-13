"""Trusted semantic completion predicates for Research Assistant Nodes."""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

from modi_harness.workflow import CompletionValidator


def validate_evidence_bundle(value: Any) -> bool:
    """Require every evidence item to resolve to a declared source URL."""

    if not isinstance(value, Mapping):
        return False
    sources = value.get("sources")
    evidence = value.get("evidence")
    limitations = value.get("limitations")
    if not isinstance(sources, list) or not sources:
        return False
    if not isinstance(evidence, list) or not evidence:
        return False
    if not isinstance(limitations, list):
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
    """Accept only a non-empty final briefing with source-bound task results."""

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
    for item in task_results:
        if not isinstance(item, Mapping):
            return False
        if not isinstance(item.get("result"), str) or not str(item["result"]).strip():
            return False
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not any(
            _is_http_url(source) for source in evidence
        ):
            return False
    return isinstance(value.get("recommendations"), list) and isinstance(
        value.get("source_limitations"), list
    )


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
        version="1",
        validate=validate_evidence_bundle,
    ),
    CompletionValidator(
        id="validate_research_briefing",
        version="1",
        validate=validate_research_briefing,
    ),
)

__all__ = [
    "RESEARCH_VALIDATORS",
    "validate_evidence_bundle",
    "validate_research_briefing",
]
