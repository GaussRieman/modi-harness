"""Run the canonical Research Assistant Agent against explicit source URLs."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console

from modi_harness import ModiHarness, ModiSession
from modi_harness.cli.runner import run_streaming
from modi_harness.config import Settings
from modi_harness.discovery import discover_agents
from modi_harness.models import create_chat_model

REPO_ROOT = Path(__file__).resolve().parents[2]


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the canonical Research Assistant")
    parser.add_argument("question", help="research question")
    parser.add_argument(
        "--url",
        action="append",
        required=True,
        dest="urls",
        help="source URL; repeat for multiple sources",
    )
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    args = _arguments(argv)
    console = Console()
    settings = Settings()
    if not settings.model.api_key:
        console.print("[red]MODI_MODEL_API_KEY is required[/red]")
        return 1
    model = create_chat_model(
        provider=settings.model.provider,
        name=settings.model.name,
        api_key=settings.model.api_key,
        base_url=settings.model.base_url,
    )
    agent = discover_agents(cwd=REPO_ROOT).registry.resolve("research-assistant").agent
    session = ModiSession(
        ModiHarness(model),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=REPO_ROOT / ".modi" / "workspace",
        memory_root=REPO_ROOT / ".modi" / "memory",
        max_steps=60,
    )
    thread_id = "research-example"
    exit_code = await run_streaming(
        session,
        agent=agent.name,
        input={"research_question": args.question, "source_urls": args.urls},
        thread_id=thread_id,
        permission_mode="auto",
        console=console,
    )
    counts: dict[str, int] = {}
    for event in session.get_trace(thread_id):
        event_type = event["event_type"]
        counts[event_type] = counts.get(event_type, 0) + 1
    console.print({"trace_events": counts})
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
