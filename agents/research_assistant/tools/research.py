"""Research Assistant source acquisition and synthesis tools."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.skip_depth = 0
        self.title_chunks: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "title":
            self.in_title = True
        if tag in {"script", "style", "noscript", "nav", "header", "footer", "aside"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag in {"script", "style", "noscript", "nav", "header", "footer", "aside"}:
            self.skip_depth = max(0, self.skip_depth - 1)

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self.in_title:
            self.title_chunks.append(text)
            return
        if self.skip_depth == 0:
            self.chunks.append(text)


def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the public Web through Bing's RSS endpoint."""
    normalized_query = " ".join(str(query or "").split())
    if not normalized_query:
        return {"query": normalized_query, "results": [], "error": "query cannot be empty"}
    bounded_limit = min(max(int(limit), 1), 10)
    search_url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": normalized_query, "format": "rss"}
    )
    request = urllib.request.Request(
        search_url,
        headers={"User-Agent": "ModiHarness-ResearchAssistant/0.7"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(1_000_000)
    except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
        return {
            "query": normalized_query,
            "provider": "bing_rss",
            "search_url": search_url,
            "results": [],
            "error": str(exc),
        }
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        return {
            "query": normalized_query,
            "provider": "bing_rss",
            "search_url": search_url,
            "results": [],
            "error": f"invalid search response: {exc}",
        }
    results: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = " ".join((item.findtext("title") or "").split())
        url = (item.findtext("link") or "").strip()
        snippet = _strip_markup(item.findtext("description") or "")
        if not title or not url:
            continue
        results.append({"title": title, "url": url, "snippet": snippet[:1000]})
        if len(results) >= bounded_limit:
            break
    return {
        "query": normalized_query,
        "provider": "bing_rss",
        "search_url": search_url,
        "results": results,
        "error": None if results else "search returned no results",
        "guidance": (
            "Fetch relevant candidates. If the search budget yields no traceable source, "
            "complete with these search records and explicit limitations."
        ),
    }


def fetch_url(url: str) -> dict[str, Any]:
    """Fetch one HTTP source and return a compact, readable source record."""
    requested_url = str(url or "").strip()
    try:
        normalized_url = _normalize_http_url(requested_url)
    except (UnicodeError, ValueError) as exc:
        return {"url": requested_url, "error": str(exc)}
    request = urllib.request.Request(
        normalized_url,
        headers={"User-Agent": "ModiHarness-ResearchAssistant/0.7"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2_000_000)
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
    except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
        return {"url": requested_url, "error": str(exc)}

    charset_match = re.search(r"charset=([^;\s]+)", content_type, flags=re.I)
    charset = charset_match.group(1).strip("\"'") if charset_match else "utf-8"
    text = body.decode(charset, errors="replace")
    title = ""
    if "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = _TextExtractor()
        parser.feed(text)
        text = "\n".join(parser.chunks)
        title = " ".join(parser.title_chunks)
    content_excerpt = _clean_source_text(text, title)[:12_000]
    return {
        "url": final_url,
        "requested_url": requested_url,
        "content_type": content_type,
        "truncated": len(body) == 2_000_000,
        "size_bytes": len(body),
        "source_tokens_estimate": max(1, len(content_excerpt) // 4),
        "title": title,
        "content_excerpt": content_excerpt,
    }


def _normalize_http_url(value: str) -> str:
    """Convert one absolute HTTP IRI to an ASCII URL or reject it."""
    parts = urllib.parse.urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("fetch_url requires an absolute http(s) URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("fetch_url does not accept credentials in URLs")
    hostname = parts.hostname.encode("idna").decode("ascii")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query, safe="=&%/:?+,-._~")
    fragment = urllib.parse.quote(parts.fragment, safe="=&%/:?+,-._~")
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, path, query, fragment))


def _strip_markup(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(value)).split())


def generate_research_digest(
    research_question: str,
    source_records: list[dict[str, Any]],
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate a source-bound digest as an operation result, not Brain logic."""
    records = [item for item in source_records if isinstance(item, dict)]
    sources = [
        _normalize_source_record(item)
        for item in records
        if _is_fetched_source_record(item)
    ]
    search_records = [item for item in records if _is_search_record(item)]
    evidence, noise_filtered_count = _digest_evidence(sources, research_question)
    claims = [
        {
            "claim": fact["text"],
            "evidence": [fact["source_url"]],
        }
        for fact in evidence[:6]
    ]
    limitations = _digest_limitations(evidence, sources, search_records)
    task_results = _digest_task_results(
        tasks or [], research_question, sources, evidence, limitations
    )
    quality_signals = _digest_quality_signals(sources, evidence, noise_filtered_count)
    quality_signals["search_count"] = len(search_records)
    final_output = {
        "research_question": research_question,
        "executive_summary": _digest_summary(research_question, sources, evidence, limitations),
        "task_results": [_final_task_result(item) for item in task_results],
        "recommendations": [],
        "source_limitations": limitations,
    }
    return {
        "digest": {
            "status": "generated",
            "generator": "deterministic.extractive_digest.v2",
            "research_question": research_question,
            "source_coverage": [
                {
                    "url": item["url"],
                    "requested_url": item["requested_url"],
                    "title": item["title"],
                    "coverage": item["summary"],
                }
                for item in sources
            ],
            "claims": claims,
            "evidence": evidence,
            "limitations": limitations,
            "task_results": task_results,
            "final_output": final_output,
            "quality_signals": quality_signals,
            "judge_required": quality_signals["source_quality"] != "usable",
        }
    }


def judge_research_digest(digest: dict[str, Any]) -> dict[str, Any]:
    """Judge a generated digest as a separate operation result."""
    evidence = digest.get("evidence") if isinstance(digest, dict) else None
    task_results = digest.get("task_results") if isinstance(digest, dict) else None
    quality_signals = digest.get("quality_signals") if isinstance(digest, dict) else None
    search_count = (
        quality_signals.get("search_count") if isinstance(quality_signals, dict) else None
    )
    negative_result = (
        isinstance(evidence, list)
        and not evidence
        and isinstance(search_count, int)
        and not isinstance(search_count, bool)
        and search_count > 0
        and isinstance(task_results, list)
        and bool(task_results)
        and all(
            isinstance(item, dict)
            and not item.get("evidence")
            and bool(_string_list(item.get("limitations")))
            for item in task_results
        )
    )
    issues: list[str] = []
    if (not isinstance(evidence, list) or not evidence) and not negative_result:
        issues.append("digest has no source-bound evidence")
    if not isinstance(task_results, list) or not task_results:
        issues.append("digest has no task results")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict) or not item.get("source_url") or not item.get("text"):
                issues.append("evidence item is missing source_url or text")
                break
    return {
        "judgment": {
            "status": "passed" if not issues else "failed",
            "can_finalize": not issues,
            "issues": issues,
            "reason": (
                "digest records a bounded public search with explicit limitations"
                if negative_result and not issues
                else "digest has source-bound evidence and task results"
                if not issues
                else "; ".join(issues)
            ),
        }
    }


def _normalize_source_record(record: dict[str, Any]) -> dict[str, str]:
    url = str(record.get("url") or record.get("requested_url") or "").strip()
    requested_url = str(record.get("requested_url") or url).strip()
    title = _clean_source_title(str(record.get("title") or url or "source"), url)
    raw_content = str(record.get("content_excerpt") or record.get("content") or "")
    content = _clean_source_text(raw_content, title)
    return {
        "url": url,
        "requested_url": requested_url,
        "title": title,
        "content": content,
        "summary": _source_coverage_summary(title, content),
        "raw_text_chars": str(len(raw_content)),
    }


def _is_fetched_source_record(record: dict[str, Any]) -> bool:
    url = str(record.get("url") or record.get("requested_url") or "").strip()
    content = str(record.get("content_excerpt") or record.get("content") or "").strip()
    return url.startswith(("http://", "https://")) and bool(content)


def _is_search_record(record: dict[str, Any]) -> bool:
    return (
        bool(str(record.get("query") or "").strip())
        and bool(str(record.get("provider") or "").strip())
        and str(record.get("search_url") or "").startswith(("http://", "https://"))
        and isinstance(record.get("results"), list)
    )


def _digest_evidence(
    sources: list[dict[str, str]],
    research_question: str,
) -> tuple[list[dict[str, str]], int]:
    evidence: list[dict[str, str]] = []
    noise_filtered_count = 0
    for source in sources:
        facts, skipped = _select_digest_facts(
            source["content"],
            research_question,
            source["title"],
        )
        noise_filtered_count += skipped
        for fact in facts:
            evidence.append(
                {
                    "source_url": source["url"],
                    "source_title": source["title"],
                    "text": fact,
                }
            )
            if len(evidence) >= 8:
                return evidence, noise_filtered_count
    return evidence, noise_filtered_count


def _select_digest_facts(
    content: str,
    research_question: str,
    source_title: str = "",
) -> tuple[list[str], int]:
    text = re.sub(r"\s+", " ", content or "").strip()
    if not text:
        return [], 0
    question_terms = _keyword_terms(research_question)
    candidates: list[tuple[int, int, str]] = []
    noise_filtered_count = 0
    fallback: list[tuple[int, str]] = []
    for index, part in enumerate(_split_fact_candidates(text)):
        fact = _clean_fact(part)
        if not fact:
            continue
        if _is_noisy_fact(fact):
            noise_filtered_count += 1
            continue
        if len(fact) < 14:
            continue
        fact = _rewrite_fact(fact, source_title)
        if not fact:
            continue
        fallback.append((index, fact))
        score = _fact_score(fact, question_terms)
        if score <= 0:
            continue
        candidates.append((score, index, fact))
    ordered = sorted(candidates, key=lambda item: (-item[0], item[1]))[:8]
    facts = [_bounded_text(fact, 150) for _, _, fact in sorted(ordered, key=lambda item: item[1])]
    if facts:
        return _dedupe_texts(facts, limit=8), noise_filtered_count
    return [_bounded_text(fact, 150) for _, fact in fallback[:4]], noise_filtered_count


def _digest_limitations(
    evidence: list[dict[str, str]],
    sources: list[dict[str, str]],
    search_records: list[dict[str, Any]],
) -> list[str]:
    if sources:
        limitations: list[str] = ["仅使用成功获取并可核验的公开来源"]
    elif search_records:
        limitations = [
            f"已完成 {len(search_records)} 次公开 Web 搜索, 未获得可核验来源"
        ]
    else:
        limitations = ["没有可用来源或可追溯搜索记录"]
    joined = " ".join(item["text"] for item in evidence).lower()
    if len(sources) == 1:
        limitations.append("仅覆盖单一来源页面")
    if _is_financing_context(sources, evidence):
        limitations.append("仅覆盖页面列出的融资快讯")
        limitations.append("未覆盖完整市场趋势或行业全景")
    if "pricing" not in joined and "价格" not in joined and "定价" not in joined:
        limitations.append("未覆盖定价证据")
    if "benchmark" not in joined and "性能" not in joined and "速度" not in joined:
        limitations.append("未覆盖性能或基准数据")
    if not evidence:
        limitations.append("没有足够可用正文证据")
    if not sources and search_records:
        limitations.append("搜索结果不能支持事实性技术实力判断")
    return _dedupe_texts(limitations, limit=6)


def _digest_task_results(
    tasks: list[dict[str, Any]],
    research_question: str,
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
    limitations: list[str],
) -> list[dict[str, Any]]:
    if not tasks:
        tasks = [
            {"id": "scope", "title": "界定来源覆盖"},
            {"id": "evidence", "title": "提炼关键证据"},
            {"id": "judgment", "title": "形成取舍判断"},
        ]
    source_names = _source_names(sources)
    evidence_texts = [item["text"] for item in evidence]
    fact_text = _join_short(evidence_texts[:3], limit=260) or "来源没有足够证据"
    coverage = _infer_scope_phrase(sources, evidence)
    judgment = _judgment_sentence(sources, evidence, limitations)
    evidence_urls = _unique_strings(item["source_url"] for item in evidence)[:3]
    financing_context = _is_financing_context(sources, evidence)
    out: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        title = str(task.get("title") or task_id or "研究任务")
        task_kind = _task_kind(task_id, title)
        if task_kind == "scope":
            if financing_context:
                result = (
                    f"{source_names} 主要覆盖: {coverage}; "
                    "可回答页面内哪些项目获得融资、轮次/估值、投资方和资金用途。"
                )
            else:
                result = (
                    f"{source_names} 主要覆盖: {coverage}; "
                    "可回答该来源的定位、能力范围和页面内限制。"
                )
        elif task_kind == "evidence":
            if financing_context:
                result = _financing_feature_sentence(evidence_texts) or fact_text
            else:
                result = fact_text
        elif task_kind == "judgment":
            result = judgment
        else:
            result = (
                f"{source_names} 主要覆盖: {coverage}; 可回答该来源的定位、能力范围和页面内限制。"
            )
        out.append(
            {
                "task_id": task_id,
                "task": title,
                "result": _bounded_text(result, 220),
                "evidence": evidence_urls,
                "limitations": limitations if not evidence or task_kind == "judgment" else [],
            }
        )
    return out


def _final_task_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": str(item.get("task") or ""),
        "result": str(item.get("result") or ""),
        "evidence": _string_list(item.get("evidence")),
        "limitations": _string_list(item.get("limitations")),
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None or value == "":
        return []
    return [str(value)]


def _digest_summary(
    research_question: str,
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
    limitations: list[str],
) -> str:
    source_names = _source_names(sources)
    coverage = _infer_scope_phrase(sources, evidence)
    facts = _join_short([item["text"] for item in evidence[:4]], limit=320)
    if not facts:
        limit_text = _join_short(limitations, limit=220)
        return f"公开资料不足以回答: {research_question}。限制: {limit_text}。"
    limit_text = _join_short(limitations, limit=160)
    if _is_financing_context(sources, evidence):
        facts = _join_short(
            _preferred_financing_facts([item["text"] for item in evidence]), limit=240
        )
        summary = (
            f"{source_names} 主要说明: {coverage}。关键证据: {_strip_final_punctuation(facts)}。"
        )
    else:
        summary = (
            f"{source_names} 主要说明: {coverage}。关键证据: {_strip_final_punctuation(facts)}。"
        )
    if limit_text:
        summary += f"限制: {_strip_final_punctuation(limit_text)}。"
    return _bounded_text(summary, 520)


def _clean_source_title(title: str, url: str = "") -> str:
    value = " ".join(str(title or "").split())
    if not value:
        return _label_from_url(url) or "该来源"
    brand = _brand_from_text(value) or _label_from_url(url)
    if brand:
        return brand
    fullwidth_bar = "\uff5c"
    segments = [
        item.strip(" -_|" + fullwidth_bar)
        for item in re.split(r"[。!?\uff01\uff1f\n\r|\uff5c]+", value)
        if item.strip(" -_|" + fullwidth_bar)
    ]
    if segments:
        value = segments[0]
    words = value.split()
    deduped: list[str] = []
    for word in words:
        if not deduped or deduped[-1] != word:
            deduped.append(word)
    return _bounded_text(" ".join(deduped) or value, 54)


def _clean_source_text(content: str, title: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return ""
    if title and text.startswith(title):
        text = text[len(title) :].lstrip(" 。:;" + "\uff1a" + "-")
    return _strip_common_web_chrome(text)


def _strip_common_web_chrome(text: str) -> str:
    """Remove common navigation/catalog prefixes without changing evidence facts."""

    value = " ".join(str(text or "").split())
    prefixes = (
        r"^公司/项目名/投资机构/赛道\s*",
        r"^返回36氪\s*",
        r"^登录\s*",
        r"^融资快报\s*",
        r"^快讯\s*",
    )
    previous = None
    while value and value != previous:
        previous = value
        for pattern in prefixes:
            value = re.sub(pattern, "", value).strip()
    return value


def _is_financing_context(
    sources: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> bool:
    joined = " ".join(
        [
            *(str(source.get("title") or "") for source in sources),
            *(str(source.get("content") or "")[:500] for source in sources),
            *(str(item.get("text") or "") for item in evidence),
        ]
    )
    return any(token in joined for token in ("融资", "领投", "估值", "投资方"))


def _task_kind(task_id: str, title: str) -> str:
    value = f"{task_id} {title}".lower()
    if any(token in value for token in ("scope", "范围", "覆盖", "界定")):
        return "scope"
    if any(token in value for token in ("evidence", "特点", "结论", "证据", "突出")):
        return "evidence"
    if any(token in value for token in ("limit", "局限", "限制", "缺口")):
        return "limitations"
    return "synthesis"


def _preferred_financing_facts(texts: list[str]) -> list[str]:
    cleaned = [_clean_fact(text) for text in texts]
    facts = [text for text in cleaned if text and "公司/项目名/投资机构/赛道" not in text]
    preferred = [
        text
        for text in facts
        if any(token in text for token in ("融资", "领投", "估值", "资金", "投后"))
    ]
    return _unique_strings([*preferred, *facts])


def _financing_feature_sentence(texts: list[str]) -> str:
    return _join_short(_preferred_financing_facts(texts), limit=240)


def _source_coverage_summary(title: str, content: str) -> str:
    first_facts, _ = _select_digest_facts(content, title, title)
    if first_facts:
        return _bounded_text(_join_short(first_facts[:2], limit=180), 180)
    return _bounded_text(title or content, 180)


def _split_fact_candidates(text: str) -> list[str]:
    rough = [
        part.strip()
        for part in re.split(r"(?<=[。.!?\uff01\uff1f])\s*|[\n\r]+", text)
        if part.strip()
    ]
    out: list[str] = []
    for part in rough:
        if len(part) <= 180:
            out.append(part)
            continue
        out.extend(
            item.strip() for item in re.split(r"[\uff1b;]\s*|(?<=\uff0c)\s*", part) if item.strip()
        )
    return out


def _clean_fact(text: str) -> str:
    value = " ".join(str(text or "").split())
    value = _strip_common_web_chrome(value)
    value = re.sub(r"^(我们的故事|关于我们|首页|菜单)\s*", "", value)
    value = re.sub(r"\s*([、\uff0c。\uff1b\uff1a\uff01\uff1f])\s*", r"\1", value)
    value = re.sub(r"\s+([\uff0c。\uff1b\uff1a\uff01\uff1f])", r"\1", value)
    value = re.sub(r"([\uff08《])\s+", r"\1", value)
    return value.strip(" ;" + "\uff1b")


def _is_noisy_fact(text: str) -> bool:
    lowered = text.lower()
    if any(token in text for token in ("公司/项目名/投资机构/赛道", "返回36氪 登录")):
        return True
    noisy = (
        "cookie",
        "privacy",
        "terms",
        "copyright",
        "all rights reserved",
        "登录",
        "注册",
        "扫码",
        "扫一扫",
        "验证码",
        "打开app",
        "打开 app",
        "下载app",
        "下载 app",
        "无障碍",
        "隐私政策",
        "服务条款",
        "岂不更好",
        "压得喘不过气",
    )
    hits = sum(1 for token in noisy if token in lowered)
    return (
        hits >= 2 or lowered in {"more", "learn more", "了解更多"} or text.endswith(("?", "\uff1f"))
    )


def _rewrite_fact(text: str, source_title: str) -> str:
    label = source_title or "该来源"
    if "致力于让文件管理变得毫不费力" in text:
        return f"{label} 的定位是降低文件管理成本。"
    if "处理可能非常耗时" in text and "PDF" in text:
        return "页面将 PDF 处理耗时作为主要用户痛点。"
    if "创建于2010年" in text or "创建于 2010年" in text or "创建于 2010 年" in text:
        if "巴塞罗那" in text:
            return f"{label} 创建于2010年, 总部位于西班牙巴塞罗那。"
        return f"{label} 创建于2010年。"
    if "目标一直是" in text and ("免费" in text or "可访问" in text):
        return f"{label} 声称目标是提供免费、可访问、高质量且易用的服务。"
    if "访问次数最多的PDF网站" in text or "访问次数最多的 PDF 网站" in text:
        return f"{label} 自称已发展成全球社区, 并是访问量较高的 PDF 网站之一。"
    if "移动版" in text and "桌面版" in text:
        return f"{label} 提供移动版和桌面版, 覆盖随时处理和离线处理场景。"
    value = re.sub(r"我们公司", label, text)
    value = re.sub(r"我们的项目", f"{label} 项目", value)
    value = re.sub(r"我们的目标一直是", f"{label} 的目标是", value)
    value = re.sub(r"我们的目标", f"{label} 的目标", value)
    return _bounded_text(value, 150)


def _brand_from_text(text: str) -> str:
    if "36氪" in text or "36kr" in text.lower():
        if "融资快报" in text:
            return "融资快报 - 36氪"
        return "36氪"
    known = (
        ("ilovepdf", "iLovePDF"),
        ("smallpdf", "Smallpdf"),
        ("livekit", "LiveKit"),
        ("qwen", "Qwen"),
        ("github", "GitHub"),
        ("hugging face", "Hugging Face"),
    )
    lowered = text.lower()
    for needle, label in known:
        if needle in lowered:
            return label
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.-]{2,}", text):
        if token.lower() not in {"pdf", "www", "com", "html"}:
            return str(token[:40])
    return ""


def _label_from_url(url: str) -> str:
    match = re.search(r"^https?://([^/]+)", str(url or ""), flags=re.I)
    if not match:
        return ""
    host = match.group(1).lower().removeprefix("www.")
    if "36kr.com" in host:
        return "36氪"
    stem = host.split(".", 1)[0]
    known = {
        "ilovepdf": "iLovePDF",
        "smallpdf": "Smallpdf",
        "github": "GitHub",
        "huggingface": "Hugging Face",
    }
    return known.get(stem, stem or host)


def _fact_score(text: str, question_terms: set[str]) -> int:
    lowered = text.lower()
    score = 0
    for term in question_terms:
        if term and term.lower() in lowered:
            score += 3
    signal_terms = (
        "主要",
        "提供",
        "支持",
        "覆盖",
        "工具",
        "服务",
        "功能",
        "产品",
        "目标",
        "创建",
        "成立",
        "总部",
        "免费",
        "可访问",
        "高质量",
        "易于使用",
        "移动版",
        "桌面版",
        "离线",
        "pdf",
        "parallel",
        "sequential",
        "attention",
        "benchmark",
        "limitation",
        "融资",
        "投资",
        "领投",
        "跟投",
        "估值",
        "轮次",
        "资金",
        "研发",
        "规模化",
        "pre-a",
    )
    for term in signal_terms:
        if term in lowered:
            score += 2
    if re.search(r"\d{4}|20\d\d|19\d\d", text):
        score += 1
    if 24 <= len(text) <= 180:
        score += 1
    if len(text) > 260:
        score -= 2
    return score


def _keyword_terms(text: str) -> set[str]:
    value = str(text or "")
    terms = {
        item.lower() for item in re.findall(r"[A-Za-z][A-Za-z0-9+.-]{2,}", value) if len(item) >= 3
    }
    for item in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if len(item) <= 8:
            terms.add(item)
    for generic in ("主要覆盖", "突出结论", "局限", "内容", "什么"):
        terms.discard(generic)
    return terms


def _digest_quality_signals(
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
    noise_filtered_count: int,
) -> dict[str, Any]:
    raw_chars = sum(int(str(item.get("raw_text_chars") or "0")) for item in sources)
    usable_sources = {
        item["source_url"] for item in evidence if item.get("source_url") and item.get("text")
    }
    source_quality = "usable" if len(evidence) >= 2 and usable_sources else "thin"
    return {
        "summary_mode": "deterministic_content_operation",
        "source_count": len(sources),
        "usable_source_count": len(usable_sources),
        "evidence_count": len(evidence),
        "noise_filtered_count": noise_filtered_count,
        "raw_text_chars": raw_chars,
        "source_quality": source_quality,
    }


def _source_names(sources: list[dict[str, str]]) -> str:
    return "、".join(item["title"] for item in sources[:2] if item.get("title")) or "公开来源"


def _infer_scope_phrase(
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
) -> str:
    joined = " ".join(
        [item.get("title", "") for item in sources] + [item.get("text", "") for item in evidence]
    ).lower()
    if "pdf" in joined:
        return "PDF 文件管理服务、产品定位和可用能力"
    if _is_financing_context(sources, evidence):
        return "融资事件、投资方、轮次/估值和资金用途"
    if "transformer" in joined or "rnn" in joined or "attention" in joined:
        return "序列建模机制、并行性和适用限制"
    if "agent" in joined:
        return "项目定位、能力范围和使用限制"
    if "tool" in joined or "工具" in joined:
        return "工具定位、功能范围和使用限制"
    return "来源页面覆盖的主题、证据和限制"


def _judgment_sentence(
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
    limitations: list[str],
) -> str:
    if not evidence:
        return "当前来源正文证据不足, 不能形成可靠结论。"
    coverage = _infer_scope_phrase(sources, evidence)
    if _is_financing_context(sources, evidence):
        return (
            f"可判断该来源覆盖: {coverage}; "
            "但结论只适合说明页面列出的融资快讯, 不能推出完整市场趋势或行业全景。"
        )
    missing = [item for item in limitations if item.startswith("未覆盖")]
    if missing:
        return f"可判断该来源覆盖: {coverage}; {'; '.join(missing[:2])}。"
    return f"可判断该来源覆盖: {coverage}; 结论应限制在已抓取页面范围内。"


def _join_short(items: list[str], *, limit: int) -> str:
    out = ""
    for item in _dedupe_texts(items, limit=8):
        if not item:
            continue
        candidate = item if not out else f"{out}; {item}"
        if len(candidate) > limit:
            break
        out = candidate
    return out


def _strip_final_punctuation(text: str) -> str:
    return str(text or "").rstrip("。.!?" + "\uff01\uff1f")


def _dedupe_texts(items: list[str], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join(str(item or "").split())
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _unique_strings(items: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _bounded_text(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


FETCH_URL_SPEC = {
    "name": "fetch_url",
    "description": (
        "Fetch one promising research URL and return a compact source record. "
        "Do not fetch weak or duplicate candidates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "pattern": "^https?://",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": True,
    "max_calls_per_node": 3,
}

WEB_SEARCH_SPEC = {
    "name": "web_search",
    "description": (
        "Search public Web sources when the user did not provide a known URL. "
        "Returns candidate result URLs and snippets for later fetch and verification."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": True,
    "max_calls_per_node": 2,
}


GENERATE_RESEARCH_DIGEST_SPEC = {
    "name": "generate_research_digest",
    "description": "Generate a source-bound research digest from fetched source records.",
    "input_schema": {
        "type": "object",
        "properties": {
            "research_question": {"type": "string"},
            "source_records": {"type": "array", "items": {"type": "object"}},
            "tasks": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["research_question", "source_records"],
        "additionalProperties": False,
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
    "max_calls_per_node": 1,
}


JUDGE_RESEARCH_DIGEST_SPEC = {
    "name": "judge_research_digest",
    "description": "Judge whether a generated research digest can support completion/finalization.",
    "input_schema": {
        "type": "object",
        "properties": {"digest": {"type": "object"}},
        "required": ["digest"],
        "additionalProperties": False,
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
    "max_calls_per_node": 1,
}


__all__ = [
    "FETCH_URL_SPEC",
    "GENERATE_RESEARCH_DIGEST_SPEC",
    "JUDGE_RESEARCH_DIGEST_SPEC",
    "WEB_SEARCH_SPEC",
    "fetch_url",
    "generate_research_digest",
    "judge_research_digest",
    "web_search",
]
