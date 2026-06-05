"""Modi Harness — Support Triage (multi-agent delegation).

A markdown `triage` orchestrator classifies a support ticket and routes it to
one of three code-built specialist subagents (billing / technical / refund),
then summarizes the reply. After the run, the delegation is printed from the
session trace.

Demonstrates V0.5 capabilities the other examples don't: recursive subagents,
delegate_to_<name> + allowed_subagents governance, agent isolation, markdown vs
code agents, and introspection.

Run from the repo root (needs a model API key in .env):
    uv run python examples/support_triage/run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from rich.console import Console

from modi_harness import ModiHarness, ModiSession
from modi_harness.config import Settings
from modi_harness.models import create_chat_model

sys.path.insert(0, str(Path(__file__).parent))
from _experts import build_triage_agent  # late import: examples/ isn't a package

# Three sample tickets; change DEFAULT_TICKET to exercise a different route.
TICKETS = {
    "billing": "I was charged $29 on account acct_123 but I thought I cancelled. Why?",
    "refund": "Please refund order ord_555 — I changed my mind within the window.",
    "technical": "The export button does nothing when I click it. How do I fix this?",
}
DEFAULT_TICKET = TICKETS["billing"]


def print_delegation_chain(console: Console, session: ModiSession, thread_id: str) -> None:
    """Surface which specialist the ticket was routed to, from the trace.

    Trace emits `tool_result` events carrying payload['tool_name']. The parent
    run records the `delegate_to_<specialist>` call. Subagent runs are isolated
    child runs with their own trace files, so the specialist's own tool calls
    intentionally do NOT appear here — delegation is the visible boundary.
    """
    console.print("\n[bold]Delegation chain:[/bold]")
    saw_any = False
    for ev in session.get_trace(thread_id):
        if ev["event_type"] != "tool_result":
            continue
        name = ev["payload"].get("tool_name", "")
        if name.startswith("delegate_to_"):
            console.print(f"  triage ──delegate──▶ {name.removeprefix('delegate_to_')}")
            saw_any = True
    if not saw_any:
        console.print("  [dim](no delegation recorded — triage answered directly)[/dim]")


def main() -> int:
    console = Console()
    console.print()
    console.print("[bold cyan]Modi Harness — Support Triage[/bold cyan]")
    console.print("[dim]Multi-agent delegation: triage → billing/technical/refund[/dim]")
    console.print()

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

    triage = build_triage_agent()
    harness = ModiHarness(chat_model=chat_model)
    session = ModiSession(
        harness=harness,
        agents=[triage],
        checkpointer=MemorySaver(),
        workspace_root=".modi/workspace",
        memory_root="~/.modi/memory",
        max_steps=20,
    )

    console.print(f"[dim]top-level (runnable):[/dim] {session.list_agents()}")
    console.print(f"[dim]all agents (incl. nested):[/dim] {session.list_all_agents()}")
    console.print(f"\n[bold]Ticket:[/bold] {DEFAULT_TICKET}\n")

    response = session.run_task(
        agent="triage",
        input={"goal": "Resolve the support ticket.",
               "messages": [{"role": "user", "content": DEFAULT_TICKET}]},
        mode="auto",
    )

    console.print("[bold]Final reply:[/bold]")
    console.print(response.get("output"))
    print_delegation_chain(console, session, response["thread_id"])
    return 0 if response["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
