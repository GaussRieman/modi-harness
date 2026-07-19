"""Bounded public-Web research Operations."""

from __future__ import annotations

import concurrent.futures
import html
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from .. import confidence
from ..long_task import (
    authority_binding_fingerprint,
    canonical_source_type,
    normalize_authority_bindings,
    registrable_domain,
    verification_coverage_gap,
)

_PROVIDERS = ("bing_rss", "baidu", "duckduckgo")
_SEARCH_RESULTS_PER_PROVIDER = 4
_MAX_FETCH_ATTEMPTS = 5
_MAX_USABLE_SOURCES = 3
_SOURCE_EXCERPT_CHARS = 6_000
_DISCOVERY_EXCERPT_CHARS = 2_000
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_HTML_SEARCH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}
_HEALTHY_SEARCH_STATUSES = {"ok", "empty"}
_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def get_current_time() -> dict[str, Any]:
    """Return current UTC/local time plus a fresh single-use search token."""

    now = datetime.now(UTC)
    local = now.astimezone(_LOCAL_TIMEZONE)
    expires = now + timedelta(seconds=120)
    utc_time = _iso_time(now)
    local_time = local.isoformat(timespec="milliseconds")
    expires_at = _iso_time(expires)
    return {
        "utc_time": utc_time,
        "local_time": local_time,
        "timezone": "Asia/Shanghai",
        "current_date": local.date().isoformat(),
        "current_year": local.year,
        "time_token": secrets.token_urlsafe(24),
        "issued_at": utc_time,
        "expires_at": expires_at,
        "operation_summary": {
            "utc_time": utc_time,
            "local_time": local_time,
            "timezone": "Asia/Shanghai",
            "current_date": local.date().isoformat(),
            "expires_at": expires_at,
        },
    }


class _TextExtractor(HTMLParser):
    _SKIP_TAGS: ClassVar[set[str]] = {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
    }

    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.in_title = False
        self.title_chunks: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in self._SKIP_TAGS:
            self.skip_depth += 1
        elif lowered == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        elif lowered == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value or self.skip_depth:
            return
        if self.in_title:
            self.title_chunks.append(value)
        else:
            self.chunks.append(value)


class _AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict[str, str]] = []
        self._href: str | None = None
        self._css_class = ""
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        values = {key.lower(): value or "" for key, value in attrs}
        self._href = values.get("href")
        self._css_class = values.get("class", "")
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        title = " ".join("".join(self._chunks).split())
        if title:
            self.anchors.append(
                {"href": self._href, "title": title, "class": self._css_class}
            )
        self._href = None
        self._css_class = ""
        self._chunks = []


def public_web_research(
    subject: str,
    question: str = "",
    task_id: str = "",
    time_token: str = "",
) -> dict[str, Any]:
    """Search several public indexes and fetch a few strongly matching pages."""

    del time_token
    normalized_subject = " ".join(str(subject or "").split())
    normalized_question = " ".join(str(question or "").split())
    normalized_task_id = " ".join(str(task_id or "").split())
    if not normalized_subject:
        return {
            "subject": "",
            "question": normalized_question,
            "task_id": normalized_task_id,
            "queries": [],
            "search_records": [],
            "candidates": [],
            "sources": [],
            "fetch_records": [],
            "limitations": ["research subject is empty"],
        }

    queries = _query_variants(normalized_subject, normalized_question)
    search_records = _run_searches(queries)
    candidates = _rank_candidates(normalized_subject, search_records)
    fetch_records = _fetch_candidates(candidates)
    sources = [item for item in fetch_records if item["usable"]][:_MAX_USABLE_SOURCES]
    search_id = secrets.token_urlsafe(18)

    limitations: list[str] = []
    failed_providers = sorted(
        {
            str(record["provider"])
            for record in search_records
            if record.get("status") in {"blocked", "failed"}
        }
    )
    healthy_providers = {
        str(record["provider"])
        for record in search_records
        if record.get("status") in _HEALTHY_SEARCH_STATUSES
    }
    if failed_providers:
        limitations.append("search provider failures: " + ", ".join(failed_providers))
    if not candidates:
        if len(healthy_providers) >= 2:
            limitations.append(
                "the bounded public search produced no result with a reliable "
                "subject-name match"
            )
        else:
            limitations.append(
                "the public search was inconclusive because fewer than two providers "
                "returned healthy responses"
            )
    elif not sources:
        limitations.append(
            "matching candidates were found, but no candidate page yielded usable public content"
        )
    if not sources:
        limitations.append(
            "absence from these bounded public searches does not prove that the subject does not exist"
        )

    return {
        "subject": normalized_subject,
        "question": normalized_question,
        "task_id": normalized_task_id,
        "queries": queries,
        "search_records": search_records,
        "candidates": candidates[:8],
        "sources": sources,
        "fetch_records": fetch_records,
        "search_id": search_id,
        "limitations": limitations,
        "summary": {
            "provider_count": len(_PROVIDERS),
            "healthy_provider_count": len(healthy_providers),
            "query_count": len(queries),
            "relevant_candidate_count": len(candidates),
            "usable_source_count": len(sources),
        },
        "operation_summary": _search_operation_summary(
            search_id=search_id,
            task_id=normalized_task_id,
            searches=[{"query": item, "entity": normalized_subject} for item in queries],
            search_records=search_records,
            candidate_counts=[len(candidates)],
            sources=sources,
        ),
    }


def public_web_search(
    searches: list[dict[str, Any]],
    task_id: str,
    time_token: str,
) -> dict[str, Any]:
    """Search one or two entity-specific query intents with fair candidate coverage."""

    del time_token
    normalized_searches = _normalize_search_intents(searches)
    normalized_task_id = " ".join(str(task_id or "").split())
    if not normalized_searches or not normalized_task_id:
        raise ValueError("searches and task_id are required")

    search_records = _run_searches([item["query"] for item in normalized_searches])
    candidate_pools = [
        _rank_structured_candidates(
            item,
            [
                record
                for record in search_records
                if record.get("query_index") == index
            ],
            search_index=index,
        )
        for index, item in enumerate(normalized_searches)
    ]
    candidates = _round_robin_candidates(candidate_pools)
    fetch_records = _fetch_candidates(candidates)
    sources = [
        {
            **item,
            "content_excerpt": str(item.get("content_excerpt") or "")[
                :_DISCOVERY_EXCERPT_CHARS
            ],
        }
        for item in fetch_records
        if item["usable"]
    ][:_MAX_USABLE_SOURCES]
    search_id = secrets.token_urlsafe(18)
    healthy_providers = {
        str(record["provider"])
        for record in search_records
        if record.get("status") in _HEALTHY_SEARCH_STATUSES
    }
    failed_providers = sorted(
        {
            str(record["provider"])
            for record in search_records
            if record.get("status") in {"blocked", "failed"}
        }
    )
    if sources:
        resolution = "sourced"
    elif len(healthy_providers) >= 2:
        resolution = "no_evidence"
    else:
        resolution = "unavailable"
    limitations: list[str] = []
    if failed_providers:
        limitations.append("search provider failures: " + ", ".join(failed_providers))
    if resolution == "no_evidence":
        limitations.append("the public search returned no usable source for this question")
    elif resolution == "unavailable":
        limitations.append("public search services could not establish a usable result")
    return {
        "searches": normalized_searches,
        "task_id": normalized_task_id,
        "search_id": search_id,
        "resolution": resolution,
        "search_records": search_records,
        "candidates": candidates[:6],
        "sources": sources,
        "fetch_records": fetch_records,
        "limitations": limitations,
        "summary": {
            "healthy_provider_count": len(healthy_providers),
            "candidate_count": len(candidates),
            "candidate_count_by_search": [len(pool) for pool in candidate_pools],
            "usable_source_count": len(sources),
        },
        "operation_summary": _search_operation_summary(
            search_id=search_id,
            task_id=normalized_task_id,
            searches=normalized_searches,
            search_records=search_records,
            candidate_counts=[len(pool) for pool in candidate_pools],
            sources=sources,
        ),
    }


def verify_claim_evidence(
    task_id: str,
    claim: str,
    search_ids: list[str],
    items: list[dict[str, Any]],
    authority_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Tag and pre-filter candidate evidence for one claim before recording it.

    Deduplicates by source URL, validates each item's shape, drops items
    tagged ``unrelated``, and rejects two ``independent``-tagged items that
    share a domain (the Brain does not get the final say on independence; a
    plain domain check here does).
    """

    normalized_task_id = " ".join(str(task_id or "").split())
    normalized_claim = " ".join(str(claim or "").split())
    normalized_search_ids = list(
        dict.fromkeys(str(item or "").strip() for item in search_ids or [] if str(item).strip())
    )
    if not normalized_task_id or not normalized_claim or not normalized_search_ids:
        raise ValueError("task_id, claim, and search_ids are required")
    normalized_bindings = normalize_authority_bindings(authority_bindings or [])
    binding_fingerprint = authority_binding_fingerprint(normalized_bindings)
    allowed_types = {
        "official",
        "primary",
        "reputable_media",
        "industry_report",
        "job_board",
        "secondary",
    }
    seen_urls: set[str] = set()
    normalized_items: list[dict[str, Any]] = []
    evaluations: list[dict[str, Any]] = []
    evaluated_urls: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            raise ValueError("evidence items must be objects")
        source_url = str(item.get("source_url") or "").strip()
        proposed_source_type = " ".join(
            str(item.get("source_type") or "").split()
        ).lower()
        stance = " ".join(str(item.get("stance") or "").split()).lower()
        directness = " ".join(str(item.get("directness") or "").split()).lower()
        as_of = " ".join(str(item.get("as_of") or "").split())
        if not _is_http_url(source_url):
            raise ValueError("evidence requires a valid source_url")
        if proposed_source_type not in allowed_types:
            raise ValueError("evidence source_type is unsupported")
        source_type = canonical_source_type(
            source_url,
            proposed_source_type,
            normalized_bindings,
        )
        if stance not in {"supporting", "contradicting", "unrelated"}:
            raise ValueError("evidence stance must be supporting, contradicting, or unrelated")
        if directness not in {"direct", "indirect"}:
            raise ValueError("evidence directness must be direct or indirect")
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        evaluated_urls.append(source_url)
        evaluation = {
            "claim": normalized_claim,
            "source_url": source_url,
            "source_type": source_type,
            "stance": stance,
            "directness": directness,
            "independence": "independent"
            if bool(item.get("independent"))
            else "same_origin",
            **({"as_of": as_of} if as_of else {}),
        }
        evaluations.append(evaluation)
        if stance == "unrelated":
            continue
        normalized_items.append(dict(evaluation))
    domains_seen: set[str] = set()
    for entry in normalized_items:
        if entry["independence"] != "independent":
            continue
        domain = registrable_domain(entry["source_url"])
        if domain and domain in domains_seen:
            raise ValueError(
                f"two evidence items tagged independent share the domain {domain!r}; "
                "re-tag one as same_origin or remove it"
            )
        if domain:
            domains_seen.add(domain)
    verification_id = secrets.token_urlsafe(18)
    return {
        "verification_id": verification_id,
        "task_id": normalized_task_id,
        "claim": normalized_claim,
        "search_ids": normalized_search_ids,
        "evaluated_urls": evaluated_urls,
        "evaluations": evaluations,
        "evidence": normalized_items,
        "authority_binding_fingerprint": binding_fingerprint,
        "operation_summary": {
            "verification_id": verification_id,
            "task_id": normalized_task_id,
            "search_ids": normalized_search_ids,
            "evaluated_url_count": len(evaluated_urls),
            "evidence_count": len(normalized_items),
            "authority_binding_fingerprint": binding_fingerprint,
        },
    }


def record_research_finding(
    task_id: str,
    question: str,
    conclusion: str,
    implications: str,
    verification_method: str,
    status: str,
    verification_id: str = "",
    evidence: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
    verified_claim: str = "",
    authority_binding_fingerprint: str = "",
) -> dict[str, Any]:
    """Close one researched question or declare that it needs user help.

    Confidence is never supplied by the model; it is computed here from the
    tagged evidence and the task's ``verification_method`` (see
    ``agents.research_assistant.confidence``).
    """

    normalized_task_id = " ".join(str(task_id or "").split())
    normalized_question = " ".join(str(question or "").split())
    normalized_conclusion = " ".join(str(conclusion or "").split())
    normalized_implications = " ".join(str(implications or "").split())
    normalized_method = " ".join(str(verification_method or "").split()).lower()
    normalized_verification_id = str(verification_id or "").strip()
    normalized_status = " ".join(str(status or "").split()).lower()
    normalized_verified_claim = " ".join(str(verified_claim or "").split())
    normalized_authority_fingerprint = str(
        authority_binding_fingerprint or ""
    ).strip()
    normalized_evidence = _normalize_finding_evidence(evidence or [])
    normalized_citations = list(
        dict.fromkeys(item["source_url"] for item in normalized_evidence)
    )
    normalized_limitations = [
        " ".join(str(item).split())
        for item in limitations or []
        if " ".join(str(item).split())
    ]
    normalized_provenance = _normalize_finding_provenance(provenance or {})
    if not all(
        (
            normalized_task_id,
            normalized_question,
            normalized_conclusion,
            normalized_implications,
        )
    ):
        raise ValueError("task_id, question, conclusion, and implications are required")
    if normalized_method not in confidence.VERIFICATION_METHODS:
        raise ValueError("verification_method is unsupported")
    if normalized_status not in {"sourced", "blocked"}:
        raise ValueError("status must be sourced or blocked")
    if normalized_status == "blocked" and not normalized_limitations:
        raise ValueError("a blocked finding requires at least one limitation")
    if normalized_method == "unverifiable_flag" and normalized_status != "blocked":
        raise ValueError("unverifiable_flag tasks must be recorded as blocked without a search")
    if normalized_method != "unverifiable_flag" and not normalized_verification_id:
        raise ValueError("researched findings require verification_id")
    if not normalized_authority_fingerprint.startswith("sha256:"):
        raise ValueError("authority_binding_fingerprint is required")
    if normalized_method != "unverifiable_flag":
        if not normalized_verified_claim:
            raise ValueError("researched findings require the runtime-owned verified_claim")
        if normalized_conclusion != normalized_verified_claim:
            raise ValueError("conclusion must exactly match the verified claim")

    normalized_provenance["authority_binding_fingerprint"] = (
        normalized_authority_fingerprint
    )

    if normalized_status == "sourced":
        gap = _verification_coverage_gap(normalized_evidence, normalized_method)
        if gap:
            normalized_status = "blocked"
            normalized_confidence = "low"
            if gap not in normalized_limitations:
                normalized_limitations.append(gap)
        else:
            factors = confidence.score_finding(
                normalized_evidence,
                normalized_method,
                today=_provenance_reference_date(normalized_provenance),
            )
            normalized_confidence = factors["overall"]
    else:
        normalized_confidence = "low"
        gap = _verification_coverage_gap(normalized_evidence, normalized_method)
        if gap and gap not in normalized_limitations:
            normalized_limitations.append(gap)

    result: dict[str, Any] = {
        "task_id": normalized_task_id,
        "question": normalized_question,
        "conclusion": normalized_conclusion,
        "implications": normalized_implications,
        "confidence": normalized_confidence,
        "verification_method": normalized_method,
        "verification_id": normalized_verification_id,
        "status": normalized_status,
        "evidence": normalized_evidence,
        "citations": normalized_citations,
        "limitations": normalized_limitations,
        "provenance": normalized_provenance,
        "task_resolution": "completed" if normalized_status == "sourced" else "blocked",
    }
    result["operation_summary"] = {
        "task_id": normalized_task_id,
        "verification_id": normalized_verification_id or None,
        "status": normalized_status,
        "verification_method": normalized_method,
        "evidence_count": len(normalized_evidence),
        "citation_count": len(normalized_citations),
        "limitation_count": len(normalized_limitations),
        "search_count": len(normalized_provenance.get("searches") or []),
    }
    return result


def _normalize_finding_provenance(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("finding provenance must be an object")
    verification_id = str(value.get("verification_id") or "").strip()
    search_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in value.get("search_ids") or []
            if str(item or "").strip()
        )
    )
    evaluated_urls = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in value.get("evaluated_urls") or []
            if _is_http_url(str(item or "").strip())
        )
    )
    searches: list[dict[str, Any]] = []
    for item in value.get("searches") or []:
        if not isinstance(item, dict):
            raise ValueError("finding provenance searches must be objects")
        search_id = str(item.get("search_id") or "").strip()
        current_time = item.get("current_time")
        if not search_id or not isinstance(current_time, dict):
            raise ValueError("finding provenance search requires search_id and current_time")
        normalized_time = {
            key: str(current_time.get(key) or "").strip()
            for key in ("issued_at", "current_date", "timezone")
        }
        if not all(normalized_time.values()):
            raise ValueError("finding provenance current_time is incomplete")
        structured = [
            {
                "query": _clean_text(str(entry.get("query") or "")),
                "entity": _clean_text(str(entry.get("entity") or "")),
                "aliases": [
                    _clean_text(str(alias or ""))
                    for alias in entry.get("aliases") or []
                    if _clean_text(str(alias or ""))
                ],
                "dimension": _clean_text(str(entry.get("dimension") or "")),
            }
            for entry in item.get("structured_searches") or []
            if isinstance(entry, dict)
        ]
        if not structured or any(
            not entry["query"] or not entry["entity"] or not entry["dimension"]
            for entry in structured
        ):
            raise ValueError("finding provenance structured searches are incomplete")
        searches.append(
            {
                "search_id": search_id,
                "structured_searches": structured,
                "usable_urls": list(
                    dict.fromkeys(
                        str(url or "").strip()
                        for url in item.get("usable_urls") or []
                        if _is_http_url(str(url or "").strip())
                    )
                ),
                "current_time": normalized_time,
            }
        )
    raw_evaluations = value.get("evaluations", [])
    if not isinstance(raw_evaluations, list):
        raise ValueError("finding provenance evaluations must be an array")
    evaluations = _normalize_finding_evidence(
        raw_evaluations,
        allow_unrelated=True,
    )
    return {
        "verification_id": verification_id,
        "search_ids": search_ids,
        "evaluated_urls": evaluated_urls,
        "evaluations": evaluations,
        "searches": searches,
        **(
            {
                "authority_binding_fingerprint": str(
                    value.get("authority_binding_fingerprint") or ""
                ).strip()
            }
            if value.get("authority_binding_fingerprint")
            else {}
        ),
    }


def _provenance_reference_date(provenance: Mapping[str, Any]) -> date | None:
    searches = provenance.get("searches")
    if not isinstance(searches, list | tuple):
        return None
    for raw_search in reversed(searches):
        if not isinstance(raw_search, Mapping):
            continue
        current_time = raw_search.get("current_time")
        if not isinstance(current_time, Mapping):
            continue
        value = str(current_time.get("current_date") or "").strip()
        try:
            return date.fromisoformat(value)
        except ValueError:
            continue
    return None


def _normalize_finding_evidence(
    items: list[dict[str, Any]],
    *,
    allow_unrelated: bool = False,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    allowed_types = {
        "official",
        "primary",
        "reputable_media",
        "industry_report",
        "job_board",
        "secondary",
    }
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("evidence items must be objects")
        claim = " ".join(str(item.get("claim") or "").split())
        source_url = str(item.get("source_url") or "").strip()
        source_type = " ".join(str(item.get("source_type") or "").split()).lower()
        stance = " ".join(str(item.get("stance") or "").split()).lower()
        independence = " ".join(str(item.get("independence") or "").split()).lower()
        directness = " ".join(str(item.get("directness") or "").split()).lower()
        as_of = " ".join(str(item.get("as_of") or "").split())
        if not claim or not _is_http_url(source_url):
            raise ValueError("evidence requires a claim and source_url")
        if source_type not in allowed_types:
            raise ValueError("evidence source_type is unsupported")
        allowed_stances = {"supporting", "contradicting"}
        if allow_unrelated:
            allowed_stances.add("unrelated")
        if stance not in allowed_stances:
            expected = "supporting, contradicting, or unrelated" if allow_unrelated else (
                "supporting or contradicting"
            )
            raise ValueError(f"evidence stance must be {expected}")
        if independence not in {"independent", "same_origin"}:
            raise ValueError("evidence independence must be independent or same_origin")
        if directness not in {"direct", "indirect"}:
            raise ValueError("evidence directness must be direct or indirect")
        key = (claim, source_url)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "claim": claim,
                "source_url": source_url,
                "source_type": source_type,
                "stance": stance,
                "independence": independence,
                "directness": directness,
                **({"as_of": as_of} if as_of else {}),
            }
        )
    return normalized


def _verification_coverage_gap(
    evidence: Sequence[Mapping[str, Any]],
    method: str,
) -> str | None:
    return verification_coverage_gap(evidence, method)


def reject_research_request(reason: str, message: str) -> dict[str, Any]:
    """Return a deterministic refusal without performing any retrieval."""

    return {
        "executive_summary": " ".join(str(message or "").split()),
        "citations": [],
        "rejected": True,
        "reason": " ".join(str(reason or "").split()),
    }


def build_evidence_graph(
    report: Mapping[str, Any] | None = None,
    committed_results: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Render already-assembled key findings as a Mermaid evidence graph.

    Pure function: no model reasoning, no network access. ``report`` is the
    Node result the Harness already assembled from recorded findings
    (``key_findings``/``citations``/``limitations``/``direct_answer``); this
    only adds an ``evidence_graph`` field built from that same data, so every
    edge in the graph traces back to an evidence item already present in
    ``key_findings``.
    """

    if report is not None and not isinstance(report, Mapping):
        raise ValueError("report must be an object")
    if report is None and committed_results is None:
        raise ValueError("report or committed_results is required")
    result = _assemble_committed_research_report(report, committed_results)
    findings = result.get("key_findings")
    lines = ["flowchart LR"]
    if isinstance(findings, list | tuple) and findings:
        source_ids: dict[str, str] = {}
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            task_id = str(finding.get("task_id") or "")
            if not task_id:
                continue
            task_node = _mermaid_id("T", task_id)
            question = _mermaid_label(str(finding.get("question") or task_id))
            status = str(finding.get("status") or "")
            css_class = "sourced" if status == "sourced" else "limited"
            lines.append(f'{task_node}["{question}"]:::{css_class}')
            evidence = finding.get("evidence")
            if not isinstance(evidence, list | tuple):
                continue
            for item in evidence:
                if not isinstance(item, Mapping):
                    continue
                url = str(item.get("source_url") or "").strip()
                if not url:
                    continue
                if url not in source_ids:
                    source_ids[url] = _mermaid_id("S", str(len(source_ids) + 1))
                    domain = urllib.parse.urlsplit(url).hostname or url
                    lines.append(f'{source_ids[url]}["{_mermaid_label(domain)}"]:::source')
                stance = str(item.get("stance") or "supporting")
                arrow = "-.->" if stance == "contradicting" else "-->"
                lines.append(f"{task_node} {arrow} {source_ids[url]}")
        lines.append("classDef sourced fill:#e6ffed,stroke:#2ea44f")
        lines.append("classDef limited fill:#fff5e6,stroke:#d9822b")
        lines.append("classDef source fill:#eef2ff,stroke:#4c51bf")
    result["evidence_graph"] = "\n".join(lines)
    return result


def _assemble_committed_research_report(
    report: Mapping[str, Any] | None,
    committed_results: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    if committed_results is None:
        return dict(report or {})
    findings: list[dict[str, Any]] = []
    citations: list[str] = []
    limitations: list[str] = []
    direct_answer: list[str] = []
    seen_tasks: set[str] = set()
    for envelope in committed_results:
        if not isinstance(envelope, Mapping):
            raise ValueError("committed_results items must be objects")
        candidate = envelope.get("result", envelope.get("candidate", envelope))
        if not isinstance(candidate, Mapping):
            raise ValueError("committed research result must contain a result object")
        task_id = _clean_text(str(candidate.get("task_id") or envelope.get("task_id") or ""))
        if not task_id or task_id in seen_tasks:
            raise ValueError("committed research results require unique task_id values")
        seen_tasks.add(task_id)
        raw_status = _clean_text(str(candidate.get("status") or ""))
        if raw_status not in {"sourced", "blocked"}:
            raise ValueError("committed research result has unsupported status")
        evidence = candidate.get("evidence")
        provenance = candidate.get("provenance")
        if not isinstance(evidence, Sequence) or isinstance(evidence, str | bytes):
            raise ValueError("committed research result lacks evidence provenance")
        if not isinstance(provenance, Mapping):
            raise ValueError("committed research result lacks evidence provenance")
        finding_citations = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in candidate.get("citations") or []
                if _is_http_url(str(item or "").strip())
            )
        )
        evidence_urls = list(
            dict.fromkeys(
                str(item.get("source_url") or "").strip()
                for item in evidence
                if isinstance(item, Mapping)
                and _is_http_url(str(item.get("source_url") or "").strip())
            )
        )
        if finding_citations != evidence_urls:
            raise ValueError("committed research citations must exactly match evidence URLs")
        finding = {
            "task_id": task_id,
            "question": _clean_text(str(candidate.get("question") or task_id)),
            "conclusion": _clean_text(str(candidate.get("conclusion") or "")),
            "confidence": _clean_text(str(candidate.get("confidence") or "low")),
            "verification_method": _clean_text(
                str(candidate.get("verification_method") or "")
            ),
            "status": "sourced" if raw_status == "sourced" else "limited",
            "evidence": [dict(item) for item in evidence if isinstance(item, Mapping)],
            "provenance": dict(provenance),
        }
        findings.append(finding)
        if raw_status == "sourced":
            direct_answer.append(f'{finding["question"]}: {finding["conclusion"]}')
        else:
            direct_answer.append(
                f'{finding["question"]}: 未达到验证要求，详见限制'  # noqa: RUF001
            )
        for url in finding_citations:
            if url not in citations:
                citations.append(url)
        for item in candidate.get("limitations") or []:
            text = _clean_text(str(item or ""))
            if text and text not in limitations:
                limitations.append(text)
    return {
        "direct_answer": "\n\n".join(direct_answer),
        "key_findings": findings,
        "citations": citations,
        "limitations": limitations,
    }


def _mermaid_id(prefix: str, value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_") or "x"
    return f"{prefix}_{slug}"


def _mermaid_label(value: str) -> str:
    cleaned = " ".join(str(value or "").split()).replace('"', "'")
    return cleaned[:60]


def _query_variants(subject: str, question: str) -> list[str]:
    dimension = _question_dimension(question)
    exact_subject = subject.strip('"“”')
    variants = [subject, f'"{exact_subject}" {dimension or "公司"}']
    out: list[str] = []
    for item in variants:
        value = " ".join(item.split()).strip()
        if value and value not in out:
            out.append(value[:120])
    return out[:2]


def _question_dimension(question: str) -> str:
    if not question:
        return ""
    cleaned = re.sub(
        r"[\uff1f?\u3002.!\uff01,\uff0c;\uff1b:\uff1a]",
        " ",
        question,
    )
    ignored = {"如何", "怎么样", "看看", "研究", "调查", "这个", "这家"}
    parts = [part for part in cleaned.split() if part not in ignored]
    return " ".join(parts)[:48]


def _normalize_search_intents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_entities: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            raise ValueError("search items must be objects")
        query = _clean_text(str(item.get("query") or ""))[:240]
        entity = _clean_text(str(item.get("entity") or ""))[:240]
        dimension = _clean_text(str(item.get("dimension") or ""))[:120]
        aliases = list(
            dict.fromkeys(
                _clean_text(str(alias or ""))[:120]
                for alias in item.get("aliases") or []
                if _clean_text(str(alias or ""))
            )
        )[:6]
        if not query or not entity or not dimension:
            raise ValueError("each search requires query, entity, and dimension")
        entity_key = _entity_key(entity)
        if not entity_key:
            raise ValueError("search entity must contain letters, digits, or CJK text")
        if entity_key in seen_entities:
            raise ValueError("search entities must be distinct")
        seen_entities.add(entity_key)
        normalized.append(
            {
                "query": query,
                "entity": entity,
                "aliases": aliases,
                "dimension": dimension,
            }
        )
    if not 1 <= len(normalized) <= 2:
        raise ValueError("searches must contain one or two items")
    return normalized


def _run_searches(queries: list[str]) -> list[dict[str, Any]]:
    jobs = [
        (query_index, provider, query)
        for query_index, query in enumerate(queries)
        for provider in _PROVIDERS
    ]
    records: list[dict[str, Any] | None] = [None] * len(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as executor:
        futures = {
            executor.submit(_search_provider, provider, query): index
            for index, (_query_index, provider, query) in enumerate(jobs)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            query_index, provider, query = jobs[index]
            try:
                records[index] = {**future.result(), "query_index": query_index}
            except Exception as exc:  # provider isolation is part of the Operation contract
                records[index] = {
                    "query_index": query_index,
                    "provider": provider,
                    "query": query,
                    "search_url": _search_url(provider, query),
                    "status": "failed",
                    "results": [],
                    "error": str(exc),
                }
    return [record for record in records if record is not None]


def _search_provider(provider: str, query: str) -> dict[str, Any]:
    search_url = _search_url(provider, query)
    try:
        if provider == "bing_rss":
            results = _search_bing_rss(search_url)
            status = "ok" if results else "empty"
            error = None
        elif provider == "duckduckgo":
            search_url, results, status, error = _search_duckduckgo(query)
        else:
            results, body = _search_html_page(provider, search_url)
            status, error = _classify_html_search(provider, results, body)
    except urllib.error.HTTPError as exc:
        results = []
        status = "blocked" if exc.code in {401, 403, 429} else "failed"
        error = f"HTTP {exc.code}: {exc.reason}"
    except (OSError, UnicodeError, ValueError, ET.ParseError, urllib.error.URLError) as exc:
        results = []
        status = "failed"
        error = str(exc)
    return {
        "provider": provider,
        "query": query,
        "search_url": search_url,
        "status": status,
        "results": results[:_SEARCH_RESULTS_PER_PROVIDER],
        "error": error,
    }


def _search_url(provider: str, query: str) -> str:
    if provider == "bing_rss":
        return "https://www.bing.com/search?" + urllib.parse.urlencode(
            {"q": query, "format": "rss"}
        )
    if provider == "baidu":
        return "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
    if provider == "duckduckgo":
        return "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    raise ValueError(f"unsupported search provider {provider!r}")


def _duckduckgo_lite_url(query: str) -> str:
    return "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})


def _search_bing_rss(search_url: str) -> list[dict[str, str]]:
    payload, _final_url, _content_type = _read_url(search_url, timeout=6, limit=1_000_000)
    root = ET.fromstring(payload)
    results: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = " ".join((item.findtext("title") or "").split())
        url = (item.findtext("link") or "").strip()
        snippet = _strip_markup(item.findtext("description") or "")
        if title and _is_http_url(url):
            results.append({"title": title, "url": url, "snippet": snippet[:300]})
    return results


def _search_duckduckgo(
    query: str,
) -> tuple[str, list[dict[str, str]], str, str | None]:
    primary_url = _search_url("duckduckgo", query)
    primary = _search_html_attempt("duckduckgo", primary_url, endpoint="html")
    if primary[1] in _HEALTHY_SEARCH_STATUSES:
        return primary_url, primary[0], primary[1], primary[2]

    lite_url = _duckduckgo_lite_url(query)
    fallback = _search_html_attempt("duckduckgo", lite_url, endpoint="lite")
    if fallback[1] in _HEALTHY_SEARCH_STATUSES:
        return lite_url, fallback[0], fallback[1], fallback[2]

    statuses = {primary[1], fallback[1]}
    status = "blocked" if "blocked" in statuses else "failed"
    errors = [
        f"html: {primary[2] or primary[1]}",
        f"lite: {fallback[2] or fallback[1]}",
    ]
    return lite_url, [], status, "; ".join(errors)


def _search_html_attempt(
    provider: str,
    search_url: str,
    *,
    endpoint: str,
) -> tuple[list[dict[str, str]], str, str | None]:
    try:
        results, body = _search_html_page(provider, search_url, endpoint=endpoint)
        status, error = _classify_html_search(provider, results, body)
        return results, status, error
    except urllib.error.HTTPError as exc:
        status = "blocked" if exc.code in {401, 403, 429} else "failed"
        return [], status, f"HTTP {exc.code}: {exc.reason}"
    except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
        return [], "failed", str(exc)


def _search_html(
    provider: str,
    search_url: str,
    *,
    endpoint: str = "html",
) -> list[dict[str, str]]:
    results, _body = _search_html_page(provider, search_url, endpoint=endpoint)
    return results


def _search_html_page(
    provider: str,
    search_url: str,
    *,
    endpoint: str = "html",
) -> tuple[list[dict[str, str]], str]:
    payload, _final_url, content_type = _read_url(
        search_url,
        timeout=6,
        limit=1_000_000,
        headers=_HTML_SEARCH_HEADERS,
    )
    body = _decode(payload, content_type)
    parser = _AnchorExtractor()
    parser.feed(body)
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in parser.anchors:
        if provider == "duckduckgo":
            result_class = "result__a" if endpoint == "html" else "result-link"
            if result_class not in anchor["class"]:
                continue
        url = _normalize_result_url(provider, anchor["href"])
        if url is None or url in seen:
            continue
        seen.add(url)
        results.append({"title": anchor["title"][:180], "url": url, "snippet": ""})
        if len(results) >= _SEARCH_RESULTS_PER_PROVIDER:
            break
    return results, body


def _classify_html_search(
    provider: str,
    results: list[dict[str, str]],
    body: str,
) -> tuple[str, str | None]:
    if results:
        return "ok", None

    raw = html.unescape(body).lower()
    normalized = _clean_text(_strip_markup(body)).lower()
    blocked_markers = (
        "anomaly-modal",
        "bots use duckduckgo too",
        "请输入验证码",
        "访问过于频繁",
        "安全验证",
        "captcha",
        "verify you are human",
        "access denied",
    )
    if any(marker in normalized or marker in raw for marker in blocked_markers):
        return "blocked", "search response was blocked by access control"

    empty_markers = {
        "duckduckgo": ("no results.", "no results found for"),
        "baidu": ("抱歉没有找到", "没有找到相关结果", "未找到相关结果"),
    }
    if any(marker in normalized for marker in empty_markers.get(provider, ())):
        return "empty", None
    if len(normalized) < 120:
        return "failed", "search response contained too little readable content"
    return "failed", "search response did not contain recognized result or empty-state markup"


def _normalize_result_url(provider: str, href: str) -> str | None:
    value = html.unescape(str(href or "").strip())
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("/"):
        host = "www.baidu.com" if provider == "baidu" else "html.duckduckgo.com"
        value = f"https://{host}{value}"
    if not _is_http_url(value):
        return None
    parts = urllib.parse.urlsplit(value)
    hostname = (parts.hostname or "").lower()
    if provider == "duckduckgo" and hostname.endswith("duckduckgo.com"):
        target = urllib.parse.parse_qs(parts.query).get("uddg", [""])[0]
        return target if _is_http_url(target) else None
    if provider == "baidu" and hostname.endswith("baidu.com"):
        if parts.path != "/link":
            return None
    return value


def _rank_candidates(
    subject: str,
    search_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in search_records:
        for result in record.get("results") or []:
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "").strip()
            title = " ".join(str(result.get("title") or "").split())
            snippet = " ".join(str(result.get("snippet") or "").split())[:300]
            if not title or not _is_http_url(url):
                continue
            score = _relevance_score(subject, title, snippet, url)
            if score < 6:
                continue
            key = _canonical_url(url)
            candidate = merged.setdefault(
                key,
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "score": score,
                    "providers": [],
                    "queries": [],
                },
            )
            candidate["score"] = max(int(candidate["score"]), score)
            provider = str(record.get("provider") or "")
            query = str(record.get("query") or "")
            if provider and provider not in candidate["providers"]:
                candidate["providers"].append(provider)
            if query and query not in candidate["queries"]:
                candidate["queries"].append(query)
    return sorted(
        merged.values(),
        key=lambda item: (-int(item["score"]), str(item["url"])),
    )


def _rank_query_candidates(
    query: str,
    search_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank provider-returned discovery results without entity identity filtering."""

    tokens = {
        token
        for token in re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", query.lower())
        if token not in {"what", "which", "with", "from", "that", "this", "china"}
    }
    merged: dict[str, dict[str, Any]] = {}
    for record in search_records:
        for index, result in enumerate(record.get("results") or []):
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "").strip()
            title = " ".join(str(result.get("title") or "").split())
            snippet = " ".join(str(result.get("snippet") or "").split())[:300]
            if not title or not _is_http_url(url):
                continue
            haystack = f"{title} {snippet}".lower()
            overlap = sum(token in haystack for token in tokens)
            score = max(1, 8 - index * 2) + overlap * 3
            key = _canonical_url(url)
            candidate = merged.setdefault(
                key,
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "score": score,
                    "providers": [],
                },
            )
            candidate["score"] = max(int(candidate["score"]), score)
            provider = str(record.get("provider") or "")
            if provider and provider not in candidate["providers"]:
                candidate["providers"].append(provider)
                candidate["score"] = int(candidate["score"]) + 2
    return sorted(
        merged.values(),
        key=lambda item: (-int(item["score"]), str(item["url"])),
    )


def _rank_structured_candidates(
    search: dict[str, Any],
    search_records: list[dict[str, Any]],
    *,
    search_index: int,
) -> list[dict[str, Any]]:
    entity_key = _entity_key(str(search["entity"]))
    alias_keys = {
        key
        for key in (_entity_key(str(item)) for item in search.get("aliases") or [])
        if len(key) >= 2 and key != entity_key
    }
    dimension_tokens = {
        token
        for token in re.findall(
            r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}",
            str(search["dimension"]).lower(),
        )
        if token not in {"what", "which", "with", "from", "that", "this", "china"}
    }
    merged: dict[str, dict[str, Any]] = {}
    for record in search_records:
        for result_index, result in enumerate(record.get("results") or []):
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "").strip()
            title = _clean_text(str(result.get("title") or ""))
            snippet = _clean_text(str(result.get("snippet") or ""))[:300]
            if not title or not _is_http_url(url):
                continue
            identity_haystack = _entity_key(
                f"{title} {snippet} {urllib.parse.urlsplit(url).path}"
            )
            text_haystack = f"{title} {snippet}".lower()
            entity_match = bool(entity_key and entity_key in identity_haystack)
            alias_matches = sum(key in identity_haystack for key in alias_keys)
            dimension_overlap = sum(token in text_haystack for token in dimension_tokens)
            score = max(1, 8 - result_index * 2)
            if entity_match:
                score += 18
            if alias_matches:
                score += min(alias_matches, 2) * 10
            score += dimension_overlap * 3
            key = _canonical_url(url)
            candidate = merged.setdefault(
                key,
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "score": score,
                    "providers": [],
                    "search_index": search_index,
                    "entity": search["entity"],
                    "entity_match": entity_match,
                },
            )
            candidate["score"] = max(int(candidate["score"]), score)
            candidate["entity_match"] = bool(candidate["entity_match"] or entity_match)
            provider = str(record.get("provider") or "")
            if provider and provider not in candidate["providers"]:
                candidate["providers"].append(provider)
                candidate["score"] = int(candidate["score"]) + 2
    return sorted(
        merged.values(),
        key=lambda item: (
            not bool(item["entity_match"]),
            -int(item["score"]),
            str(item["url"]),
        ),
    )


def _round_robin_candidates(pools: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    cursors = [0] * len(pools)
    while len(selected) < _MAX_FETCH_ATTEMPTS:
        added = False
        for pool_index, pool in enumerate(pools):
            while cursors[pool_index] < len(pool):
                candidate = pool[cursors[pool_index]]
                cursors[pool_index] += 1
                key = _canonical_url(str(candidate["url"]))
                if key in seen_urls:
                    continue
                selected.append(candidate)
                seen_urls.add(key)
                added = True
                break
            if len(selected) >= _MAX_FETCH_ATTEMPTS:
                break
        if not added:
            break
    return selected


def _relevance_score(subject: str, title: str, snippet: str, url: str) -> int:
    full, core, brand = _subject_aliases(subject)
    haystack = _search_key(f"{title} {snippet} {urllib.parse.urlsplit(url).path}")
    score = 0
    if len(full) >= 3 and full in haystack:
        score += 12
    if len(core) >= 2 and core in haystack:
        score += 8
    business_markers = (
        "公司",
        "企业",
        "科技",
        "技术",
        "智能",
        "机器人",
        "官网",
        "招聘",
        "company",
        "technology",
        "robot",
    )
    if len(brand) >= 2 and brand in haystack:
        score += 3
        if any(marker in haystack for marker in business_markers):
            score += 4
    hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    if score and any(
        marker in hostname
        for marker in ("qcc.com", "tianyancha.com", "aiqicha.baidu.com", "36kr.com")
    ):
        score += 2
    return score


def _subject_aliases(subject: str) -> tuple[str, str, str]:
    value = "".join(subject.split())
    full = _search_key(value)
    core_text = re.sub(
        r"(股份有限公司|有限责任公司|有限公司|集团有限公司|集团)$",
        "",
        value,
    )
    core_text = re.sub(
        r"^(北京|上海|天津|重庆|深圳|广州|杭州|苏州|南京|成都|武汉|西安|合肥|宁波|厦门|青岛|长沙)",
        "",
        core_text,
    )
    core_text = re.sub(r"(科技|技术|信息技术)$", "", core_text)
    core = _search_key(core_text) or full
    brand_text = re.sub(r"(具身智能|人工智能|智能科技|机器人|科技|技术|智能|公司)", "", core_text)
    brand = _search_key(brand_text) or core
    return full, core, brand


def _fetch_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = candidates[:_MAX_FETCH_ATTEMPTS]
    if not selected:
        return []
    records: list[dict[str, Any] | None] = [None] * len(selected)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as executor:
        futures = {
            executor.submit(_fetch_source, str(candidate["url"])): index
            for index, candidate in enumerate(selected)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                records[index] = future.result()
            except Exception as exc:  # one page cannot fail the whole research Operation
                records[index] = {
                    "requested_url": selected[index]["url"],
                    "url": selected[index]["url"],
                    "title": selected[index]["title"],
                    "content_excerpt": "",
                    "usable": False,
                    "error": str(exc),
                }
    return [record for record in records if record is not None]


def _fetch_source(url: str) -> dict[str, Any]:
    try:
        normalized_url = _normalize_http_url(url)
        payload, final_url, content_type = _read_url(
            normalized_url,
            timeout=8,
            limit=1_000_000,
        )
        text = _decode(payload, content_type)
        title = ""
        if "html" in content_type.lower() or "<html" in text[:500].lower():
            parser = _TextExtractor()
            parser.feed(text)
            title = " ".join(parser.title_chunks)
            text = "\n".join(parser.chunks)
        excerpt = _clean_text(text)[:_SOURCE_EXCERPT_CHARS]
        blocked = _blocked_reason(excerpt)
        return {
            "requested_url": url,
            "url": final_url,
            "title": _clean_text(title)[:180],
            "content_type": content_type,
            "content_excerpt": excerpt if blocked is None else "",
            "usable": blocked is None,
            "error": blocked,
        }
    except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
        return {
            "requested_url": url,
            "url": url,
            "title": "",
            "content_excerpt": "",
            "usable": False,
            "error": str(exc),
        }


def _read_url(
    url: str,
    *,
    timeout: int,
    limit: int,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str, str]:
    request_headers = {"User-Agent": _BROWSER_USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(limit)
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
    return payload, final_url, content_type


def _decode(payload: bytes, content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type, flags=re.I)
    charset = match.group(1).strip("\"'") if match else "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _blocked_reason(text: str) -> str | None:
    if len(text) < 120:
        return "page returned too little readable public content"
    lowered = text.lower()
    blocked_markers = (
        "请输入验证码",
        "访问过于频繁",
        "登录后查看",
        "安全验证",
        "captcha",
        "access denied",
        "verify you are human",
    )
    if any(marker in lowered for marker in blocked_markers):
        return "page is blocked by login, captcha, or access control"
    return None


def _normalize_http_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("an absolute http(s) URL is required")
    if parts.username is not None or parts.password is not None:
        raise ValueError("credentials in URLs are not allowed")
    hostname = parts.hostname.encode("idna").decode("ascii")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if parts.port is None else f"{hostname}:{parts.port}"
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query, safe="=&%/:?+,-._~")
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def _canonical_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(key, item) for key, item in query if not key.lower().startswith("utm_")]
    return urllib.parse.urlunsplit(
        (
            parts.scheme.lower(),
            (parts.netloc or "").lower(),
            parts.path.rstrip("/") or "/",
            urllib.parse.urlencode(query),
            "",
        )
    )


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _strip_markup(value: str) -> str:
    return _clean_text(re.sub(r"<[^>]+>", " ", html.unescape(value)))


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _search_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _entity_key(value: str) -> str:
    return _search_key(value)


def _iso_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _search_operation_summary(
    *,
    search_id: str,
    task_id: str,
    searches: list[dict[str, Any]],
    search_records: list[dict[str, Any]],
    candidate_counts: list[int],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    healthy = {
        str(record.get("provider") or "")
        for record in search_records
        if record.get("status") in _HEALTHY_SEARCH_STATUSES
    }
    failed = sorted(
        {
            str(record.get("provider") or "")
            for record in search_records
            if record.get("status") in {"blocked", "failed"}
        }
    )
    return {
        "search_id": search_id,
        "task_id": task_id,
        "searches": [
            {"query": item.get("query"), "entity": item.get("entity")} for item in searches
        ],
        "healthy_provider_count": len(healthy),
        "failed_providers": failed,
        "candidate_count_by_search": candidate_counts,
        "usable_sources": [
            {"url": item.get("url"), "title": item.get("title")} for item in sources
        ],
    }


GET_CURRENT_TIME_SPEC = {
    "name": "get_current_time",
    "description": (
        "Return the current UTC and Asia/Shanghai time plus a fresh single-use "
        "time_token. Call immediately before every public Web search invocation."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": False,
}

_TIME_PREREQUISITE = {
    "argument": "time_token",
    "issuer_adapter": "get_current_time",
    "issuer_output_field": "time_token",
    "issued_at_field": "issued_at",
    "ttl_seconds": 120,
}

PUBLIC_WEB_RESEARCH_SPEC = {
    "name": "public_web_research",
    "description": (
        "Run a bounded multi-provider public-Web investigation for the active "
        "research subject. It expands queries, ranks subject-name matches, fetches "
        "a few strong candidates, and returns compact source and search records."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "minLength": 1},
            "question": {"type": "string"},
            "task_id": {"type": "string"},
            "time_token": {"type": "string", "minLength": 1},
        },
        "required": ["subject", "time_token"],
        "additionalProperties": False,
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": False,
    "max_calls_per_node": 6,
    "fresh_output_prerequisite": _TIME_PREREQUISITE,
}

PUBLIC_WEB_SEARCH_SPEC = {
    "name": "public_web_search",
    "description": (
        "Search public Web sources for one research question. A search collects "
        "evidence but does not complete the TaskPlan item; verify the result with "
        "verify_claim_evidence before recording a finding. May be called up to "
        "twice per item: once to gather evidence, and once more only when "
        "verification found a gap that a different query could close."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "searches": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 240},
                        "entity": {"type": "string", "minLength": 1, "maxLength": 240},
                        "aliases": {
                            "type": "array",
                            "maxItems": 6,
                            "items": {"type": "string", "minLength": 1, "maxLength": 120},
                        },
                        "dimension": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                        },
                    },
                    "required": ["query", "entity", "dimension"],
                    "additionalProperties": False,
                },
            },
            "task_id": {"type": "string", "minLength": 1},
            "time_token": {"type": "string", "minLength": 1},
        },
        "required": ["searches", "task_id", "time_token"],
        "additionalProperties": False,
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": False,
    "max_calls_per_task": 2,
    "fresh_output_prerequisite": _TIME_PREREQUISITE,
}

VERIFY_CLAIM_EVIDENCE_SPEC = {
    "name": "verify_claim_evidence",
    "description": (
        "Tag candidate evidence for one claim before recording a finding. Pass "
        "distinct source URLs observed in this task's search results this round, "
        "each tagged supporting/contradicting/unrelated, independent/not "
        "independent from the other sources, and direct/indirect. Duplicate URLs "
        "are dropped; unrelated items are excluded from Finding evidence but "
        "retained in the complete evaluation manifest. Two items claimed "
        "independent that share a domain are rejected; re-tag one as not "
        "independent or drop it and call again. authority_bindings is immutable, "
        "runtime-owned context; any model-supplied value is discarded."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "minLength": 1},
            "claim": {"type": "string", "minLength": 1},
            "search_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_url": {"type": "string", "pattern": "^https?://"},
                        "source_type": {
                            "type": "string",
                            "enum": [
                                "official",
                                "primary",
                                "reputable_media",
                                "industry_report",
                                "job_board",
                                "secondary",
                            ],
                        },
                        "as_of": {"type": "string"},
                        "stance": {
                            "type": "string",
                            "enum": ["supporting", "contradicting", "unrelated"],
                        },
                        "independent": {"type": "boolean"},
                        "directness": {"type": "string", "enum": ["direct", "indirect"]},
                    },
                    "required": [
                        "source_url",
                        "source_type",
                        "stance",
                        "independent",
                        "directness",
                    ],
                    "additionalProperties": False,
                },
            },
            "authority_bindings": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "minLength": 1},
                        "source_type": {"enum": ["official", "primary"]},
                        "include_subdomains": {"type": "boolean"},
                    },
                    "required": ["host", "source_type", "include_subdomains"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "task_id",
            "claim",
            "search_ids",
            "items",
            "authority_bindings",
        ],
        "additionalProperties": False,
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
}

RECORD_RESEARCH_FINDING_SPEC = {
    "name": "record_research_finding",
    "description": (
        "Finish the active research question after evaluating its verified "
        "evidence. Pass the latest verification_id but do not copy evidence; the "
        "Runtime injects the exact normalized verification output. Use sourced when "
        "the question is answered; confidence is computed automatically and must "
        "not be supplied. Use blocked only after reasonable query rewrites are "
        "exhausted, or immediately for unverifiable_flag. verified_claim and "
        "authority_binding_fingerprint are runtime-owned and must not be authored."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "minLength": 1},
            "question": {"type": "string", "minLength": 1},
            "conclusion": {"type": "string", "minLength": 1},
            "implications": {"type": "string", "minLength": 1},
            "verification_method": {
                "type": "string",
                "enum": [
                    "single_source_sufficient",
                    "dual_independent_required",
                    "official_primary_required",
                    "contradiction_sensitive",
                    "unverifiable_flag",
                ],
            },
            "verification_id": {"type": "string"},
            "status": {"type": "string", "enum": ["sourced", "blocked"]},
            "verified_claim": {"type": "string"},
            "authority_binding_fingerprint": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}$",
            },
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string", "minLength": 1},
                        "source_url": {"type": "string", "pattern": "^https?://"},
                        "source_type": {
                            "type": "string",
                            "enum": [
                                "official",
                                "primary",
                                "reputable_media",
                                "industry_report",
                                "job_board",
                                "secondary",
                            ],
                        },
                        "stance": {
                            "type": "string",
                            "enum": ["supporting", "contradicting"],
                        },
                        "independence": {
                            "type": "string",
                            "enum": ["independent", "same_origin"],
                        },
                        "directness": {"type": "string", "enum": ["direct", "indirect"]},
                        "as_of": {"type": "string"},
                    },
                    "required": [
                        "claim",
                        "source_url",
                        "source_type",
                        "stance",
                        "independence",
                        "directness",
                    ],
                    "additionalProperties": False,
                },
            },
            "limitations": {"type": "array", "items": {"type": "string"}},
            "provenance": {
                "type": "object",
                "properties": {
                    "verification_id": {"type": "string"},
                    "search_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "evaluated_urls": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "^https?://"},
                        "uniqueItems": True,
                    },
                    "evaluations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim": {"type": "string", "minLength": 1},
                                "source_url": {
                                    "type": "string",
                                    "pattern": "^https?://",
                                },
                                "source_type": {
                                    "enum": [
                                        "official",
                                        "primary",
                                        "reputable_media",
                                        "industry_report",
                                        "job_board",
                                        "secondary",
                                    ]
                                },
                                "stance": {
                                    "enum": [
                                        "supporting",
                                        "contradicting",
                                        "unrelated",
                                    ]
                                },
                                "independence": {
                                    "enum": ["independent", "same_origin"]
                                },
                                "directness": {"enum": ["direct", "indirect"]},
                                "as_of": {"type": "string"},
                            },
                            "required": [
                                "claim",
                                "source_url",
                                "source_type",
                                "stance",
                                "independence",
                                "directness",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "searches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "search_id": {"type": "string", "minLength": 1},
                                "structured_searches": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "object"},
                                },
                                "usable_urls": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "pattern": "^https?://",
                                    },
                                },
                                "current_time": {
                                    "type": "object",
                                    "properties": {
                                        "issued_at": {"type": "string", "minLength": 1},
                                        "current_date": {"type": "string", "minLength": 1},
                                        "timezone": {"type": "string", "minLength": 1},
                                    },
                                    "required": ["issued_at", "current_date", "timezone"],
                                    "additionalProperties": False,
                                },
                            },
                            "required": [
                                "search_id",
                                "structured_searches",
                                "usable_urls",
                                "current_time",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "authority_binding_fingerprint": {
                        "type": "string",
                        "pattern": "^sha256:[0-9a-f]{64}$",
                    },
                },
                "required": [
                    "verification_id",
                    "search_ids",
                    "evaluated_urls",
                    "evaluations",
                    "searches",
                    "authority_binding_fingerprint",
                ],
                "additionalProperties": False,
            },
        },
        "required": [
            "task_id",
            "question",
            "conclusion",
            "implications",
            "verification_method",
            "status",
            "limitations",
            "verified_claim",
            "authority_binding_fingerprint",
        ],
        "additionalProperties": False,
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
}

BUILD_EVIDENCE_GRAPH_SPEC = {
    "name": "build_evidence_graph",
    "description": (
        "Deterministically assemble the user-facing report and Mermaid evidence "
        "graph from committed canonical Findings only. report is a legacy input "
        "and is ignored whenever committed_results is supplied."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "report": {"type": "object"},
            "committed_results": {
                "type": "array",
                "items": {"type": "object"},
            },
        },
        "anyOf": [
            {"required": ["report"]},
            {"required": ["committed_results"]},
        ],
        "additionalProperties": False,
    },
    "output_schema": {
        "type": "object",
        "required": ["evidence_graph"],
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
}

REJECT_RESEARCH_REQUEST_SPEC = {
    "name": "reject_research_request",
    "description": "Return the Router-provided refusal for a non-research request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "minLength": 1},
            "message": {"type": "string", "minLength": 1},
        },
        "required": ["reason", "message"],
        "additionalProperties": False,
    },
    "output_schema": {
        "type": "object",
        "required": ["executive_summary", "citations", "rejected", "reason"],
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
}

__all__ = [
    "BUILD_EVIDENCE_GRAPH_SPEC",
    "PUBLIC_WEB_RESEARCH_SPEC",
    "PUBLIC_WEB_SEARCH_SPEC",
    "RECORD_RESEARCH_FINDING_SPEC",
    "REJECT_RESEARCH_REQUEST_SPEC",
    "VERIFY_CLAIM_EVIDENCE_SPEC",
    "build_evidence_graph",
    "public_web_research",
    "public_web_search",
    "record_research_finding",
    "reject_research_request",
    "verify_claim_evidence",
]
