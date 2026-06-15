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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


_MAX_BYTES = 256 * 1024
_MAX_PREVIEW_CHARS = 1800
_MAX_CARD_FACTS = 8


def fetch_url(url: str) -> dict:
    """Fetch a URL and return a compact evidence card, not the full page."""
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
    if "html" in content_type.lower():
        parser = _TextExtractor()
        try:
            parser.feed(body)
            body = parser.text()
        except Exception:
            pass
    card = source_extract(url=final_url, content=body, content_type=content_type)
    return {
        "url": final_url,
        "content_type": content_type,
        "truncated": truncated,
        "size_bytes": len(data),
        "source_tokens_estimate": max(1, len(body.encode("utf-8")) // 4),
        "evidence_card": card["evidence_card"],
        "content_preview": body[:_MAX_PREVIEW_CHARS],
    }


FETCH_URL_SPEC = {
    "name": "fetch_url",
    "description": "Fetch a URL and return a compact evidence card plus a short content preview.",
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
    sentences = re.split(r"(?<=[.!?。！？])\s+", content)
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

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_URLS = [
    "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)",
    "https://en.wikipedia.org/wiki/Recurrent_neural_network",
    "https://en.wikipedia.org/wiki/Attention_(machine_learning)",
]

DEFAULT_QUESTION = (
    "Transformer 和 RNN 在序列建模上有何区别？各自在哪些场景下表现更好？"
)


# ---------------------------------------------------------------------------
# Context / Workspace / Memory / Trace demo helpers
# ---------------------------------------------------------------------------


def build_research_agent(base_dir: Path | None = None) -> ModiAgent:
    here = base_dir or Path(__file__).parent
    return ModiAgent.from_markdown(
        here / "agents" / "research-assistant.md",
        tools=[(FETCH_URL_SPEC, fetch_url), (SOURCE_EXTRACT_SPEC, source_extract)],
    )


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
            "body": "研究简报必须把关键判断和证据来源绑定，用简短 citation labels 标明出处。",
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
            "body": "比较模型时优先覆盖：核心结构差异、训练/推理权衡、适用场景和局限。",
            "tags": ["research", "model-comparison"],
            "metadata": {"approved": True},
        },
        {
            "id": "ra_reference_locomotion",
            "scope": "agent",
            "type": "reference",
            "name": "memory-benchmark-note",
            "description": "Pointer: memory benchmarks and recall quality belong in references, not raw body.",
            "body": "如果任务涉及 Memory benchmark，只保存指针和摘要，不保存大段网页正文。",
            "tags": ["memory", "reference"],
        },
        {
            "id": "ra_expired_old_style",
            "scope": "agent",
            "type": "feedback",
            "name": "expired-style",
            "description": "Expired demo record; should not enter context.",
            "body": "过期示例：这条不应该被注入上下文。",
            "tags": ["research"],
            "expires_at": "2000-01-01T00:00:00.000Z",
        },
        {
            "id": "ra_superseded_old_frame",
            "scope": "agent",
            "type": "project",
            "name": "old-frame",
            "description": "Superseded demo record; should not enter context.",
            "body": "被替代示例：这条不应该被注入上下文。",
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
# Main
# ---------------------------------------------------------------------------


async def main(argv: list[str]) -> int:
    console = Console()
    console.print()
    console.print("[bold cyan]Modi Harness — Research Assistant (Execution efficiency demo)[/bold cyan]")
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

    urls = argv if argv else DEFAULT_URLS
    question = DEFAULT_QUESTION if not argv else (
        "Research the topic represented by the provided URLs and produce a cited briefing."
    )

    console.print(f"[dim]URLs:[/dim] {len(urls)} source(s)")
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

    user_message = (
        f"Research question: {question}\n\n"
        f"Source URLs:\n"
        + "\n".join(f"- {u}" for u in urls)
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
    )
    print_memory_trace_summary(console, session, thread_id)
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
