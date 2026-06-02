"""Streaming runner for the Modi Harness CLI (V0.4b N2).

The runner glues together :class:`StreamRenderer` (event -> rich console) and
:class:`ApprovalPrompt` (interactive approve/reject) on top of
``ModiHarness.astream``. It is intentionally focused on a single, governed
turn:

1. Generate a ``thread_id`` upfront so the caller can resume after an
   interrupt without scraping it out of stream events.
2. Stream events from the harness, dispatching each to the renderer.
3. When an ``approval_request`` arrives, hand off to the prompt, then call
   :meth:`ModiHarness.approve_action` or :meth:`ModiHarness.reject_action`
   to drive the run to its terminal state. The post-resume response is
   re-rendered as a synthesised terminal event so the operator sees the
   final status line.
4. Print elapsed wall time and return ``0`` on ``status == "completed"``,
   ``1`` otherwise.

The implementation deliberately does not introduce its own event types or
state machine; everything mirrors the existing harness contract so the runner
remains thin and replaceable.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from rich.console import Console

from .._utils import new_ulid
from .prompt import ApprovalPrompt
from .renderer import StreamRenderer

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ..api import ModiHarness


async def run_streaming(
    harness: ModiHarness,
    *,
    agent: str,
    input: dict[str, Any],
    thread_id: str | None = None,
    mode: str | None = None,
    permission_mode: str | None = None,
    console: Console | None = None,
) -> int:
    """Drive a single governed agent turn against a rich console.

    Returns the process-style exit code: ``0`` for ``completed``, ``1``
    otherwise (failed / interrupted-but-rejected / blocked).
    """

    console = console if console is not None else Console()
    renderer = StreamRenderer(console)
    prompt = ApprovalPrompt(console)

    console.print(f"[{agent}] running...", style="bold", markup=False, highlight=False)
    started_at = time.monotonic()

    # Always pin a thread_id so a downstream approve/reject can target the
    # same checkpointer entry. Generated IDs follow the same ``run_<ulid>``
    # shape used by the runtime adapter when no thread_id is supplied.
    tid = thread_id if thread_id else f"run_{new_ulid()}"

    final_status: str | None = None
    pending_approval: dict[str, Any] | None = None

    chosen_mode = mode if mode is not None else permission_mode

    async for event in harness.astream(
        agent=agent,
        input=input,
        thread_id=tid,
        mode=chosen_mode,  # type: ignore[arg-type]
    ):
        event_type = event.get("event_type")

        if event_type == "approval_request":
            # Capture the payload and break out — astream will drive to a
            # terminal state on its own once we resume via approve/reject.
            pending_approval = dict(event.get("payload") or {})
            break

        if event_type == "terminal":
            terminal_response = event.get("terminal_response") or {}
            status = terminal_response.get("status")
            # The runtime adapter surfaces an interrupt by lifting the
            # approval payload onto the terminal_response rather than a
            # separate approval_request event. Treat that the same way.
            if status == "interrupted" and terminal_response.get("pending_approval"):
                pending_approval = dict(terminal_response["pending_approval"])
                break
            renderer.render_event(event)
            final_status = status
            continue

        renderer.render_event(event)

    if pending_approval is not None:
        # Best-effort: surface the agent profile to the prompt's detail view.
        agent_profile: dict[str, Any] | None
        try:
            agent_profile = harness._agent_loader.load_agent(agent)  # type: ignore[assignment]
        except Exception:
            agent_profile = None

        decision, reason = prompt.ask(pending_approval, agent=agent_profile)
        approval_id = pending_approval.get("approval_id", "")

        if decision == "approved":
            response = harness.approve_action(thread_id=tid, approval_id=approval_id)
        else:
            response = harness.reject_action(
                thread_id=tid,
                approval_id=approval_id,
                reason=reason or "",
            )

        # Print the model's free-form output before the synthesized terminal
        # marker so the user can see what the agent actually said.
        output = response.get("output")
        if isinstance(output, str) and output:
            console.print(output, markup=False, highlight=False)
        elif isinstance(output, dict) and output:
            console.print(
                json.dumps(output, ensure_ascii=False, indent=2, default=str),
                markup=False,
                highlight=False,
            )

        terminal_event = {
            "event_type": "terminal",
            "payload": {},
            "terminal_response": response,
        }
        renderer.render_event(terminal_event)
        final_status = response.get("status")

    elapsed = time.monotonic() - started_at
    console.print(f"elapsed {elapsed:.2f}s", style="dim")

    return 0 if final_status == "completed" else 1


__all__ = ["run_streaming"]
