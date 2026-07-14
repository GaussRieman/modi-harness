"""Streaming runner for the Modi Harness CLI (V0.4b N2).

The runner glues together :class:`StreamRenderer` (event -> rich console) and
:class:`ApprovalPrompt` (interactive approve/reject) on top of
``ModiSession.astream``. It is intentionally focused on a single, governed
turn:

1. Generate a ``thread_id`` upfront so the caller can resume after an
   interrupt without scraping it out of stream events.
2. Stream events from the session, dispatching each to the renderer.
3. When an ``approval_request`` arrives, hand off to the prompt and resume
   the same checkpoint as a stream. Repeated interrupts are handled until the
   run reaches a terminal state or the operator cancels.
4. Print elapsed wall time and return ``0`` on ``status == "completed"``,
   ``1`` otherwise.

The implementation deliberately does not introduce its own event types or
state machine; everything mirrors the existing harness contract so the runner
remains thin and replaceable.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rich.console import Console

from .._utils import new_ulid
from .prompt import InteractionPrompt, JudgmentPrompt
from .renderer import StreamRenderer

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ..api import ModiSession


async def run_streaming(
    session: ModiSession,
    *,
    agent: str,
    input: dict[str, Any],
    thread_id: str | None = None,
    mode: str | None = None,
    permission_mode: str | None = None,
    console: Console | None = None,
    renderer: StreamRenderer | None = None,
    approval_prompt: Any | None = None,
    interaction_prompt: Any | None = None,
    render_start: bool = True,
) -> int:
    """Drive a single governed agent turn against a rich console.

    Returns the process-style exit code: ``0`` for ``completed``, ``1``
    otherwise (failed / interrupted-but-rejected / blocked).
    """

    if console is None:
        console = renderer.console if renderer is not None else Console()
    renderer = renderer if renderer is not None else StreamRenderer(console)
    prompt = approval_prompt if approval_prompt is not None else JudgmentPrompt(console)
    interaction_handler = (
        interaction_prompt if interaction_prompt is not None else InteractionPrompt(console)
    )

    if render_start:
        render_run_start = getattr(renderer, "render_run_start", None)
        if callable(render_run_start):
            render_run_start(agent)
        else:
            console.print(f"[{agent}] running...", style="bold", markup=False, highlight=False)
    started_at = time.monotonic()

    # Always pin a thread_id so a downstream approve/reject can target the
    # same checkpointer entry. Generated IDs follow the same ``run_<ulid>``
    # shape used by the runtime adapter when no thread_id is supplied.
    tid = thread_id if thread_id else f"run_{new_ulid()}"

    final_status: str | None = None
    chosen_mode = mode if mode is not None else permission_mode
    stream = session.astream(
        agent=agent,
        input=input,
        thread_id=tid,
        mode=chosen_mode,  # type: ignore[arg-type]
    )

    while True:
        pending_approval: dict[str, Any] | None = None
        pending_interaction: dict[str, Any] | None = None
        async for event in stream:
            event_type = event.get("event_type")
            if event_type == "approval_request":
                renderer.render_event(event)
                pending_approval = dict(event.get("payload") or {})
                continue
            if event_type == "interaction_requested":
                renderer.render_event(event)
                pending_interaction = dict(event.get("payload") or {})
                continue
            if event_type == "terminal":
                terminal_response: dict[str, Any] = dict(event.get("terminal_response") or {})
                status = terminal_response.get("status")
                if status == "interrupted" and terminal_response.get("pending_approval"):
                    if getattr(renderer, "emit_interrupted_terminal", False):
                        renderer.render_event(event)
                    approval = terminal_response["pending_approval"]
                    assert isinstance(approval, dict)
                    pending_approval = dict(approval)
                    continue
                if status == "interrupted" and terminal_response.get("pending_judgment"):
                    if getattr(renderer, "emit_interrupted_terminal", False):
                        renderer.render_event(event)
                    judgment = terminal_response["pending_judgment"]
                    assert isinstance(judgment, dict)
                    pending_approval = dict(judgment)
                    continue
                if status == "interrupted" and terminal_response.get("pending_interaction"):
                    if getattr(renderer, "emit_interrupted_terminal", False):
                        renderer.render_event(event)
                    interaction = terminal_response["pending_interaction"]
                    assert isinstance(interaction, dict)
                    pending_interaction = dict(interaction)
                    continue
                renderer.render_event(event)
                final_status = status
                continue
            renderer.render_event(event)

        if pending_approval is None and pending_interaction is None:
            break

        prepare_for_prompt = getattr(renderer, "prepare_for_prompt", None)
        if callable(prepare_for_prompt):
            prepare_for_prompt()

        try:
            agent_obj = session.get_agent(agent)
            agent_profile: dict[str, Any] | None = {
                "name": agent_obj.name,
                "description": agent_obj.description,
                "safety_constraints": list(agent_obj.safety_constraints),
            }
        except Exception:
            agent_profile = None

        if pending_interaction is not None:
            decision, value = interaction_handler.ask(pending_interaction, agent=agent_profile)
            resume_payload = {
                "interaction_id": pending_interaction.get("interaction_id", ""),
                "decision": decision,
            }
            if pending_interaction.get("kind") == "user_input":
                resume_payload["value"] = value
            else:
                resume_payload["feedback"] = value or ""
            stream = session.astream_resume(
                thread_id=tid,
                payload=resume_payload,
            )
            continue

        assert pending_approval is not None
        kind, rationale, intent_updates = prompt.ask(pending_approval, agent=agent_profile)
        if kind == "cancel":
            console.print("cancelled", style="yellow")
            final_status = "interrupted"
            break

        resume_payload = {
            "judgment_id": pending_approval.get("judgment_id")
            or pending_approval.get("approval_id", ""),
            "kind": kind,
        }
        if rationale is not None:
            resume_payload["rationale"] = rationale
        if intent_updates:
            resume_payload["intent_updates"] = intent_updates
        stream = session.astream_resume(
            thread_id=tid,
            payload=resume_payload,
        )

    elapsed = time.monotonic() - started_at
    close_renderer = getattr(renderer, "close", None)
    if callable(close_renderer):
        close_renderer()
    console.print(f"elapsed {elapsed:.2f}s", style="dim")

    return 0 if final_status == "completed" else 1


__all__ = ["run_streaming"]
