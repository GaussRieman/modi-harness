"""Stream event renderer for the Modi Harness CLI.

The renderer consumes the harness's stream events (see
``modi_harness.runtime.adapter._stream_event``) and turns them into rich
console output suitable for the interactive REPL.

The class is intentionally tiny so it can be unit-tested against a recording
``rich.console.Console`` instance. It does **not** prompt the user for
approvals; when an ``approval_request`` arrives the payload is returned to the
caller (the REPL) which is responsible for invoking the prompt module.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console


def _truncate(text: str, limit: int) -> str:
    """Return *text* clipped to *limit* characters with an ellipsis.

    A non-positive ``limit`` returns the unchanged input. Strings shorter
    than or equal to ``limit`` are returned untouched.
    """

    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "..."


class StreamRenderer:
    """Dispatch harness stream events to a ``rich.console.Console``.

    Single rule: each branch is responsible for the visual marker (▸/←/✓/✗/⏸)
    and the colour, and must avoid markup interpretation of model output.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else Console()

    @property
    def console(self) -> Console:
        return self._console

    def render_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}

        if event_type == "model_delta":
            self._render_model_delta(payload)
            return None
        if event_type == "tool_call_proposal":
            self._render_tool_proposal(payload)
            return None
        if event_type == "tool_call_result":
            self._render_tool_result(payload)
            return None
        if event_type == "approval_request":
            # The REPL handles the actual prompt panel.
            return dict(payload)
        if event_type == "terminal":
            terminal_response = event.get("terminal_response") or payload.get("response")
            self._render_terminal(terminal_response)
            return terminal_response
        # Unknown / unhandled events (e.g. policy_decision, hook_dispatch) are
        # silently ignored at this stage.
        return None

    # ------------------------------------------------------------------
    # event handlers
    # ------------------------------------------------------------------

    def _render_model_delta(self, payload: dict[str, Any]) -> None:
        delta = payload.get("delta")
        if delta is None:
            delta = payload.get("content", "")
        if not delta:
            return
        # Inline write: no newline, no markup so model output cannot inject styles.
        self._console.print(delta, end="", markup=False, highlight=False)

    def _render_tool_proposal(self, payload: dict[str, Any]) -> None:
        tool_name = payload.get("tool_name", "<unknown>")
        arguments = payload.get("arguments", {})
        try:
            args_repr = json.dumps(arguments, ensure_ascii=False, default=str)
        except TypeError:
            args_repr = str(arguments)
        line = f"▸ {tool_name}({_truncate(args_repr, 80)})"
        self._console.print(line, style="cyan", highlight=False)

    def _render_tool_result(self, payload: dict[str, Any]) -> None:
        content = payload.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        line = f"← {_truncate(content, 200)}"
        self._console.print(line, style="cyan", highlight=False)

    def _render_terminal(self, response: Any) -> None:
        if not isinstance(response, dict):
            return
        status = response.get("status", "")
        elapsed = response.get("elapsed")
        suffix = ""
        if isinstance(elapsed, int | float):
            suffix = f" in {float(elapsed):.1f}s"
        if status == "completed":
            self._console.print(f"✓ {status}{suffix}", style="green", highlight=False)
        elif status in ("failed", "blocked"):
            self._console.print(f"✗ {status}{suffix}", style="red", highlight=False)
        elif status == "interrupted":
            self._console.print(f"⏸ {status}{suffix}", style="yellow", highlight=False)
        else:
            self._console.print(f"  {status}{suffix}", highlight=False)


__all__ = ["StreamRenderer", "_truncate"]
