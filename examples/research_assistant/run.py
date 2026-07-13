"""Modi Harness — Research Assistant with efficient tool / memory execution demo.

Minimal demo of the research assistant with auto-generated JSON schema.
No hand-written 40-line YAML schema — the loader generates it from
``output_contract.required_fields`` + ``field_constraints``.

This example uses the repo-local ``.modi/memory`` store by default so V0.6.b
memory behavior is visible in the same place as the rest of the project state:

- caller-managed user/workspace/thread/agent memory bootstrap
- expired/superseded records filtered out of selection
- runtime recall/admission/selection trace events
- model-initiated ``recall_memory`` and ``propose_memory`` calls
- drafts/artifacts kept as workspace outputs, not memory
- V0.6.e execution efficiency: multiple tool calls from one model turn are
  executed in one Harness node visit, and run-local memory recall is cached
  until a committed memory write invalidates it.

Run from the repo root:
    uv run python examples/research_assistant/run.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console

from modi_harness import ModiAgent, ModiHarness, ModiSession
from modi_harness._utils import new_ulid
from modi_harness.cli.renderer import TaskProgressRenderer
from modi_harness.cli.runner import run_streaming
from modi_harness.config import Settings
from modi_harness.models import create_chat_model

# ---------------------------------------------------------------------------
# Tool: fetch_url  (same as the full example)
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._title_depth = 0
        self._title_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._title_depth += 1
            return
        if tag in ("script", "style", "noscript", "nav", "header", "footer", "aside"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title_depth > 0:
            self._title_depth -= 1
            return
        if (
            tag in ("script", "style", "noscript", "nav", "header", "footer", "aside")
            and self._skip_depth > 0
        ):
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth > 0:
            text = data.strip()
            if text:
                self._title_chunks.append(text)
            return
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)

    def title(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._title_chunks)).strip()


_MAX_BYTES = 256 * 1024
_MAX_BODY_CHARS = 32000
_MAX_CARD_FACTS = 8


def fetch_url(url: str) -> dict:
    """Fetch a URL and return cleaned source text for model-led evidence selection."""
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": f"refusing non-http(s) URL: {url!r}"}
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "modi-harness-research-assistant/0.4d"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read(_MAX_BYTES + 1)
            content_type = resp.headers.get("Content-Type", "") or ""
            final_url = resp.geturl()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": f"fetch failed: {exc}"}
    truncated = len(data) > _MAX_BYTES
    if truncated:
        data = data[:_MAX_BYTES]
    try:
        body = data.decode("utf-8", errors="replace")
    except Exception:
        return {"error": "decode failed"}
    title = ""
    if "html" in content_type.lower():
        parser = _TextExtractor()
        try:
            parser.feed(body)
            body = parser.text()
            title = parser.title()
        except Exception:
            pass
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS]
        truncated = True
    return {
        "url": final_url,
        "content_type": content_type,
        "truncated": truncated,
        "size_bytes": len(data),
        "source_tokens_estimate": max(1, len(body.encode("utf-8")) // 4),
        "title": title or final_url,
        "content": body,
    }


FETCH_URL_SPEC = {
    "name": "fetch_url",
    "description": "Fetch a URL and return cleaned source text for model-led evidence selection.",
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string", "format": "uri"}},
        "required": ["url"],
        "additionalProperties": False,
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": True,
}


def source_extract(url: str, content: str, content_type: str = "") -> dict:
    """Compress source text into an evidence card for model context."""
    clean = _normalize_source_text(content)
    facts = _select_evidence_facts(clean)
    card = {
        "citation_key": _citation_key(url),
        "source_url": url,
        "content_type": content_type,
        "title_or_label": _source_title(clean, url),
        "facts": facts,
        "quality_notes": [],
        "open_questions": [],
        "source_tokens_estimate": max(1, len(clean.encode("utf-8")) // 4) if clean else 0,
        "card_tokens_estimate": max(1, len(str(facts).encode("utf-8")) // 4) if facts else 0,
    }
    if not facts:
        card["open_questions"].append("source text was empty or could not be extracted")
    return {"evidence_card": card}


def _normalize_source_text(content: str) -> str:
    return re.sub(r"\s+", " ", content or "").strip()


def _select_evidence_facts(content: str) -> list[str]:
    if not content:
        return []
    sentences = re.split(r"(?<=[.!?。!?])\s+", content)
    facts: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        text = sentence.strip()
        if len(text) < 40:
            continue
        if text in seen:
            continue
        seen.add(text)
        facts.append(text[:280])
        if len(facts) >= _MAX_CARD_FACTS:
            break
    if facts:
        return facts
    return [content[:280]]


def _source_title(content: str, url: str) -> str:
    if content:
        return content[:120]
    return url


def _citation_key(url: str) -> str:
    label = re.sub(r"^https?://", "", url).strip("/")
    label = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower()
    return (label or "source")[:48]


SOURCE_EXTRACT_SPEC = {
    "name": "source_extract",
    "description": "Compress fetched source text into a structured evidence card.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "content": {"type": "string"},
            "content_type": {"type": "string"},
        },
        "required": ["url", "content"],
        "additionalProperties": False,
    },
    "risk_level": "L0",
    "side_effect": False,
    "idempotent": True,
}


def generate_research_digest(
    research_question: str,
    source_records: list[dict],
    tasks: list[dict] | None = None,
) -> dict:
    """Generate source-bound content as an operation artifact."""
    sources = [_normalize_digest_source(item) for item in source_records if isinstance(item, dict)]
    evidence, noise_filtered_count = _digest_evidence(sources, research_question)
    claims = [{"claim": item["text"], "evidence": [item["source_url"]]} for item in evidence[:6]]
    limitations = _digest_limitations(evidence, sources)
    task_results = _digest_task_results(
        tasks or [], research_question, sources, evidence, limitations
    )
    quality_signals = _digest_quality_signals(sources, evidence, noise_filtered_count)
    final_output = {
        "research_question": research_question,
        "executive_summary": _digest_summary(research_question, sources, evidence, limitations),
        "task_results": [_final_digest_task_result(item) for item in task_results],
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


def judge_research_digest(digest: dict) -> dict:
    """Judge generated content as an operation artifact."""
    evidence = digest.get("evidence") if isinstance(digest, dict) else None
    task_results = digest.get("task_results") if isinstance(digest, dict) else None
    issues: list[str] = []
    if not isinstance(evidence, list) or not evidence:
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
            "reason": "digest has source-bound evidence and task results"
            if not issues
            else "; ".join(issues),
        }
    }


def _normalize_digest_source(record: dict) -> dict[str, str]:
    url = str(record.get("url") or record.get("requested_url") or "").strip()
    requested_url = str(record.get("requested_url") or url).strip()
    title = _clean_digest_title(str(record.get("title") or url or "source"), url)
    raw_content = str(record.get("content_excerpt") or record.get("content") or "")
    content = _clean_digest_source_text(raw_content, title)
    return {
        "url": url,
        "requested_url": requested_url,
        "title": title,
        "content": content,
        "summary": _digest_source_coverage_summary(title, content),
        "raw_text_chars": str(len(raw_content)),
    }


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
    question_terms = _digest_keyword_terms(research_question)
    candidates: list[tuple[int, int, str]] = []
    noise_filtered_count = 0
    fallback: list[tuple[int, str]] = []
    for index, part in enumerate(_split_digest_fact_candidates(text)):
        fact = _clean_digest_fact(part)
        if not fact:
            continue
        if _is_noisy_digest_fact(fact):
            noise_filtered_count += 1
            continue
        if len(fact) < 14:
            continue
        fact = _rewrite_digest_fact(fact, source_title)
        if not fact:
            continue
        fallback.append((index, fact))
        score = _digest_fact_score(fact, question_terms)
        if score <= 0:
            continue
        candidates.append((score, index, fact))
    ordered = sorted(candidates, key=lambda item: (-item[0], item[1]))[:8]
    facts = [
        _bounded_digest_text(fact, 150) for _, _, fact in sorted(ordered, key=lambda item: item[1])
    ]
    if facts:
        return _dedupe_digest_texts(facts, limit=8), noise_filtered_count
    return [_bounded_digest_text(fact, 150) for _, fact in fallback[:4]], noise_filtered_count


def _digest_limitations(evidence: list[dict[str, str]], sources: list[dict[str, str]]) -> list[str]:
    limitations: list[str] = ["仅使用用户给定并成功获取的来源"]
    joined = " ".join(item["text"] for item in evidence).lower()
    if len(sources) == 1:
        limitations.append("仅覆盖单一来源页面")
    if "pricing" not in joined and "价格" not in joined and "定价" not in joined:
        limitations.append("未覆盖定价证据")
    if "benchmark" not in joined and "性能" not in joined and "速度" not in joined:
        limitations.append("未覆盖性能或基准数据")
    if not evidence:
        limitations.append("没有足够可用正文证据")
    if not sources:
        limitations.append("没有可用来源")
    return _dedupe_digest_texts(limitations, limit=6)


def _digest_task_results(
    tasks: list[dict],
    research_question: str,
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
    limitations: list[str],
) -> list[dict]:
    if not tasks:
        tasks = [
            {"id": "scope", "title": "界定来源覆盖"},
            {"id": "evidence", "title": "提炼关键证据"},
            {"id": "judgment", "title": "形成取舍判断"},
        ]
    source_names = _digest_source_names(sources)
    evidence_texts = [item["text"] for item in evidence]
    fact_text = _join_short_digest(evidence_texts[:3], limit=260) or "来源没有足够证据"
    coverage = _infer_digest_scope_phrase(sources, evidence)
    judgment = _digest_judgment_sentence(sources, evidence, limitations)
    evidence_urls = _unique_digest_strings(item["source_url"] for item in evidence)[:3]
    out: list[dict] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        title = str(task.get("title") or task_id or "研究任务")
        if task_id == "scope":
            result = (
                f"{source_names} 主要覆盖: {coverage}; 可回答该来源的定位、能力范围和页面内限制。"
            )
        elif task_id == "evidence":
            result = fact_text
        elif task_id == "judgment":
            result = judgment
        else:
            result = fact_text
        out.append(
            {
                "task_id": task_id,
                "task": title,
                "result": _bounded_digest_text(result, 180),
                "evidence": evidence_urls,
                "limitations": limitations if task_id == "judgment" else [],
            }
        )
    return out


def _final_digest_task_result(item: dict) -> dict:
    return {
        "task": str(item.get("task") or ""),
        "result": str(item.get("result") or ""),
        "evidence": _digest_string_list(item.get("evidence")),
        "limitations": _digest_string_list(item.get("limitations")),
    }


def _digest_string_list(value) -> list[str]:
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
    source_names = _digest_source_names(sources)
    coverage = _infer_digest_scope_phrase(sources, evidence)
    facts = _join_short_digest([item["text"] for item in evidence[:4]], limit=320)
    if not facts:
        return f"{source_names} 暂无足够正文证据回答: {research_question}。"
    limit_text = _join_short_digest(limitations, limit=160)
    summary = (
        f"{source_names} 主要说明: {coverage}。关键证据: {_strip_digest_final_punctuation(facts)}。"
    )
    if limit_text:
        summary += f"限制: {_strip_digest_final_punctuation(limit_text)}。"
    return _bounded_digest_text(summary, 520)


def _clean_digest_title(title: str, url: str = "") -> str:
    value = " ".join(str(title or "").split())
    if not value:
        return _digest_label_from_url(url) or "该来源"
    brand = _digest_brand_from_text(value) or _digest_label_from_url(url)
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
    return _bounded_digest_text(" ".join(deduped) or value, 54)


def _clean_digest_source_text(content: str, title: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return ""
    if title and text.startswith(title):
        text = text[len(title) :].lstrip(" 。:;" + "\uff1a" + "-")
    return text


def _digest_source_coverage_summary(title: str, content: str) -> str:
    first_facts, _ = _select_digest_facts(content, title, title)
    if first_facts:
        return _bounded_digest_text(_join_short_digest(first_facts[:2], limit=180), 180)
    return _bounded_digest_text(title or content, 180)


def _split_digest_fact_candidates(text: str) -> list[str]:
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


def _clean_digest_fact(text: str) -> str:
    value = " ".join(str(text or "").split())
    value = re.sub(r"^(我们的故事|关于我们|首页|菜单)\s*", "", value)
    value = re.sub(r"\s*([、\uff0c。\uff1b\uff1a\uff01\uff1f])\s*", r"\1", value)
    value = re.sub(r"\s+([\uff0c。\uff1b\uff1a\uff01\uff1f])", r"\1", value)
    value = re.sub(r"([\uff08《])\s+", r"\1", value)
    return value.strip(" ;" + "\uff1b")


def _is_noisy_digest_fact(text: str) -> bool:
    lowered = text.lower()
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


def _rewrite_digest_fact(text: str, source_title: str) -> str:
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
    return _bounded_digest_text(value, 150)


def _digest_brand_from_text(text: str) -> str:
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
            return token[:40]
    return ""


def _digest_label_from_url(url: str) -> str:
    match = re.search(r"^https?://([^/]+)", str(url or ""), flags=re.I)
    if not match:
        return ""
    host = match.group(1).lower().removeprefix("www.")
    stem = host.split(".", 1)[0]
    known = {
        "ilovepdf": "iLovePDF",
        "smallpdf": "Smallpdf",
        "github": "GitHub",
        "huggingface": "Hugging Face",
    }
    return known.get(stem, stem or host)


def _digest_fact_score(text: str, question_terms: set[str]) -> int:
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


def _digest_keyword_terms(text: str) -> set[str]:
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
) -> dict:
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


def _digest_source_names(sources: list[dict[str, str]]) -> str:
    return "、".join(item["title"] for item in sources[:2] if item.get("title")) or "给定来源"


def _infer_digest_scope_phrase(
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
) -> str:
    joined = " ".join(
        [item.get("title", "") for item in sources] + [item.get("text", "") for item in evidence]
    ).lower()
    if "pdf" in joined:
        return "PDF 文件管理服务、产品定位和可用能力"
    if "transformer" in joined or "rnn" in joined or "attention" in joined:
        return "序列建模机制、并行性和适用限制"
    if "agent" in joined:
        return "项目定位、能力范围和使用限制"
    if "tool" in joined or "工具" in joined:
        return "工具定位、功能范围和使用限制"
    return "来源页面覆盖的主题、证据和限制"


def _digest_judgment_sentence(
    sources: list[dict[str, str]],
    evidence: list[dict[str, str]],
    limitations: list[str],
) -> str:
    if not evidence:
        return "当前来源正文证据不足, 不能形成可靠结论。"
    coverage = _infer_digest_scope_phrase(sources, evidence)
    missing = [item for item in limitations if item.startswith("未覆盖")]
    if missing:
        return f"可判断该来源覆盖: {coverage}; {'; '.join(missing[:2])}。"
    return f"可判断该来源覆盖: {coverage}; 结论应限制在已抓取页面范围内。"


def _join_short_digest(items: list[str], *, limit: int) -> str:
    out = ""
    for item in _dedupe_digest_texts(items, limit=8):
        if not item:
            continue
        candidate = item if not out else f"{out}; {item}"
        if len(candidate) > limit:
            break
        out = candidate
    return out


def _strip_digest_final_punctuation(text: str) -> str:
    return str(text or "").rstrip("。.!?" + "\uff01\uff1f")


def _dedupe_digest_texts(items: list[str], *, limit: int) -> list[str]:
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


def _unique_digest_strings(items) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _bounded_digest_text(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "..."


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
}


class ResearchPlanPrompt:
    """Collect confirmation or revision feedback for a proposed research plan."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def ask(self, interaction: dict, agent: dict | None = None) -> tuple[str, str | None]:
        del interaction, agent
        self.console.print()
        self.console.print(
            "[dim]直接回车确认并开始研究;输入修改意见重新规划;输入 /cancel 取消。[/dim]"
        )
        try:
            feedback = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            self.console.print()
            return ("cancelled", None)
        if not feedback:
            return ("approved", None)
        if feedback.lower() == "/cancel":
            return ("cancelled", None)
        return ("revise", feedback)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_URLS = [
    "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)",
    "https://en.wikipedia.org/wiki/Recurrent_neural_network",
    "https://en.wikipedia.org/wiki/Attention_(machine_learning)",
]

DEFAULT_QUESTION = "Transformer 和 RNN 在序列建模上有何区别?各自在哪些场景下表现更好?"


# ---------------------------------------------------------------------------
# Context / Workspace / Memory / Trace demo helpers
# ---------------------------------------------------------------------------


def build_research_agent(base_dir: Path | None = None) -> ModiAgent:
    del base_dir
    from modi_harness.discovery.factories import load_agent_package

    package = Path(__file__).resolve().parents[2] / "agents" / "research_assistant"
    return load_agent_package(package)


def build_session(
    *,
    chat_model,
    memory_root: Path | str | None = None,
    workspace_root: Path | str = ".modi/workspace/research_assistant",
) -> ModiSession:
    project_root = Path(__file__).resolve().parents[2]
    research = build_research_agent()
    harness = ModiHarness(chat_model=chat_model)
    return ModiSession(
        harness=harness,
        agents=[research],
        checkpointer=MemorySaver(),
        workspace_root=workspace_root,
        memory_root=memory_root or project_root / ".modi" / "memory",
        project_root=project_root,
        max_steps=30,
    )


def seed_example_memory(session: ModiSession) -> list[str]:
    """Seed compact caller-managed memory records via the direct API."""
    records = [
        {
            "id": "ra_feedback_citations",
            "scope": "agent",
            "type": "feedback",
            "name": "citation-style",
            "description": "Research briefings should cite sources with short labels.",
            "body": "研究简报必须把关键判断和证据来源绑定,用简短 citation labels 标明出处。",
            "tags": ["research", "citations"],
            "metadata": {"approved": True},
        },
        {
            "id": "ra_user_pref_concise_cn",
            "scope": "user",
            "type": "user",
            "name": "concise-chinese",
            "description": "User prefers concise Chinese research summaries.",
            "body": "用户偏好中文、结构化、少铺垫的研究摘要。",
            "tags": ["style", "research"],
        },
        {
            "id": "ra_project_compare_models",
            "scope": "workspace",
            "type": "project",
            "name": "model-comparison-frame",
            "description": "Workspace-local model comparison frame.",
            "body": "比较模型时优先覆盖:核心结构差异、训练/推理权衡、适用场景和局限。",
            "tags": ["research", "model-comparison"],
            "metadata": {"approved": True},
        },
        {
            "id": "ra_reference_locomotion",
            "scope": "agent",
            "type": "reference",
            "name": "memory-benchmark-note",
            "description": "Pointer: memory benchmarks and recall quality belong in references, not raw body.",
            "body": "如果任务涉及 Memory benchmark,只保存指针和摘要,不保存大段网页正文。",
            "tags": ["memory", "reference"],
        },
        {
            "id": "ra_expired_old_style",
            "scope": "agent",
            "type": "feedback",
            "name": "expired-style",
            "description": "Expired demo record; should not enter context.",
            "body": "过期示例:这条不应该被注入上下文。",
            "tags": ["research"],
            "expires_at": "2000-01-01T00:00:00.000Z",
        },
        {
            "id": "ra_superseded_old_frame",
            "scope": "agent",
            "type": "project",
            "name": "old-frame",
            "description": "Superseded demo record; should not enter context.",
            "body": "被替代示例:这条不应该被注入上下文。",
            "tags": ["research"],
            "metadata": {"superseded_by": "ra_project_compare_models"},
        },
    ]
    written: list[str] = []
    for record in records:
        session.add_memory(record)
        written.append(record["id"])
    return written


def memory_trace_summary(events: Iterable[dict]) -> dict[str, int]:
    interesting = {
        "memory_recall_candidates",
        "memory_admission",
        "memory_selection",
        "memory_write_proposed",
        "memory_write",
    }
    counts = {name: 0 for name in sorted(interesting)}
    for event in events:
        event_type = event.get("event_type")
        if event_type in counts:
            counts[event_type] += 1
    return counts


def print_memory_trace_summary(console: Console, session: ModiSession, thread_id: str) -> None:
    counts = memory_trace_summary(session.get_trace(thread_id))
    console.print()
    console.print("[bold cyan]Memory trace events[/bold cyan]")
    for name, count in counts.items():
        console.print(f"[dim]{name}[/dim]: {count}")


# ---------------------------------------------------------------------------
# Human-in-loop helpers
# ---------------------------------------------------------------------------


async def _get_research_urls(console: Console, argv: list[str]) -> list[str]:
    """交互式获取研究 URLs。如果命令行提供了 URLs,直接使用;否则提示用户输入。"""
    if argv:
        return argv

    console.print("[bold yellow]请输入研究 URLs(每行一个,输入空行结束):[/bold yellow]")
    urls = []
    while True:
        try:
            url = input("URL: ").strip()
            if not url:
                break
            if url.startswith("http://") or url.startswith("https://"):
                urls.append(url)
                console.print(f"  [dim]✓[/dim] {url}")
            else:
                console.print(f"  [red]✗[/red] 无效 URL(需要 http:// 或 https://): {url}")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

    return urls


async def _generate_and_confirm_question(
    console: Console,
    chat_model,
    urls: list[str],
) -> str | None:
    """基于 URLs 生成建议问题,并通过单提示循环确认或修改。"""
    console.print("[bold cyan]正在基于 URLs 生成研究问题...[/bold cyan]")

    urls_text = "\n".join(f"- {url}" for url in urls)
    generation_prompt = f"""请基于以下 URLs 生成一个自然、具体、可由这些 URL 回答的中文研究问题。

要求:
- 像用户会直接问的问题,不要像论文题目。
- 单个 URL 时,只问这个页面本身能支撑的问题。
- 优先生成“这是什么、怎么收费、差异在哪里、对使用有什么影响”这类可解释的问题,不要只问原始数字清单。
- 不要主动加入竞品对比、行业分析、市场份额、趋势预测等需要额外来源的范围。
- 只有多个 URL 明确来自不同对象或页面本身就是对比页时,才生成对比问题。
- 避免使用“策略分析”“深度调研”“及其与竞品的对比研究”这类生硬表述。
- 不超过 60 个中文字符。

URLs:
{urls_text}

只输出研究问题本身,不要额外解释。"""

    try:
        # 调用模型生成问题
        response = await chat_model.ainvoke([{"role": "user", "content": generation_prompt}])
        # response.content 可能是字符串或列表,需要处理
        content = response.content
        if isinstance(content, list):
            # 提取文本内容
            suggested_question = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            ).strip()
        else:
            suggested_question = content.strip()

        current_question = suggested_question
        while True:
            console.print()
            console.print("[bold green]建议研究问题[/bold green]")
            console.print(f"  {current_question}")
            console.print()
            console.print(
                "[dim]直接回车开始研究;输入修改意见或完整问题继续调整;输入 /cancel 退出。[/dim]"
            )

            try:
                feedback = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return None

            if not feedback:
                return current_question
            if feedback.lower() == "/cancel":
                return None

            refined_question = await _refine_question_with_feedback(
                console, chat_model, current_question, feedback
            )
            if not refined_question:
                console.print("[yellow]没有生成有效问题,请再试一次或输入 /cancel。[/yellow]")
                continue
            current_question = refined_question

    except Exception as e:
        console.print(f"[red]生成研究问题时出错:{e}[/red]")
        console.print("[bold yellow]请直接输入研究问题,或输入 /cancel 退出:[/bold yellow]")
        try:
            manual_question = input("> ").strip()
            if not manual_question or manual_question.lower() == "/cancel":
                return None
            return manual_question
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None


async def _refine_question_with_feedback(
    console: Console,
    chat_model,
    original_question: str,
    user_feedback: str,
) -> str | None:
    """基于用户反馈智能修正研究问题。

    用户反馈可能是:
    1. 完整的新研究问题
    2. 修正意见(例如:"不是2023-2024,而是最新")

    系统会智能判断并生成合适的完整研究问题。
    """
    console.print("[dim]正在理解您的修改意见并重新生成问题...[/dim]")

    refine_prompt = f"""你需要根据用户的反馈,修正研究问题。

原研究问题:
{original_question}

用户反馈:
{user_feedback}

请判断用户反馈的类型并做出相应处理:
1. 如果用户反馈是一个完整的研究问题(包含明确的主题、研究对象和研究角度),直接返回这个问题。
2. 如果用户反馈是修正意见(例如指出时间范围错误、强调某个方面、修改某个词语等),基于原问题和修正意见,生成一个修正后的完整研究问题。

要求:
- 输出一个自然、具体、可回答的中文研究问题(不超过 60 个中文字符)
- 像用户会直接问的问题,不要像论文题目
- 优先保留可解释空间,例如规则、差异、成本含义或适用场景,不要缩成原始数字清单
- 不要主动扩大到竞品对比、行业分析、市场份额或趋势预测,除非用户明确要求
- 避免使用“策略分析”“深度调研”“及其与竞品的对比研究”这类生硬表述
- 只输出最终的研究问题,不要额外解释
- 确保问题语句通顺、完整、有明确的研究目标"""

    try:
        response = await chat_model.ainvoke([{"role": "user", "content": refine_prompt}])
        content = response.content
        if isinstance(content, list):
            refined_question = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            ).strip()
        else:
            refined_question = content.strip()

        return refined_question

    except Exception as e:
        console.print(f"[red]修正问题时出错:{e}[/red]")
        console.print("[dim]将使用您输入的内容作为研究问题。[/dim]")
        return user_feedback


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(argv: list[str]) -> int:
    console = Console()
    console.print()
    console.print(
        "[bold cyan]Modi Harness — Research Assistant (Execution efficiency demo)[/bold cyan]"
    )
    console.print(
        "[dim]Context uses cached run-local memory recall; batched tools avoid extra model turns.[/dim]"
    )
    console.print()

    # Model config comes from MODI_MODEL_* keys in .env (see .env.example).
    settings = Settings()
    if not settings.model.api_key:
        console.print("[red]Error:[/red] MODI_MODEL_API_KEY not set in .env")
        console.print("[dim]Copy .env.example to .env and fill in your API key.[/dim]")
        return 1

    chat_model = create_chat_model(
        provider=settings.model.provider,
        name=settings.model.name,
        api_key=settings.model.api_key,
        base_url=settings.model.base_url,
    )

    # Human-in-loop: 获取研究 URLs
    urls = await _get_research_urls(console, argv)
    if not urls:
        console.print("[yellow]No URLs provided. Exiting.[/yellow]")
        return 0

    console.print(f"[dim]URLs:[/dim] {len(urls)} source(s)")
    for url in urls:
        console.print(f"  [dim]-[/dim] {url}")
    console.print()

    # Human-in-loop: 生成并确认研究问题
    question = await _generate_and_confirm_question(console, chat_model, urls)
    if not question:
        console.print("[yellow]No research question confirmed. Exiting.[/yellow]")
        return 0

    console.print(f"[bold green]Research question:[/bold green] {question}")
    console.print()

    here = Path(__file__).parent
    memory_root = here.parents[1] / ".modi" / "memory"
    thread_id = f"research_memory_demo_{new_ulid()}"
    session = build_session(
        chat_model=chat_model,
        memory_root=memory_root,
    )
    seeded = seed_example_memory(session)
    console.print(f"[dim]Memory store:[/dim] {memory_root}")
    console.print(f"[dim]Seeded caller-managed memory records:[/dim] {len(seeded)}")

    user_message = f"Research question: {question}\n\nSource URLs:\n" + "\n".join(
        f"- {u}" for u in urls
    )

    exit_code = await run_streaming(
        session,
        agent="research-assistant",
        input={
            "goal": "Produce a cited briefing on the research question.",
            "messages": [{"role": "user", "content": user_message}],
            "tags": ["research", "model-comparison"],
            "reference_keys": ["memory-benchmark-note"],
        },
        thread_id=thread_id,
        permission_mode="auto",
        console=console,
        renderer=TaskProgressRenderer(console, title="研究任务"),
        interaction_prompt=ResearchPlanPrompt(console),
    )
    print_memory_trace_summary(console, session, thread_id)
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
