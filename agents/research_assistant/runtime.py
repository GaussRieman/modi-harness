"""Project-local Research Assistant factory and source tools."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from modi_harness import ModiAgent
from modi_harness.skills import SkillLoader
from modi_harness.types import Skill


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
        if self.skip_depth == 0:
            self.chunks.append(text)


def fetch_url(url: str) -> dict[str, Any]:
    """Fetch one HTTP source and return bounded, readable page content."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ModiHarness-ResearchAssistant/0.7"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2_000_000)
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
    except (OSError, urllib.error.URLError) as exc:
        return {"url": url, "error": str(exc)}

    charset_match = re.search(r"charset=([^;\s]+)", content_type, flags=re.I)
    charset = charset_match.group(1).strip('"\'') if charset_match else "utf-8"
    text = body.decode(charset, errors="replace")
    title = ""
    if "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = _TextExtractor()
        parser.feed(text)
        text = "\n".join(parser.chunks)
        title = " ".join(parser.title_chunks)
    return {
        "url": final_url,
        "content_type": content_type,
        "truncated": len(body) == 2_000_000,
        "size_bytes": len(body),
        "source_tokens_estimate": max(1, len(text) // 4),
        "title": title,
        "content": text[:120_000],
    }


def source_extract(url: str, content: str, content_type: str = "") -> dict[str, Any]:
    """Compress source text into a compact evidence card."""
    sentences = [part.strip() for part in re.split(r"(?<=[。.?!])\s+", content) if part.strip()]
    facts = [sentence[:500] for sentence in sentences[:8]] or [content[:500]]
    citation = re.sub(r"^https?://", "", url).strip("/")
    citation = re.sub(r"[^A-Za-z0-9]+", "-", citation).strip("-").lower()[:48]
    return {
        "evidence_card": {
            "citation_key": citation or "source",
            "url": url,
            "content_type": content_type,
            "facts": facts,
        }
    }


FETCH_URL_SPEC = {
    "name": "fetch_url",
    "description": "Fetch and extract readable content from one research URL.",
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    },
    "risk_level": "L1",
    "side_effect": False,
    "idempotent": True,
}

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


def build_agent() -> ModiAgent:
    package = Path(__file__).parent
    loader = SkillLoader(project_dir=package / "skills")
    skills = tuple(
        Skill(name=name, profile=loader.load_skill(name), source_path=package / "skills" / name)
        for name in ("source-evaluation", "briefing-structure")
    )
    return ModiAgent.from_markdown(
        package / "agent.toml",
        tools=[(FETCH_URL_SPEC, fetch_url), (SOURCE_EXTRACT_SPEC, source_extract)],
        skills=skills,
    )


__all__ = ["build_agent", "fetch_url", "source_extract"]
