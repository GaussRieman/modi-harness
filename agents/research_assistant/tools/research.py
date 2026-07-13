"""Bounded public-Web research for the single-node Research Assistant."""

from __future__ import annotations

import concurrent.futures
import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, ClassVar

_PROVIDERS = ("bing_rss", "baidu", "duckduckgo")
_SEARCH_RESULTS_PER_PROVIDER = 4
_MAX_FETCH_ATTEMPTS = 5
_MAX_USABLE_SOURCES = 3
_SOURCE_EXCERPT_CHARS = 6_000
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


def public_web_research(subject: str, question: str = "") -> dict[str, Any]:
    """Search several public indexes and fetch a few strongly matching pages."""

    normalized_subject = " ".join(str(subject or "").split())
    normalized_question = " ".join(str(question or "").split())
    if not normalized_subject:
        return {
            "subject": "",
            "question": normalized_question,
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
        "queries": queries,
        "search_records": search_records,
        "candidates": candidates[:8],
        "sources": sources,
        "fetch_records": fetch_records,
        "limitations": limitations,
        "summary": {
            "provider_count": len(_PROVIDERS),
            "healthy_provider_count": len(healthy_providers),
            "query_count": len(queries),
            "relevant_candidate_count": len(candidates),
            "usable_source_count": len(sources),
        },
    }


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


def _run_searches(queries: list[str]) -> list[dict[str, Any]]:
    jobs = [(provider, query) for query in queries for provider in _PROVIDERS]
    records: list[dict[str, Any] | None] = [None] * len(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as executor:
        futures = {
            executor.submit(_search_provider, provider, query): index
            for index, (provider, query) in enumerate(jobs)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            provider, query = jobs[index]
            try:
                records[index] = future.result()
            except Exception as exc:  # provider isolation is part of the Operation contract
                records[index] = {
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


PUBLIC_WEB_RESEARCH_SPEC = {
    "name": "public_web_research",
    "description": (
        "Run one bounded multi-provider public-Web investigation for the active "
        "research subject. It expands queries, ranks subject-name matches, fetches "
        "a few strong candidates, and returns compact source and search records. "
        "Use it once, then answer from its result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "minLength": 1},
            "question": {"type": "string"},
        },
        "required": ["subject"],
        "additionalProperties": False,
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": True,
    "max_calls_per_node": 1,
}

__all__ = ["PUBLIC_WEB_RESEARCH_SPEC", "public_web_research"]
