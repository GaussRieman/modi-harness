"""Stream event renderer for the Modi Harness CLI.

The renderer consumes the harness's stream events (see
``modi_harness.graph.harness_adapter._stream_event``) and turns them into rich
console output suitable for the interactive REPL.

The class is intentionally tiny so it can be unit-tested against a recording
``rich.console.Console`` instance. It does **not** prompt the user for
approvals; when an ``approval_request`` arrives the payload is returned to the
caller (the REPL) which is responsible for invoking the prompt module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

from rich.console import Console, Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

_PROTOCOL_TOOL_NAMES = {
    "request_user_input",
    "create_task_plan",
    "revise_task_plan",
    "start_task",
    "resume_task",
    "complete_task",
    "block_task",
    "submit_output",
}


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
        self._protocol_call_ids: set[str] = set()

    @property
    def console(self) -> Console:
        return self._console

    def render_event(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}

        if event_type == "model_delta":
            self._render_model_delta(payload)
            return None
        if event_type == "tool_call_proposal":
            tool_name = str(payload.get("tool_name") or "")
            if tool_name in _PROTOCOL_TOOL_NAMES:
                call_id = str(payload.get("tool_call_id") or "")
                if call_id:
                    self._protocol_call_ids.add(call_id)
                return None
            self._render_tool_proposal(payload)
            return None
        if event_type == "tool_call_result":
            if str(payload.get("tool_call_id") or "") in self._protocol_call_ids:
                return None
            self._render_tool_result(payload)
            return None
        if event_type == "approval_request":
            # The REPL handles the actual prompt panel.
            return dict(payload)
        if event_type == "interaction_requested":
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
            output_text = _format_terminal_output(response.get("output"))
            if output_text:
                self._console.print(output_text, highlight=False, markup=False)
        elif status in ("failed", "blocked"):
            self._console.print(f"✗ {status}{suffix}", style="red", highlight=False)
            error = response.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("code") or "").strip()
                if message:
                    self._console.print(_truncate(message, 500), style="red", highlight=False)
        elif status == "interrupted":
            self._console.print(f"⏸ {status}{suffix}", style="yellow", highlight=False)
        else:
            self._console.print(f"  {status}{suffix}", highlight=False)


class TaskProgressRenderer(StreamRenderer):
    """Render canonical task events as a live, truthful run-local checklist."""

    _TASK_EVENTS: ClassVar[set[str]] = {
        "task_plan_created",
        "task_plan_revised",
        "task_started",
        "task_resumed",
        "task_completed",
        "task_blocked",
    }

    def __init__(self, console: Console | None = None, *, title: str = "Tasks") -> None:
        super().__init__(console)
        self.title = title
        self.plan: dict[str, Any] | None = None
        self.tool_activity = ""
        self.finalization_activity = ""
        self._tool_names: dict[str, str] = {}
        self._printed_task_states: set[tuple[str, str]] = set()
        self._live: Live | None = None

    def render_event(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == "finalization_started":
            self.finalization_activity = "正在生成最终结果"
            self._refresh()
            return None
        if event_type == "output_repair_started":
            self.finalization_activity = "正在修复输出格式"
            self._refresh()
            return None
        if event_type in self._TASK_EVENTS:
            plan = payload.get("task_plan")
            if isinstance(plan, dict):
                self.plan = plan
                self.tool_activity = ""
                self._print_completed_history()
                self._refresh()
            return None
        if event_type == "tool_call_proposal" and self.plan is not None:
            call_id = str(payload.get("tool_call_id") or "")
            tool_name = str(payload.get("tool_name") or "tool")
            if call_id:
                self._tool_names[call_id] = tool_name
            if tool_name in _PROTOCOL_TOOL_NAMES:
                return None
            self.tool_activity = self.format_tool_start(tool_name, payload.get("arguments") or {})
            self._refresh()
            return None
        if event_type == "tool_call_result" and self.plan is not None:
            call_id = str(payload.get("tool_call_id") or "")
            tool_name = self._tool_names.get(call_id, "tool")
            if tool_name in _PROTOCOL_TOOL_NAMES:
                return None
            self.tool_activity = self.format_tool_result(tool_name, payload.get("content", ""))
            self._refresh()
            return None
        if event_type == "model_delta" and self.plan is not None:
            return None
        if event_type in ("approval_request", "interaction_requested"):
            self.prepare_for_prompt()
            return dict(payload)
        if event_type == "terminal":
            self.close(final=True)
        return super().render_event(event)

    def prepare_for_prompt(self) -> None:
        self.close()

    def close(self, *, final: bool = False) -> None:
        if self._live is not None:
            if final and self.plan is not None:
                items = self.plan.get("items") or []
                completed = sum(item.get("status") == "completed" for item in items)
                self._live.update(
                    Text(f"{self.title} · {completed}/{len(items)}", style="bold green"),
                    refresh=True,
                )
            self._live.stop()
            self._live = None

    def format_tool_start(self, tool_name: str, arguments: dict[str, Any]) -> str:
        del arguments
        return f"{tool_name}  ..."

    def format_tool_result(self, tool_name: str, content: Any) -> str:
        del content
        return f"{tool_name}  done"

    def _refresh(self) -> None:
        if self.plan is None:
            return
        renderable = self._build_renderable()
        if self.console.is_terminal:
            if self._live is None:
                self._live = Live(renderable, console=self.console, refresh_per_second=10)
                self._live.start()
            else:
                self._live.update(renderable, refresh=True)
        else:
            self.console.print(renderable)

    def _print_completed_history(self) -> None:
        if self.plan is None:
            return
        for item in self.plan.get("items") or []:
            task_id = str(item.get("id") or "")
            status = str(item.get("status") or "")
            state_key = (task_id, status)
            if not task_id or state_key in self._printed_task_states:
                continue
            if status == "completed":
                self.console.print(
                    f"✓ {item.get('title', '')}  {_compact_summary(item.get('summary'))}".rstrip(),
                    style="green",
                    highlight=False,
                )
                self._printed_task_states.add(state_key)
            elif status == "blocked":
                self.console.print(
                    f"! {item.get('title', '')}  {_compact_summary(item.get('summary'))}".rstrip(),
                    style="red",
                    highlight=False,
                )
                self._printed_task_states.add(state_key)

    def _build_renderable(self) -> Group:
        assert self.plan is not None
        items = self.plan.get("items") or []
        completed = sum(item.get("status") == "completed" for item in items)
        text = Text(f"{self.title} · {completed}/{len(items)}\n", style="bold")
        markers = {
            "completed": ("✓", "green"),
            "in_progress": ("●", "cyan"),
            "pending": ("○", "dim"),
            "blocked": ("!", "red"),
        }
        for index, item in enumerate(items):
            marker, style = markers.get(item.get("status"), ("?", "yellow"))
            text.append(f"{marker} {item.get('title', '')}", style=style)
            if index < len(items) - 1:
                text.append("\n")
        details: list[Any] = [text]
        if self.tool_activity:
            details.extend([Text(""), Text(self.tool_activity, style="yellow")])
        if self.finalization_activity:
            details.append(Spinner("dots", text=self.finalization_activity, style="cyan"))
        current_action = self.plan.get("current_action")
        if current_action:
            details.append(Spinner("dots", text=str(current_action), style="cyan"))
        return Group(*details)


def _compact_summary(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return _truncate(text, limit)


def _format_terminal_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output.strip()
    if not isinstance(output, dict):
        return _truncate(str(output), 1000)

    lines: list[str] = []
    summary = output.get("executive_summary")
    if summary:
        lines.append(str(summary).strip())
    elif "text" in output:
        lines.append(str(output.get("text") or "").strip())
    elif "value" in output:
        lines.append(str(output.get("value") or "").strip())

    task_results = output.get("task_results")
    if isinstance(task_results, list):
        for item in task_results[:5]:
            if not isinstance(item, dict):
                continue
            task = str(item.get("task") or "").strip()
            result = str(item.get("result") or "").strip()
            if task or result:
                lines.append(f"- {task}: {_truncate(result, 140)}".strip())

    recommendations = output.get("recommendations")
    if isinstance(recommendations, list) and recommendations:
        lines.append("建议: " + "; ".join(str(item) for item in recommendations[:3]))

    return "\n".join(line for line in lines if line).strip()


class JsonlRenderer(StreamRenderer):
    """Emit each canonical event as one machine-readable JSON line."""

    emit_interrupted_terminal = True

    def render_event(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        self.console.print(json.dumps(event, ensure_ascii=False, default=str), markup=False)
        payload = event.get("payload") or {}
        if event.get("event_type") in ("approval_request", "interaction_requested"):
            return dict(payload)
        if event.get("event_type") == "terminal":
            return event.get("terminal_response") or payload.get("response")
        return None


__all__ = [
    "JsonlRenderer",
    "StreamRenderer",
    "TaskProgressRenderer",
    "_truncate",
]
