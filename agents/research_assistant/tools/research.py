"""Bounded public-Web research Operations."""

from __future__ import annotations

import concurrent.futures
import html
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from modi_harness._utils import compute_fingerprint

from .. import confidence
from ..long_task import (
    authority_binding_fingerprint,
    canonical_source_type,
    normalize_authority_bindings,
    registrable_domain,
    verification_coverage_gap,
)
from .doubao import DoubaoSearchConfig, search_doubao

_PROVIDERS = ("bing_rss", "baidu", "duckduckgo")
_SEARCH_RESULTS_PER_PROVIDER = 6
_MAX_FETCH_ATTEMPTS = 12
_MAX_FETCH_WORKERS = 5
_MAX_USABLE_SOURCES = 6
_MAX_SOURCE_BYTES = 8_000_000
_SOURCE_EXCERPT_CHARS = 6_000
_DISCOVERY_EXCERPT_CHARS = 2_000
_AUTHORITY_SNIPPET_MIN_CHARS = 160
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
_LOW_VALUE_DISCOVERY_HOSTS = {"hao123.com", "www.hao123.com"}
_LOW_QUALITY_SOURCE_SUFFIXES = (
    "51cto.com",
    "baike.com",
    "baike.baidu.com",
    "baijiahao.baidu.com",
    "blog.csdn.net",
    "cnblogs.com",
    "jianshu.com",
    "jishuzhan.net",
    "meipian.cn",
    "sohu.com",
    "163.com",
    "zhihu.com",
)
_HIGH_QUALITY_SOURCE_SUFFIXES = (
    "acm.org",
    "arxiv.org",
    "plato.stanford.edu",
    "iep.utm.edu",
    "britannica.com",
    "cambridge.org",
    "ieee.org",
    "ieee-ras.org",
    "iso.org",
    "itu.int",
    "nature.com",
    "ncbi.nlm.nih.gov",
    "oup.com",
    "science.org",
    "springer.com",
    "jstor.org",
)
_DIMENSION_QUERY_TERMS = {
    "academic_usage_patterns": "academic terminology research",
    "concept_relationship": "relationship differences",
    "definitions_and_core_features": "definition core features",
    "industry_usage_patterns": "industry products companies",
}
_COVERAGE_STATUSES = {"unexplored", "partial", "covered", "conflicted", "blocked"}
_INDUSTRY_CHAIN_COVERAGE = (
    (
        "upstream",
        "上游核心零部件与技术供应",
        "上游包含哪些核心零部件、关键技术、供应商和代表企业?",
        ("上游",),
    ),
    (
        "midstream",
        "中游本体、系统集成与平台",
        "中游本体制造、系统集成、软件平台和代表企业如何分工?",
        ("中游", "本体", "整机"),
    ),
    (
        "downstream",
        "下游应用场景与客户",
        "下游有哪些主要应用场景、客户类型和落地案例?",
        ("下游", "应用场景", "终端应用"),
    ),
    (
        "supporting-ecosystem",
        "支撑生态与基础设施",
        "产业发展依赖哪些数据、开发工具、测试认证、渠道和基础设施?",
        ("支撑生态", "配套生态", "基础设施"),
    ),
    (
        "leaders-and-competition",
        "龙头企业与竞争格局",
        "各环节有哪些国内外龙头企业, 它们的定位、优势和竞争关系是什么?",
        ("龙头", "竞争格局", "竞争"),
    ),
    (
        "commercialization",
        "商业化进展与量产验证",
        "产业目前的量产、订单、收入和规模化落地进展如何?",
        ("商业化", "量产", "落地进展", "商业进展"),
    ),
    (
        "bottlenecks",
        "技术、成本与规模化瓶颈",
        "当前技术、成本、供应链和商业模式面临哪些关键瓶颈?",
        ("瓶颈", "挑战", "制约"),
    ),
)
_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
_PROVIDER_CIRCUIT_SECONDS = 300.0
_PROVIDER_HEALTH_LOCK = threading.Lock()
_PROVIDER_BLOCKED_UNTIL: dict[str, float] = {}


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
            self.anchors.append({"href": self._href, "title": title, "class": self._css_class})
        self._href = None
        self._css_class = ""
        self._chunks = []


def public_web_research(
    subject: str,
    question: str = "",
    task_id: str = "",
    time_token: str = "",
    _doubao_config: DoubaoSearchConfig | None = None,
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

    subject_variants = _subject_identity_variants(normalized_subject, normalized_question)
    queries = _query_variants(normalized_subject, normalized_question)
    active_doubao_config = _doubao_config if _doubao_config and _doubao_config.enabled else None
    search_records = _run_searches(queries, doubao_config=active_doubao_config)
    candidates = _rank_candidates(subject_variants, search_records)
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
        limitations.append(
            "search provider failures: " + ", ".join(_unique_failure_labels(search_records))
        )
    if not candidates:
        if len(healthy_providers) >= 2:
            limitations.append(
                "the bounded public search produced no result with a reliable subject-name match"
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
        "search_records": _compact_search_records(search_records),
        "candidates": _compact_candidates(candidates[:8]),
        "sources": sources,
        "fetch_records": _compact_fetch_records(fetch_records),
        "search_id": search_id,
        "limitations": limitations,
        "summary": {
            "provider_count": len({str(item["provider"]) for item in search_records}),
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


def public_web_explore(
    request: str,
    time_token: str,
    queries: list[dict[str, Any]] | None = None,
    _doubao_config: DoubaoSearchConfig | None = None,
) -> dict[str, Any]:
    """Run complementary broad queries before the research map is created."""

    del time_token
    normalized_request = _clean_text(request)
    if not normalized_request:
        raise ValueError("request is required")
    query_plan = _normalize_exploration_queries(normalized_request, queries)
    query_values = [item["query"] for item in query_plan]
    active_doubao_config = _doubao_config if _doubao_config and _doubao_config.enabled else None
    search_records = _run_searches(query_values, doubao_config=active_doubao_config)
    candidate_pools = [
        _rank_query_candidates(
            item["query"],
            [record for record in search_records if record.get("query_index") == index],
            search_index=index,
        )
        for index, item in enumerate(query_plan)
    ]
    candidates = _round_robin_candidates(candidate_pools)
    fetch_records = _fetch_candidates(candidates)
    usable_sources = [
        {
            **item,
            "content_excerpt": str(item.get("content_excerpt") or "")[:_DISCOVERY_EXCERPT_CHARS],
        }
        for item in fetch_records
        if item.get("usable")
    ]
    sources = _select_usable_sources(
        usable_sources,
        search_count=len(query_plan),
        authority_bindings=(),
    )
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
    limitations: list[str] = []
    if failed_providers:
        limitations.append(
            "search provider failures: " + ", ".join(_unique_failure_labels(search_records))
        )
    if not sources:
        limitations.append("exploration search returned no usable public source")
    return {
        "request": normalized_request,
        "queries": query_values,
        "query_plan": query_plan,
        "search_id": search_id,
        "search_records": _compact_search_records(search_records),
        "candidates": _compact_candidates(candidates[:8]),
        "sources": sources,
        "fetch_records": _compact_fetch_records(fetch_records),
        "limitations": limitations,
        "summary": {
            "healthy_provider_count": len(healthy_providers),
            "query_count": len(query_plan),
            "candidate_count": len(candidates),
            "candidate_count_by_query": [len(pool) for pool in candidate_pools],
            "usable_source_count": len(sources),
        },
        "operation_summary": _search_operation_summary(
            search_id=search_id,
            task_id="explore",
            searches=[
                {
                    "query": item["query"],
                    "entity": normalized_request,
                    "dimension": item["purpose"],
                }
                for item in query_plan
            ],
            search_records=search_records,
            candidate_counts=[len(pool) for pool in candidate_pools],
            sources=sources,
        ),
    }


def public_web_search(
    searches: list[dict[str, Any]],
    task_id: str,
    time_token: str,
    authority_bindings: list[dict[str, Any]] | None = None,
    verification_method: str = "",
    _doubao_config: DoubaoSearchConfig | None = None,
) -> dict[str, Any]:
    """Search one or two entity-specific query intents with fair candidate coverage."""

    del time_token
    normalized_searches = _normalize_search_intents(searches)
    normalized_task_id = " ".join(str(task_id or "").split())
    normalized_bindings = normalize_authority_bindings(authority_bindings or [])
    normalized_method = _clean_text(verification_method).lower()
    if not normalized_searches or not normalized_task_id:
        raise ValueError("searches and task_id are required")

    active_doubao_config = _doubao_config if _doubao_config and _doubao_config.enabled else None
    indexed_queries = [
        (search_index, query)
        for search_index, item in enumerate(normalized_searches)
        for query in _structured_query_variants(item, normalized_bindings)
    ]
    search_records = _run_searches(
        [query for _, query in indexed_queries],
        doubao_config=active_doubao_config,
    )
    for record in search_records:
        variant_index = int(record.get("query_index") or 0)
        record["query_variant_index"] = variant_index
        record["query_index"] = indexed_queries[variant_index][0]
    candidate_pools = [
        _rank_structured_candidates(
            item,
            [record for record in search_records if record.get("query_index") == index],
            search_index=index,
            authority_bindings=normalized_bindings,
        )
        for index, item in enumerate(normalized_searches)
    ]
    candidates = _round_robin_candidates(candidate_pools)
    fetch_records = _fetch_candidates(candidates, normalized_bindings)
    usable_sources = [
        {
            **item,
            "content_excerpt": str(item.get("content_excerpt") or "")[:_DISCOVERY_EXCERPT_CHARS],
        }
        for item in fetch_records
        if item["usable"]
    ]
    sources = _select_usable_sources(
        usable_sources,
        search_count=len(normalized_searches),
        authority_bindings=normalized_bindings,
    )
    quality_gaps, follow_up_searches = _search_quality_gaps(
        normalized_searches,
        sources,
        normalized_bindings,
        normalized_method,
    )
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
        limitations.append(
            "search provider failures: " + ", ".join(_unique_failure_labels(search_records))
        )
    if resolution == "no_evidence":
        limitations.append("the public search returned no usable source for this question")
    elif resolution == "unavailable":
        limitations.append("public search services could not establish a usable result")
    return {
        "searches": normalized_searches,
        "task_id": normalized_task_id,
        "search_id": search_id,
        "resolution": resolution,
        "search_records": _compact_search_records(search_records),
        "candidates": _compact_candidates(candidates[:6]),
        "sources": sources,
        "fetch_records": _compact_fetch_records(fetch_records),
        "quality_gaps": quality_gaps,
        "follow_up_searches": follow_up_searches,
        "limitations": limitations,
        "summary": {
            "provider_count": len({str(item["provider"]) for item in search_records}),
            "healthy_provider_count": len(healthy_providers),
            "candidate_count": len(candidates),
            "candidate_count_by_search": [len(pool) for pool in candidate_pools],
            "usable_source_count": len(sources),
            "authoritative_source_count": sum(
                _is_authoritative_source(str(item.get("url") or ""), normalized_bindings)
                for item in sources
            ),
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


def initialize_deep_research(
    request: str,
    research_brief: dict[str, Any],
    exploration: dict[str, Any],
    research_map: dict[str, Any],
    current_time: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind exploration-derived questions to the user's exact research request."""

    normalized_request = _clean_text(request)
    if not normalized_request:
        raise ValueError("request is required")
    if not all(isinstance(item, dict) for item in (research_brief, exploration, research_map)):
        raise ValueError("research_brief, exploration, and research_map must be objects")
    if str(research_brief.get("original_request") or "") != request:
        raise ValueError("research_brief.original_request must exactly preserve request")
    research_map = _prepare_research_map(
        research_map,
        research_brief=research_brief,
        exploration=exploration,
        request=normalized_request,
    )
    raw_tasks = research_map.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("research map normalization did not produce tasks")
    coverage_map = _normalize_coverage_map(
        research_map.get("coverage_map"), raw_tasks, normalized_request
    )

    subject = _clean_text(str(research_map.get("subject") or normalized_request))[:240]
    dimensions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise ValueError("research tasks must be objects")
        question = _clean_text(str(raw.get("question") or ""))
        title = _clean_text(str(raw.get("title") or question))[:200]
        if not question or not title:
            raise ValueError("each research question requires title and question")
        raw_id = _clean_text(str(raw.get("id") or f"question-{index + 1}"))
        question_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_id).strip("-").lower()
        if not question_id:
            question_id = f"question-{index + 1}"
        while question_id in seen_ids:
            question_id = f"{question_id}-{index + 1}"
        seen_ids.add(question_id)
        raw_entities = raw.get("entities") or [subject]
        entities = _normalized_research_entities(raw_entities, fallback=subject)
        coverage_ids = list(
            dict.fromkeys(
                _clean_text(str(item))
                for item in raw.get("coverage_ids") or []
                if _clean_text(str(item))
            )
        )
        dimensions.append(
            {
                "id": question_id,
                "title": title,
                "criterion_id": "core-answer",
                "question": question,
                "entities": entities,
                "dimension": _clean_text(str(raw.get("dimension") or title))[:120],
                "rationale": _clean_text(str(raw.get("rationale") or ""))[:500],
                "information_gap": _clean_text(str(raw.get("information_gap") or question))[:500],
                "coverage_ids": coverage_ids,
                "verification_method": "single_source_sufficient",
                "authority_bindings": [],
                "depends_on": [],
                "priority": _bounded_priority(raw.get("priority"), default=80 - index * 5),
                "required": True,
            }
        )

    limitations = [
        _clean_text(str(item))
        for item in exploration.get("limitations") or []
        if _clean_text(str(item))
    ]
    constraints = list(
        dict.fromkeys(
            ["仅使用公开可访问资料"]
            + [
                _clean_text(str(item))
                for item in research_brief.get("constraints") or []
                if _clean_text(str(item))
            ]
        )
    )
    intent = {
        "intent_id": "research-" + compute_fingerprint(normalized_request)[:20],
        "version": 1,
        "status": "confirmed",
        "goal": normalized_request,
        "desired_outcome": "直接、准确地回答用户的原始问题",
        "success_criteria": [
            {
                "id": "core-answer",
                "description": "核心问题已由公开资料充分回答",
                "required": True,
                "verification_mode": "evidence",
                "validator_id": "research-criterion-verifier",
            }
        ],
        "constraints": constraints,
        "non_goals": [],
        "assumptions": [],
        "planning_context": {
            "subject": subject,
            "research_question": normalized_request,
            "candidate_dimensions": dimensions,
            "research_brief": _plain_json(research_brief),
            "landscape_map": _plain_json(research_map.get("landscape_map") or {}),
            "coverage_map": coverage_map,
            "task_map": _plain_json(dimensions),
            "exploration_queries": _plain_json(exploration.get("query_plan") or []),
            "exploration_search_id": str(exploration.get("search_id") or ""),
            "exploration_source_urls": [
                str(item.get("url") or "")
                for item in exploration.get("sources") or []
                if isinstance(item, dict) and _is_http_url(str(item.get("url") or ""))
            ],
            "exploration_sources": [
                {
                    "url": str(item.get("url") or ""),
                    "title": _clean_text(str(item.get("title") or ""))[:240],
                    "excerpt": _clean_text(
                        str(item.get("content_excerpt") or item.get("search_snippet") or "")
                    )[:1200],
                }
                for item in exploration.get("sources") or []
                if isinstance(item, dict) and _is_http_url(str(item.get("url") or ""))
            ][:6],
            "exploration_time": {
                key: _clean_text(str((current_time or {}).get(key) or ""))
                for key in ("issued_at", "current_date", "timezone")
            },
        },
    }
    return {
        "intent": intent,
        "research_context": {
            "research_brief": _plain_json(research_brief),
            "landscape_map": _plain_json(research_map.get("landscape_map") or {}),
            "coverage_map": coverage_map,
            "task_map": _plain_json(dimensions),
            "exploration_queries": _plain_json(exploration.get("query_plan") or []),
            "source_catalog": _plain_json(exploration.get("sources") or []),
            "conflict_register": _plain_json(
                (research_map.get("landscape_map") or {}).get("early_conflicts") or []
            ),
            "limitations": limitations,
        },
        "limitations": limitations,
        "operation_summary": {
            "task_count": len(dimensions),
            "coverage_count": len(coverage_map["items"]),
            "exploration_search_id": str(exploration.get("search_id") or ""),
            "exploration_source_count": len(
                [
                    item
                    for item in exploration.get("sources") or []
                    if isinstance(item, dict) and _is_http_url(str(item.get("url") or ""))
                ]
            ),
        },
    }


def _normalized_research_entities(value: Any, *, fallback: str) -> list[dict[str, Any]]:
    values = value if isinstance(value, list) else [value]
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values[:2]:
        if isinstance(raw, dict):
            name = _clean_text(str(raw.get("name") or raw.get("entity") or ""))[:240]
            aliases = [
                _clean_text(str(item))[:120]
                for item in raw.get("aliases") or []
                if _clean_text(str(item))
            ][:6]
        else:
            name = _clean_text(str(raw or ""))[:240]
            aliases = []
        key = _entity_key(name)
        if name and key and key not in seen:
            seen.add(key)
            entities.append({"name": name, "aliases": aliases})
    return entities or [{"name": fallback, "aliases": []}]


def _bounded_priority(value: Any, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, min(100, value))
    return max(0, min(100, default))


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
        proposed_source_type = " ".join(str(item.get("source_type") or "").split()).lower()
        stance = " ".join(str(item.get("stance") or "").split()).lower()
        directness = " ".join(str(item.get("directness") or "").split()).lower()
        as_of = " ".join(str(item.get("as_of") or "").split())
        excerpt = " ".join(str(item.get("excerpt") or "").split())[:600]
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
            "independence": "independent" if bool(item.get("independent")) else "same_origin",
            **({"as_of": as_of} if as_of else {}),
            **({"excerpt": excerpt} if excerpt else {}),
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
    source_urls: list[str] | None = None,
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

    del source_urls
    normalized_task_id = " ".join(str(task_id or "").split())
    normalized_question = " ".join(str(question or "").split())
    normalized_conclusion = " ".join(str(conclusion or "").split())
    normalized_implications = " ".join(str(implications or "").split())
    normalized_method = " ".join(str(verification_method or "").split()).lower()
    normalized_verification_id = str(verification_id or "").strip()
    normalized_status = " ".join(str(status or "").split()).lower()
    normalized_verified_claim = " ".join(str(verified_claim or "").split())
    normalized_authority_fingerprint = str(authority_binding_fingerprint or "").strip()
    normalized_evidence = _normalize_finding_evidence(evidence or [])
    normalized_citations = list(dict.fromkeys(item["source_url"] for item in normalized_evidence))
    normalized_limitations = [
        " ".join(str(item).split()) for item in limitations or [] if " ".join(str(item).split())
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

    normalized_provenance["authority_binding_fingerprint"] = normalized_authority_fingerprint

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
        excerpt = " ".join(str(item.get("excerpt") or "").split())[:600]
        if not claim or not _is_http_url(source_url):
            raise ValueError("evidence requires a claim and source_url")
        if source_type not in allowed_types:
            raise ValueError("evidence source_type is unsupported")
        allowed_stances = {"supporting", "contradicting"}
        if allow_unrelated:
            allowed_stances.add("unrelated")
        if stance not in allowed_stances:
            expected = (
                "supporting, contradicting, or unrelated"
                if allow_unrelated
                else ("supporting or contradicting")
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
                **({"excerpt": excerpt} if excerpt else {}),
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
    limitations = [
        _clean_text(str(item or ""))
        for item in (report or {}).get("limitations") or []
        if _clean_text(str(item or ""))
    ]
    has_synthesized_limitations = bool(limitations)
    fallback_answer: list[str] = []
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
        public_evidence = [
            {key: value for key, value in item.items() if key != "claim"}
            for item in evidence
            if isinstance(item, Mapping)
        ]
        public_provenance = {
            key: value for key, value in provenance.items() if key != "evaluations"
        }
        finding = {
            "task_id": task_id,
            "question": _clean_text(str(candidate.get("question") or task_id)),
            "conclusion": _clean_text(str(candidate.get("conclusion") or "")),
            "confidence": _clean_text(str(candidate.get("confidence") or "low")),
            "verification_method": _clean_text(str(candidate.get("verification_method") or "")),
            "status": "sourced" if raw_status == "sourced" else "limited",
            "evidence": public_evidence,
            "provenance": public_provenance,
        }
        findings.append(finding)
        if raw_status == "sourced":
            fallback_answer.append(f"{finding['question']}: {finding['conclusion']}")
        else:
            fallback_answer.append(
                f"{finding['question']}: 未达到验证要求，详见限制"  # noqa: RUF001
            )
        for url in finding_citations:
            if url not in citations:
                citations.append(url)
        if raw_status == "blocked" or not has_synthesized_limitations:
            for item in candidate.get("limitations") or []:
                text = _clean_text(str(item or ""))
                if text and text not in limitations:
                    limitations.append(text)
    synthesized = _clean_text(str((report or {}).get("direct_answer") or ""))
    all_sourced = all(item.get("status") == "sourced" for item in findings)
    return {
        "direct_answer": (
            synthesized if synthesized and all_sourced else "\n\n".join(fallback_answer)
        ),
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
    identities = _subject_identity_variants(subject, question)
    dimension = _question_dimension(question)
    exact_subject = subject.strip('"“”')
    variants = [subject]
    if len(identities) > 1:
        variants.append(identities[1])
    else:
        variants.append(f'"{exact_subject}" {dimension or "公司"}')
    out: list[str] = []
    for item in variants:
        value = " ".join(item.split()).strip()
        if value and value not in out:
            out.append(value[:120])
    return out[:2]


def _subject_identity_variants(subject: str, question: str) -> list[str]:
    """Expand bilingual labels and recover one obvious Router-corrected typo."""

    normalized_subject = _clean_text(subject)
    variants = _bilingual_subject_variants(normalized_subject)
    subject_key = _search_key(normalized_subject)
    question_key = _search_key(question)
    if len(subject_key) < 5 or len(question_key) < len(subject_key):
        return variants

    candidates: list[tuple[int, int, str]] = []
    for index in range(len(question_key) - len(subject_key) + 1):
        candidate = question_key[index : index + len(subject_key)]
        distance = sum(left != right for left, right in zip(subject_key, candidate, strict=True))
        if distance == 1:
            candidates.append((distance, index, candidate))
    if candidates:
        variants.append(min(candidates)[2])
    return list(dict.fromkeys(variants))


def _bilingual_subject_variants(subject: str) -> list[str]:
    """Split labels such as ``具身智能 (Embodied Intelligence/AI)`` for matching."""

    variants = [subject]
    for match in re.finditer(r"[\uFF08(]([^()\uFF08\uFF09]+)[\uFF09)]", subject):
        outside = _clean_text((subject[: match.start()] + " " + subject[match.end() :]).strip())
        inside = _clean_text(match.group(1))
        if outside:
            variants.append(outside)
        if inside:
            parts = [_clean_text(item) for item in re.split(r"\s*/\s*", inside)]
            if len(parts) > 1:
                prefix = parts[0].rsplit(" ", 1)[0] if " " in parts[0] else ""
                parts = [parts[0], *[f"{prefix} {item}".strip() for item in parts[1:]]]
            variants.extend(item for item in parts if item)
    return list(dict.fromkeys(variants))


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
    seen_intents: set[tuple[str, str, str]] = set()
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
        intent_key = (entity_key, _search_key(query), _search_key(dimension))
        if intent_key in seen_intents:
            raise ValueError("search intents must be distinct")
        seen_intents.add(intent_key)
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


def _normalize_exploration_queries(
    request: str,
    queries: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    if queries is None:
        return [{"query": request, "purpose": "direct request"}]
    if not isinstance(queries, list) or not 4 <= len(queries) <= 6:
        raise ValueError("exploration queries must contain four to six items")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in queries:
        if not isinstance(raw, dict):
            raise ValueError("exploration query items must be objects")
        query = _clean_text(str(raw.get("query") or ""))[:240]
        purpose = _clean_text(str(raw.get("purpose") or ""))[:160]
        key = _search_key(query)
        if not query or not purpose:
            raise ValueError("each exploration query requires query and purpose")
        if key in seen:
            raise ValueError("exploration queries must be distinct")
        seen.add(key)
        normalized.append({"query": query, "purpose": purpose})
    return normalized


def _prepare_research_map(
    value: Mapping[str, Any],
    *,
    research_brief: Mapping[str, Any],
    exploration: Mapping[str, Any],
    request: str,
) -> dict[str, Any]:
    """Turn a compact or partial model sketch into the canonical research map."""

    subject = _clean_text(str(value.get("subject") or ""))
    if not subject:
        brief_entities = research_brief.get("entities")
        if isinstance(brief_entities, list) and brief_entities:
            subject = _clean_text(str(brief_entities[0]))
    subject = (subject or _clean_text(str(research_brief.get("objective") or request)))[:240]

    source_count = len(
        [item for item in exploration.get("sources") or [] if isinstance(item, Mapping)]
    )
    landscape_map = _prepare_landscape_map(value, source_count=source_count)
    coverage_items = _prepare_coverage_items(
        value,
        exploration=exploration,
        request=request,
        source_count=source_count,
    )
    tasks = _prepare_initial_tasks(
        value.get("tasks"),
        coverage_items=coverage_items,
        subject=subject,
    )
    return {
        "subject": subject,
        "landscape_map": landscape_map,
        "coverage_map": {"items": coverage_items},
        "tasks": tasks,
    }


def _prepare_landscape_map(
    value: Mapping[str, Any],
    *,
    source_count: int,
) -> dict[str, Any]:
    legacy = value.get("landscape_map")
    legacy_map = legacy if isinstance(legacy, Mapping) else {}
    summary = _clean_text(str(value.get("landscape_summary") or legacy_map.get("summary") or ""))
    if not summary:
        summary = f"首轮探索获得 {source_count} 个可用来源, 继续按答案覆盖缺口研究。"

    raw_themes = value.get("themes")
    if not isinstance(raw_themes, list):
        raw_themes = legacy_map.get("themes")
    themes: list[dict[str, Any]] = []
    for raw in raw_themes if isinstance(raw_themes, list) else []:
        if isinstance(raw, Mapping):
            name = _clean_text(str(raw.get("name") or raw.get("summary") or ""))
            theme_summary = _clean_text(str(raw.get("summary") or name))
            source_urls = [
                str(item) for item in raw.get("source_urls") or [] if _is_http_url(item)
            ][:6]
        else:
            name = _clean_text(str(raw))
            theme_summary = name
            source_urls = []
        if name:
            themes.append(
                {"name": name[:160], "summary": theme_summary[:500], "source_urls": source_urls}
            )
        if len(themes) == 10:
            break

    return {
        "summary": summary,
        "themes": themes,
        "early_conflicts": _compact_text_items(
            value.get("early_conflicts", legacy_map.get("early_conflicts")), limit=6
        ),
        "unresolved_terms": _compact_text_items(
            value.get("unresolved_terms", legacy_map.get("unresolved_terms")), limit=6
        ),
    }


def _prepare_coverage_items(
    value: Mapping[str, Any],
    *,
    exploration: Mapping[str, Any],
    request: str,
    source_count: int,
) -> list[dict[str, Any]]:
    raw_items = value.get("coverage")
    legacy = value.get("coverage_map")
    if not isinstance(raw_items, list) and isinstance(legacy, Mapping):
        raw_items = legacy.get("items")

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_items if isinstance(raw_items, list) else []):
        if not isinstance(raw, Mapping):
            continue
        label = _clean_text(str(raw.get("label") or raw.get("question") or ""))[:200]
        question = _clean_text(str(raw.get("question") or label))
        if not label or not question:
            continue
        item_id = _unique_map_id(raw.get("id"), prefix="coverage", index=index, seen=seen_ids)
        status = _clean_text(str(raw.get("status") or ""))
        if status not in _COVERAGE_STATUSES:
            status = "partial" if source_count else "unexplored"
        items.append(
            {
                "id": item_id,
                "label": label,
                "question": question,
                "rationale": _clean_text(
                    str(raw.get("rationale") or f"直接支撑用户问题中的“{label}”。")
                )[:500],
                "required": raw.get("required") if isinstance(raw.get("required"), bool) else True,
                "status": status,
            }
        )
        if len(items) == 10:
            break

    if not items:
        items = _coverage_from_exploration(exploration, source_count=source_count)
        seen_ids = {str(item["id"]) for item in items}

    templates: Sequence[tuple[str, str, str, tuple[str, ...]]] = ()
    if _requires_complete_industry_chain(request):
        templates = _INDUSTRY_CHAIN_COVERAGE
    elif "龙头" in request:
        templates = (_INDUSTRY_CHAIN_COVERAGE[4],)
    if templates:
        items = _ensure_coverage_templates(
            items,
            templates=templates,
            seen_ids=seen_ids,
        )
    return items[:10]


def _coverage_from_exploration(
    exploration: Mapping[str, Any],
    *,
    source_count: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    query_plan = exploration.get("query_plan")
    for index, raw in enumerate(query_plan if isinstance(query_plan, list) else []):
        if not isinstance(raw, Mapping):
            continue
        query = _clean_text(str(raw.get("query") or ""))
        purpose = _clean_text(str(raw.get("purpose") or ""))
        if not query:
            continue
        label = (purpose or query)[:200]
        items.append(
            {
                "id": f"coverage-{index + 1}",
                "label": label,
                "question": query,
                "rationale": "该方向来自首轮互补探索查询, 直接用于补齐核心答案。",
                "required": True,
                "status": "partial" if source_count else "unexplored",
            }
        )
        if len(items) == 4:
            break
    if items:
        return items
    return [
        {
            "id": "coverage-core",
            "label": "核心问题",
            "question": "用户原始问题有哪些可由公开资料回答的关键事实和结论?",
            "rationale": "确保研究仍能继续回答用户的原始问题。",
            "required": True,
            "status": "partial" if source_count else "unexplored",
        }
    ]


def _ensure_coverage_templates(
    items: list[dict[str, Any]],
    *,
    templates: Sequence[tuple[str, str, str, tuple[str, ...]]],
    seen_ids: set[str],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    used_indexes: set[int] = set()
    for item_id, label, question, aliases in templates:
        match_index = next(
            (
                index
                for index, item in enumerate(items)
                if any(
                    alias in f"{item.get('label', '')} {item.get('question', '')}"
                    for alias in aliases
                )
            ),
            None,
        )
        if match_index is not None:
            if item_id == "leaders-and-competition":
                matched = items[match_index]
                matched_text = f"{matched.get('label', '')} {matched.get('question', '')}"
                if "龙头" not in matched_text or "竞争" not in matched_text:
                    matched["label"] = label
                    matched["question"] = (f"{matched.get('question', '')}; {question}").strip("; ")
            if match_index not in used_indexes:
                ordered.append(items[match_index])
                used_indexes.add(match_index)
            continue
        stable_id = item_id
        suffix = 2
        while stable_id in seen_ids:
            stable_id = f"{item_id}-{suffix}"
            suffix += 1
        seen_ids.add(stable_id)
        ordered.append(
            {
                "id": stable_id,
                "label": label,
                "question": question,
                "rationale": f"完整产业链答案必须覆盖{label}。",
                "required": True,
                "status": "unexplored",
            }
        )

    ordered.extend(item for index, item in enumerate(items) if index not in used_indexes)
    return ordered[:10]


def _prepare_initial_tasks(
    value: Any,
    *,
    coverage_items: list[dict[str, Any]],
    subject: str,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(value if isinstance(value, list) else []):
        if not isinstance(raw, Mapping):
            continue
        question = _clean_text(str(raw.get("question") or raw.get("title") or ""))
        title = _clean_text(str(raw.get("title") or question))[:200]
        if not question or not title:
            continue
        task_id = _unique_map_id(raw.get("id"), prefix="research", index=index, seen=seen_ids)
        coverage_ids = _resolve_coverage_refs(raw, coverage_items)
        tasks.append(
            {
                "id": task_id,
                "title": title,
                "question": question,
                "rationale": _clean_text(str(raw.get("rationale") or "补齐对应答案覆盖缺口。"))[
                    :500
                ],
                "information_gap": _clean_text(str(raw.get("information_gap") or question))[:500],
                "coverage_ids": coverage_ids,
                "entities": _normalized_research_entities(
                    raw.get("entities") or [subject], fallback=subject
                ),
                "dimension": _clean_text(str(raw.get("dimension") or title))[:120],
                "priority": _bounded_priority(raw.get("priority"), default=90 - index * 5),
            }
        )
        if len(tasks) == 4:
            break

    assigned = {item for task in tasks for item in task["coverage_ids"]}
    uncovered = [item for item in coverage_items if item["id"] not in assigned]
    empty_tasks = [task for task in tasks if not task["coverage_ids"]]
    for task, item in zip(empty_tasks, uncovered, strict=False):
        _attach_coverage(task, item)
    assigned = {item for task in tasks for item in task["coverage_ids"]}
    uncovered = [item for item in coverage_items if item["id"] not in assigned]

    slots = 4 - len(tasks)
    if uncovered and slots:
        groups = _coverage_task_groups(uncovered, max_groups=slots)
        for group in groups:
            index = len(tasks)
            task_id = _unique_map_id(None, prefix="research", index=index, seen=seen_ids)
            labels = "、".join(str(item["label"]) for item in group)
            questions = "; ".join(str(item["question"]) for item in group)
            tasks.append(
                {
                    "id": task_id,
                    "title": labels[:200],
                    "question": questions,
                    "rationale": "补齐高质量答案仍缺失的必需覆盖范围。",
                    "information_gap": questions[:500],
                    "coverage_ids": [str(item["id"]) for item in group],
                    "entities": [{"name": subject, "aliases": []}],
                    "dimension": labels[:120],
                    "priority": 90 - index * 5,
                }
            )

    assigned = {item for task in tasks for item in task["coverage_ids"]}
    uncovered = [item for item in coverage_items if item["id"] not in assigned]
    if not tasks:
        raise ValueError("coverage normalization did not produce an initial research task")
    for index, item in enumerate(uncovered):
        _attach_coverage(tasks[index % len(tasks)], item)
    return tasks


def _coverage_task_groups(
    items: list[dict[str, Any]],
    *,
    max_groups: int,
) -> list[list[dict[str, Any]]]:
    group_count = min(max_groups, len(items))
    groups: list[list[dict[str, Any]]] = [[] for _ in range(group_count)]
    for index, item in enumerate(items):
        groups[index % group_count].append(item)
    return groups


def _attach_coverage(task: dict[str, Any], item: Mapping[str, Any]) -> None:
    item_id = str(item["id"])
    if item_id in task["coverage_ids"]:
        return
    task["coverage_ids"].append(item_id)
    question = _clean_text(str(item.get("question") or ""))
    if question and question not in task["question"]:
        task["question"] = f"{task['question']}; {question}"
        task["information_gap"] = task["question"][:500]


def _resolve_coverage_refs(
    task: Mapping[str, Any],
    coverage_items: Sequence[Mapping[str, Any]],
) -> list[str]:
    raw_refs = list(task.get("coverage_ids") or []) + list(task.get("coverage_labels") or [])
    resolved: list[str] = []
    for raw_ref in raw_refs:
        ref = _clean_text(str(raw_ref))
        ref_key = _search_key(ref)
        for item in coverage_items:
            item_id = str(item["id"])
            label_key = _search_key(str(item.get("label") or ""))
            if ref == item_id or (
                ref_key
                and label_key
                and (ref_key == label_key or ref_key in label_key or label_key in ref_key)
            ):
                if item_id not in resolved:
                    resolved.append(item_id)
                break
    if resolved:
        return resolved

    task_key = _search_key(
        f"{task.get('title', '')} {task.get('question', '')} {task.get('dimension', '')}"
    )
    for item in coverage_items:
        label_key = _search_key(str(item.get("label") or ""))
        if label_key and label_key in task_key:
            resolved.append(str(item["id"]))
    return resolved


def _unique_map_id(
    value: Any,
    *,
    prefix: str,
    index: int,
    seen: set[str],
) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_-]+", "-", _clean_text(str(value or ""))).strip("-").lower()
    candidate = candidate or f"{prefix}-{index + 1}"
    base = candidate
    suffix = 2
    while candidate in seen:
        candidate = f"{base}-{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _compact_text_items(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:limit] if (text := _clean_text(str(item)))]


def _requires_complete_industry_chain(request: str) -> bool:
    return "产业链" in request and ("完整" in request or "全产业链" in request)


def _normalize_coverage_map(
    value: Any,
    tasks: Sequence[Mapping[str, Any]],
    request: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("research_map.coverage_map must be an object")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 10:
        raise ValueError("coverage_map.items must contain one to ten items")
    items: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("coverage items must be objects")
        item_id = _clean_text(str(raw.get("id") or ""))
        label = _clean_text(str(raw.get("label") or ""))
        question = _clean_text(str(raw.get("question") or ""))
        rationale = _clean_text(str(raw.get("rationale") or ""))
        status = _clean_text(str(raw.get("status") or ""))
        required = raw.get("required")
        if not all((item_id, label, question, rationale)):
            raise ValueError("coverage items require id, label, question, and rationale")
        if item_id in known_ids:
            raise ValueError("coverage item ids must be unique")
        if not isinstance(required, bool):
            raise ValueError("coverage item required must be boolean")
        if status not in _COVERAGE_STATUSES:
            raise ValueError("coverage item status is unsupported")
        known_ids.add(item_id)
        items.append(
            {
                "id": item_id,
                "label": label,
                "question": question,
                "rationale": rationale,
                "required": required,
                "status": status,
            }
        )

    task_coverage: set[str] = set()
    for raw in tasks:
        if not isinstance(raw, Mapping):
            raise ValueError("research tasks must be objects")
        coverage_ids = raw.get("coverage_ids")
        if not isinstance(coverage_ids, list) or not coverage_ids:
            raise ValueError("each research task must reference coverage_ids")
        normalized_ids = {_clean_text(str(item)) for item in coverage_ids}
        unknown = sorted(normalized_ids - known_ids)
        if unknown:
            raise ValueError("research task references unknown coverage: " + ", ".join(unknown))
        task_coverage.update(normalized_ids)
    missing = sorted(
        item["id"]
        for item in items
        if item["required"] and item["status"] != "covered" and item["id"] not in task_coverage
    )
    if missing:
        raise ValueError("required coverage lacks an initial research task: " + ", ".join(missing))

    if _requires_complete_industry_chain(request):
        coverage_text = " ".join(f"{item['label']} {item['question']}" for item in items)
        required_concepts = {
            "upstream": ("上游",),
            "midstream": ("中游", "本体", "整机"),
            "downstream": ("下游", "应用场景", "终端应用"),
            "supporting ecosystem": ("支撑生态", "配套生态", "基础设施"),
            "leading companies": ("龙头",),
            "competition": ("竞争格局", "竞争"),
            "commercialization": ("商业化", "落地进展", "商业进展"),
            "bottlenecks": ("瓶颈", "挑战", "制约"),
        }
        omitted = [
            name
            for name, aliases in required_concepts.items()
            if not any(alias in coverage_text for alias in aliases)
        ]
        if omitted:
            raise ValueError("complete industry-chain coverage is missing: " + ", ".join(omitted))
    return {"items": items}


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    return value


def _run_searches(
    queries: list[str],
    *,
    doubao_config: DoubaoSearchConfig | None = None,
) -> list[dict[str, Any]]:
    providers = tuple(
        provider
        for provider in _active_providers(doubao_config)
        if not _provider_circuit_is_open(provider)
    )
    jobs = [
        (query_index, provider, query)
        for query_index, query in enumerate(queries)
        for provider in providers
    ]
    records: list[dict[str, Any] | None] = [None] * len(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as executor:
        futures = {}
        for index, (_query_index, provider, query) in enumerate(jobs):
            if provider == "doubao" and doubao_config is not None:
                future = executor.submit(
                    _search_provider,
                    provider,
                    query,
                    doubao_config=doubao_config,
                )
            else:
                future = executor.submit(_search_provider, provider, query)
            futures[future] = index
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
    completed = [record for record in records if record is not None]
    _update_search_provider_health(providers, completed)
    return completed


def _provider_circuit_is_open(provider: str) -> bool:
    now = time.monotonic()
    with _PROVIDER_HEALTH_LOCK:
        blocked_until = _PROVIDER_BLOCKED_UNTIL.get(provider, 0.0)
        if blocked_until <= now:
            _PROVIDER_BLOCKED_UNTIL.pop(provider, None)
            return False
        return True


def _update_search_provider_health(
    providers: Sequence[str],
    records: Sequence[Mapping[str, Any]],
) -> None:
    now = time.monotonic()
    by_provider = {
        provider: [item for item in records if item.get("provider") == provider]
        for provider in providers
    }
    with _PROVIDER_HEALTH_LOCK:
        for provider, provider_records in by_provider.items():
            if provider_records and all(
                item.get("status") in {"blocked", "failed"} for item in provider_records
            ):
                _PROVIDER_BLOCKED_UNTIL[provider] = now + _PROVIDER_CIRCUIT_SECONDS
            elif any(item.get("status") in _HEALTHY_SEARCH_STATUSES for item in provider_records):
                _PROVIDER_BLOCKED_UNTIL.pop(provider, None)


def _reset_search_provider_health() -> None:
    with _PROVIDER_HEALTH_LOCK:
        _PROVIDER_BLOCKED_UNTIL.clear()


def _structured_query_variants(
    search: Mapping[str, Any],
    authority_bindings: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Use one focused query plus one authority-targeted query when available."""

    primary = _clean_text(str(search.get("query") or ""))[:100]
    aliases = [
        _clean_text(str(item or ""))
        for item in search.get("aliases") or []
        if _clean_text(str(item or ""))
    ]
    entity = _clean_text(str(search.get("entity") or ""))
    raw_dimension = _clean_text(str(search.get("dimension") or ""))
    dimension = _DIMENSION_QUERY_TERMS.get(
        raw_dimension.casefold(),
        _clean_text(raw_dimension.replace("_", " ")),
    )
    alias = next(
        (item for item in aliases if bool(re.search(r"[A-Za-z]", item))),
        aliases[0] if aliases else entity,
    )
    authority_hosts = _matching_authority_hosts(search, authority_bindings)
    secondary = (
        _clean_text(f"site:{authority_hosts[0]} {entity or alias} {dimension}")[:160]
        if authority_hosts
        else _clean_text(f"{alias} {dimension}")[:100]
    )
    return list(dict.fromkeys(item for item in (primary, secondary) if item))[:2]


def _matching_authority_hosts(
    search: Mapping[str, Any],
    authority_bindings: Sequence[Mapping[str, Any]],
) -> list[str]:
    hosts = list(
        dict.fromkeys(
            str(item.get("host") or "").strip().lower()
            for item in authority_bindings
            if str(item.get("host") or "").strip()
        )
    )
    if not hosts:
        return []
    identity = " ".join(
        [
            str(search.get("entity") or ""),
            *(str(item or "") for item in search.get("aliases") or []),
        ]
    )
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]{3,}", identity.lower())
        if token not in {"model", "company", "group", "official"}
    }
    prefers_china = "中国" in identity or " china" in identity.lower()
    ranked = sorted(
        hosts,
        key=lambda host: (
            -sum(token in host for token in tokens),
            -(prefers_china and host.endswith(".cn")),
            -len(host.split(".", 1)[0]),
            host,
        ),
    )
    matching = [host for host in ranked if any(token in host for token in tokens)]
    if matching:
        return matching
    return hosts if len(hosts) == 1 else []


def _search_provider(
    provider: str,
    query: str,
    *,
    doubao_config: DoubaoSearchConfig | None = None,
) -> dict[str, Any]:
    search_url = _search_url(provider, query)
    try:
        if provider == "doubao":
            if doubao_config is None or not doubao_config.enabled:
                return {
                    "provider": provider,
                    "query": query,
                    "search_url": search_url,
                    "status": "empty",
                    "results": [],
                    "error": None,
                }
            return search_doubao(query, doubao_config)
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
    if provider == "doubao":
        return "https://ark.cn-beijing.volces.com/api/v3/responses"
    raise ValueError(f"unsupported search provider {provider!r}")


def _active_providers(config: DoubaoSearchConfig | None) -> tuple[str, ...]:
    if config is not None and config.enabled:
        return (*_PROVIDERS, "doubao")
    return _PROVIDERS


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
    subjects: Sequence[str],
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
            score = max(_relevance_score(subject, title, snippet, url) for subject in subjects)
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
    *,
    search_index: int = 0,
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
            if (urllib.parse.urlsplit(url).hostname or "").lower() in _LOW_VALUE_DISCOVERY_HOSTS:
                continue
            haystack = f"{title} {snippet}".lower()
            overlap = sum(token in haystack for token in tokens)
            if tokens and overlap == 0:
                continue
            score = max(1, 8 - index * 2) + overlap * 3 + _source_quality_hint_score(url, title, ())
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
    authority_bindings: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    entity_key = _entity_key(str(search["entity"]))
    alias_keys = {
        key
        for item in search.get("aliases") or []
        for key in _expanded_identity_keys(str(item))
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
            snippet = _clean_text(str(result.get("snippet") or ""))[:1000]
            if not title or not _is_http_url(url):
                continue
            if (urllib.parse.urlsplit(url).hostname or "").lower() in _LOW_VALUE_DISCOVERY_HOSTS:
                continue
            identity_haystack = _entity_key(f"{title} {snippet} {urllib.parse.urlsplit(url).path}")
            text_haystack = f"{title} {snippet}".lower()
            exact_entity_match = bool(entity_key and entity_key in identity_haystack)
            alias_matches = sum(key in identity_haystack for key in alias_keys)
            entity_match = exact_entity_match or alias_matches > 0
            dimension_overlap = sum(token in text_haystack for token in dimension_tokens)
            score = max(1, 8 - result_index * 2)
            if exact_entity_match:
                score += 18
            if alias_matches:
                score += min(alias_matches, 2) * 10
            score += dimension_overlap * 3
            score += _source_quality_hint_score(url, title, authority_bindings)
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
    ranked = sorted(
        merged.values(),
        key=lambda item: (
            not bool(item["entity_match"]),
            -int(item["score"]),
            str(item["url"]),
        ),
    )
    matching = [item for item in ranked if bool(item["entity_match"])]
    return matching or ranked


def _expanded_identity_keys(value: str) -> set[str]:
    normalized = _clean_text(value)
    variants = {normalized}
    words = normalized.split()
    if len(words) > 1 and any(word.casefold() == "ai" for word in words):
        variants.add(
            " ".join(
                "Artificial Intelligence" if word.casefold() == "ai" else word for word in words
            )
        )
    if "artificial intelligence" in normalized.casefold():
        variants.add(re.sub("artificial intelligence", "AI", normalized, flags=re.I))
    return {key for item in variants if (key := _entity_key(item))}


def _round_robin_candidates(pools: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    pools = [_prioritize_distinct_domains(pool) for pool in pools]
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


def _prioritize_distinct_domains(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matching = [item for item in pool if bool(item.get("entity_match", True))]
    nonmatching = [item for item in pool if not bool(item.get("entity_match", True))]
    return [*_prioritize_domains(matching), *_prioritize_domains(nonmatching)]


def _prioritize_domains(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    distinct: list[dict[str, Any]] = []
    repeated: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    for candidate in pool:
        host = (urllib.parse.urlsplit(str(candidate.get("url") or "")).hostname or "").lower()
        if host and host not in seen_hosts:
            seen_hosts.add(host)
            distinct.append(candidate)
        else:
            repeated.append(candidate)
    return [*distinct, *repeated]


def _select_usable_sources(
    sources: Sequence[Mapping[str, Any]],
    *,
    search_count: int,
    authority_bindings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pools: list[list[Mapping[str, Any]]] = []
    for search_index in range(search_count):
        pool = [item for item in sources if item.get("search_index") == search_index]
        pool.sort(
            key=lambda item: (
                not _is_authoritative_source(str(item.get("url") or ""), authority_bindings),
                -int(item.get("score") or 0),
                str(item.get("url") or ""),
            )
        )
        pools.append(pool)

    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    cursors = [0] * len(pools)
    while len(selected) < _MAX_USABLE_SOURCES:
        added = False
        for pool_index, pool in enumerate(pools):
            while cursors[pool_index] < len(pool):
                source = pool[cursors[pool_index]]
                cursors[pool_index] += 1
                key = _canonical_url(str(source.get("url") or ""))
                if key in seen_urls:
                    continue
                selected.append(dict(source))
                seen_urls.add(key)
                added = True
                break
            if len(selected) >= _MAX_USABLE_SOURCES:
                break
        if not added:
            break
    return selected


def _search_quality_gaps(
    searches: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    authority_bindings: Sequence[Mapping[str, Any]],
    verification_method: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    gaps: list[str] = []
    follow_ups: list[dict[str, Any]] = []
    for search_index, search in enumerate(searches):
        retained = [item for item in sources if item.get("search_index") == search_index]
        entity = _clean_text(str(search.get("entity") or ""))
        hosts = _matching_authority_hosts(search, authority_bindings)
        if not retained:
            requires_authority = verification_method in {
                "official_primary_required",
                "contradiction_sensitive",
            }
            gaps.append(
                (
                    "no usable official or primary source was retained for "
                    if requires_authority
                    else "no usable public source was retained for "
                )
                + entity
            )
            follow_ups.append(
                {
                    "query": (
                        _clean_text(f"site:{hosts[0]} {entity} 官方 资料")[:240]
                        if hosts
                        else _fallback_source_query(search)
                    ),
                    "entity": entity,
                    "aliases": list(search.get("aliases") or []),
                    "dimension": _clean_text(str(search.get("dimension") or "")),
                }
            )
            continue
        if verification_method not in {
            "official_primary_required",
            "contradiction_sensitive",
        }:
            continue
        has_authority = any(
            _is_authoritative_source(str(item.get("url") or ""), authority_bindings)
            for item in retained
        )
        if has_authority:
            continue
        gaps.append(f"no usable official or primary source was retained for {entity}")
        if hosts:
            follow_ups.append(
                {
                    "query": _clean_text(f"site:{hosts[0]} {entity} 官方 规格 参数 配置")[:240],
                    "entity": entity,
                    "aliases": list(search.get("aliases") or []),
                    "dimension": _clean_text(str(search.get("dimension") or "")),
                }
            )
    return gaps, follow_ups[:2]


def _fallback_source_query(search: Mapping[str, Any]) -> str:
    """Switch source-finding strategy after a search yields no readable page."""

    entity = _clean_text(str(search.get("entity") or ""))
    dimension = _clean_text(str(search.get("dimension") or ""))
    aliases = [
        _clean_text(str(item or ""))
        for item in search.get("aliases") or []
        if _clean_text(str(item or ""))
    ]
    identity = " ".join(item for item in (entity, aliases[0] if aliases else "") if item)
    lowered = dimension.casefold()
    if any(
        token in lowered
        for token in ("定义", "概念", "哲学", "学术", "理论", "definition", "academic")
    ):
        source_terms = "综述 学术百科 论文 review definition"
    elif any(
        token in lowered for token in ("人物", "经历", "履历", "身份", "biography", "profile")
    ):
        source_terms = "机构 简介 采访 履历 profile biography"
    else:
        source_terms = "官方 机构 报告 说明 official report"
    return _clean_text(f'"{identity}" {source_terms} {dimension}')[:240]


def _compact_search_records(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "query_index": item.get("query_index"),
            **(
                {"query_variant_index": item.get("query_variant_index")}
                if item.get("query_variant_index") is not None
                else {}
            ),
            "provider": item.get("provider"),
            "query": item.get("query"),
            "status": item.get("status"),
            "result_count": len(item.get("results") or []),
            "error": item.get("error"),
        }
        for item in records
    ]


def _compact_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    fields = (
        "title",
        "url",
        "score",
        "providers",
        "search_index",
        "entity",
        "entity_match",
    )
    return [{key: item.get(key) for key in fields if key in item} for item in candidates]


def _compact_fetch_records(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    fields = (
        "requested_url",
        "url",
        "title",
        "content_type",
        "usable",
        "error",
        "entity",
        "search_index",
        "providers",
        "score",
    )
    return [{key: item.get(key) for key in fields if key in item} for item in records]


def _source_quality_hint_score(
    url: str,
    title: str,
    authority_bindings: Sequence[Mapping[str, Any]] = (),
) -> int:
    lowered_title = title.lower()
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.lower()
    score = 0
    if _is_bound_authority_source(url, authority_bindings):
        score += 40
    if any(
        host == suffix or host.endswith("." + suffix) for suffix in _LOW_QUALITY_SOURCE_SUFFIXES
    ):
        score -= 24
    if any(
        host == suffix or host.endswith("." + suffix) for suffix in _HIGH_QUALITY_SOURCE_SUFFIXES
    ):
        score += 28
    if host.endswith(".edu") or ".edu." in host or ".ac." in host:
        score += 18
    if host.endswith(".gov") or ".gov." in host:
        score += 18
    if any(
        marker in lowered_title
        for marker in (
            "公司简介",
            "关于我们",
            "官网",
            "官方网站",
            "年度报告",
            "annual report",
            "official",
        )
    ):
        score += 8
    if any(marker in path for marker in ("/about", "/introduction", "/company", "/report")):
        score += 4
    if host.endswith("wikipedia.org"):
        score -= 10
    return score


def _is_bound_authority_source(
    url: str,
    authority_bindings: Sequence[Mapping[str, Any]],
) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for binding in authority_bindings:
        expected = str(binding.get("host") or "").strip().lower()
        if not expected:
            continue
        if host == expected or (
            binding.get("include_subdomains") is True and host.endswith("." + expected)
        ):
            return True
    return False


def _is_authoritative_source(
    url: str,
    authority_bindings: Sequence[Mapping[str, Any]] = (),
) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return bool(
        _is_bound_authority_source(url, authority_bindings)
        or any(
            host == suffix or host.endswith("." + suffix)
            for suffix in _HIGH_QUALITY_SOURCE_SUFFIXES
        )
        or host.endswith(".edu")
        or ".edu." in host
        or ".ac." in host
        or host.endswith(".gov")
        or ".gov." in host
    )


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


def _fetch_candidates(
    candidates: list[dict[str, Any]],
    authority_bindings: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    selected = candidates[:_MAX_FETCH_ATTEMPTS]
    if not selected:
        return []
    records: list[dict[str, Any] | None] = [None] * len(selected)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(selected), _MAX_FETCH_WORKERS)
    ) as executor:
        futures = {
            executor.submit(_fetch_source, str(candidate["url"])): index
            for index, candidate in enumerate(selected)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                fetched = future.result()
                candidate = selected[index]
                if _can_use_authority_search_excerpt(
                    candidate,
                    fetched,
                    authority_bindings,
                ):
                    fetched = {
                        **fetched,
                        "title": candidate.get("title", ""),
                        "content_type": "text/search-snippet",
                        "content_excerpt": str(candidate.get("snippet") or "")[
                            :_SOURCE_EXCERPT_CHARS
                        ],
                        "usable": True,
                        "error": None,
                    }
                records[index] = {
                    **fetched,
                    "entity": candidate.get("entity"),
                    "search_index": candidate.get("search_index"),
                    "search_snippet": candidate.get("snippet", ""),
                    "providers": list(candidate.get("providers") or []),
                    "score": candidate.get("score", 0),
                }
            except Exception as exc:  # one page cannot fail the whole research Operation
                records[index] = {
                    "requested_url": selected[index]["url"],
                    "url": selected[index]["url"],
                    "title": selected[index]["title"],
                    "content_excerpt": "",
                    "usable": False,
                    "error": str(exc),
                    "entity": selected[index].get("entity"),
                    "search_index": selected[index].get("search_index"),
                    "search_snippet": selected[index].get("snippet", ""),
                    "providers": list(selected[index].get("providers") or []),
                    "score": selected[index].get("score", 0),
                }
    return [record for record in records if record is not None]


def _can_use_authority_search_excerpt(
    candidate: Mapping[str, Any],
    fetched: Mapping[str, Any],
    authority_bindings: Sequence[Mapping[str, Any]] = (),
) -> bool:
    snippet = _clean_text(str(candidate.get("snippet") or ""))
    return bool(
        fetched.get("usable") is not True
        and "doubao" in (candidate.get("providers") or [])
        and len(snippet) >= _AUTHORITY_SNIPPET_MIN_CHARS
        and _is_authoritative_source(str(candidate.get("url") or ""), authority_bindings)
    )


def _fetch_source(url: str) -> dict[str, Any]:
    try:
        normalized_url = _normalize_http_url(url)
        payload, final_url, content_type = _read_url(
            normalized_url,
            timeout=8,
            limit=_MAX_SOURCE_BYTES,
        )
        text, title = _extract_source_text(payload, content_type)
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


def _extract_source_text(payload: bytes, content_type: str) -> tuple[str, str]:
    lowered_type = content_type.lower()
    if "application/pdf" in lowered_type or payload.startswith(b"%PDF-"):
        raise ValueError("PDF content requires text extraction and is not readable as plain text")

    text = _decode(payload, content_type)
    title = ""
    if "html" in lowered_type or "<html" in text[:500].lower():
        parser = _TextExtractor()
        parser.feed(text)
        title = " ".join(parser.title_chunks)
        text = "\n".join(parser.chunks)
    elif "text/" not in lowered_type and b"\x00" in payload[:2_000]:
        raise ValueError("source returned unsupported binary content")
    return text, title


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
    tracking_keys = {
        "_rewritetime",
        "from",
        "platform-key",
        "spm",
        "spmref",
        "src",
        "source",
        "vt",
    }
    query = [
        (key, item)
        for key, item in query
        if not key.lower().startswith("utm_") and key.lower() not in tracking_keys
    ]
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
        "query_count": len(searches),
        "searches": [
            {"query": item.get("query"), "entity": item.get("entity")} for item in searches
        ],
        "healthy_provider_count": len(healthy),
        "failed_providers": failed,
        "provider_errors": {
            str(record.get("provider")): str(record.get("error"))[:240]
            for record in search_records
            if record.get("status") in {"blocked", "failed"} and record.get("error")
        },
        "candidate_count_by_search": candidate_counts,
        "usable_sources": [
            {"url": item.get("url"), "title": item.get("title")} for item in sources
        ],
    }


def _provider_failure_label(record: Mapping[str, Any]) -> str:
    provider = str(record.get("provider") or "unknown")
    error = " ".join(str(record.get("error") or "").split())[:240]
    return f"{provider} ({error})" if error else provider


def _unique_failure_labels(records: Sequence[Mapping[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for record in records:
        if record.get("status") not in {"blocked", "failed"}:
            continue
        label = _provider_failure_label(record)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


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

PUBLIC_WEB_EXPLORE_SPEC = {
    "name": "public_web_explore",
    "description": (
        "Run four to six complementary broad searches for a clear Deep Search request "
        "before selecting research questions. It returns a compact source map for "
        "coverage-driven planning. The queries input is optional only for compatibility "
        "with direct callers; the Deep Search workflow always supplies it."
    ),
    "input_schema": {
        "type": "object",
        "required": ["request", "time_token"],
        "additionalProperties": False,
        "properties": {
            "request": {"type": "string", "minLength": 1},
            "queries": {
                "type": "array",
                "minItems": 4,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "required": ["query", "purpose"],
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 240},
                        "purpose": {"type": "string", "minLength": 1, "maxLength": 160},
                    },
                },
            },
            "time_token": {"type": "string", "minLength": 1},
        },
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": False,
    "max_calls_per_node": 1,
    "fresh_output_prerequisite": _TIME_PREREQUISITE,
}

PUBLIC_WEB_SEARCH_SPEC = {
    "name": "public_web_search",
    "description": (
        "Search public Web sources for one research question. Runtime injects the "
        "reviewed authority policy, targets matching official hosts, and returns "
        "compact usable sources plus any explicit quality_gaps. Call once normally; "
        "when quality_gaps includes follow_up_searches, make at most one additional "
        "targeted call after obtaining a fresh time token."
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

INITIALIZE_DEEP_RESEARCH_SPEC = {
    "name": "initialize_deep_research",
    "description": (
        "Bind the Research Brief, Coverage Map, and coverage-derived Tasks to the user's "
        "exact request, then produce the trusted Intent used to seed the dynamic Task Graph."
    ),
    "input_schema": {
        "type": "object",
        "required": ["request", "research_brief", "exploration", "research_map"],
        "additionalProperties": False,
        "properties": {
            "request": {"type": "string", "minLength": 1},
            "research_brief": {
                "type": "object",
                "required": [
                    "original_request",
                    "objective",
                    "task_type",
                    "entities",
                    "freshness",
                    "constraints",
                    "material_ambiguities",
                ],
                "additionalProperties": False,
                "properties": {
                    "original_request": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "task_type": {
                        "enum": ["lookup", "explain", "compare", "landscape", "due_diligence"]
                    },
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "freshness": {"type": "string", "minLength": 1},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "material_ambiguities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "exploration": {"type": "object"},
            "current_time": {"type": "object"},
            "research_map": {"type": "object"},
        },
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
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
                        "excerpt": {"type": "string"},
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
        "Finish the active research question from selected search sources. Pass "
        "source_urls observed in this task; Runtime binds their excerpts and "
        "provenance automatically. The legacy verification_id path remains "
        "supported. Confidence, evidence, verified_claim, and authority metadata "
        "are runtime-owned and must not be authored."
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
            "source_urls": {
                "type": "array",
                "maxItems": 4,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^https?://"},
            },
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
                        "excerpt": {"type": "string"},
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
                                "independence": {"enum": ["independent", "same_origin"]},
                                "directness": {"enum": ["direct", "indirect"]},
                                "as_of": {"type": "string"},
                                "excerpt": {"type": "string"},
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
    "INITIALIZE_DEEP_RESEARCH_SPEC",
    "PUBLIC_WEB_EXPLORE_SPEC",
    "PUBLIC_WEB_RESEARCH_SPEC",
    "PUBLIC_WEB_SEARCH_SPEC",
    "RECORD_RESEARCH_FINDING_SPEC",
    "REJECT_RESEARCH_REQUEST_SPEC",
    "VERIFY_CLAIM_EVIDENCE_SPEC",
    "build_evidence_graph",
    "initialize_deep_research",
    "public_web_explore",
    "public_web_research",
    "public_web_search",
    "record_research_finding",
    "reject_research_request",
    "verify_claim_evidence",
]
