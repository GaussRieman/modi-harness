"""Modi Harness — Research Assistant (simple).

Minimal demo of the research assistant with auto-generated JSON schema.
No hand-written 40-line YAML schema — the loader generates it from
``output_contract.required_fields`` + ``field_constraints``.

Run from the repo root:
    uv run python examples/research_assistant_simple/run.py
"""

from __future__ import annotations

import asyncio
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console

from modi_harness import ModiAgent, ModiHarness, ModiSession
from modi_harness.cli.runner import run_streaming
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


def fetch_url(url: str) -> dict:
    """Fetch a URL, return its text content."""
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
    return {
        "url": final_url,
        "content_type": content_type,
        "truncated": truncated,
        "size_bytes": len(data),
        "content": body,
    }


FETCH_URL_SPEC = {
    "name": "fetch_url",
    "description": "Fetch a URL and return its text content.",
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
# Main
# ---------------------------------------------------------------------------


async def main(argv: list[str]) -> int:
    console = Console()
    console.print()
    console.print("[bold cyan]Modi Harness — Research Assistant (simple)[/bold cyan]")
    console.print("[dim]Auto-generated JSON schema from output_contract[/dim]")
    console.print()

    # API config — defaults to env, override via env vars if needed:
    #   ANTHROPIC_BASE_URL=https://coding.dashscope.aliyuncs.com/apps/anthropic
    #   ANTHROPIC_AUTH_TOKEN=sk-xxx
    #   ANTHROPIC_MODEL=qwen3.6-plus
    chat_model = create_chat_model(
        provider="anthropic",
        name="",  # empty → uses env default or ANTHROPIC_MODEL
        api_key="",  # empty → uses ANTHROPIC_AUTH_TOKEN env var
        base_url="",  # empty → uses ANTHROPIC_BASE_URL env var
    )

    urls = argv if argv else DEFAULT_URLS
    question = DEFAULT_QUESTION if not argv else (
        "Research the topic represented by the provided URLs and produce a cited briefing."
    )

    console.print(f"[dim]URLs:[/dim] {len(urls)} source(s)")
    console.print()

    here = Path(__file__).parent
    research = ModiAgent.from_markdown(
        here / "agents" / "research-assistant.md",
        tools=[(FETCH_URL_SPEC, fetch_url)],
    )
    harness = ModiHarness(chat_model=chat_model)
    session = ModiSession(
        harness=harness,
        agents=[research],
        checkpointer=MemorySaver(),
        workspace_root=".modi/workspace",
        memory_root="~/.modi/memory",
        max_steps=30,
    )

    user_message = (
        f"Research question: {question}\n\n"
        f"Source URLs:\n"
        + "\n".join(f"- {u}" for u in urls)
    )

    return await run_streaming(
        session,
        agent="research-assistant",
        input={
            "goal": "Produce a cited briefing on the research question.",
            "messages": [{"role": "user", "content": user_message}],
        },
        permission_mode="auto",
        console=console,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
