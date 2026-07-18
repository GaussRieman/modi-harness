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

from rich.console import Console, ConsoleRenderable, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

_PROTOCOL_TOOL_NAMES = {
    "request_user_input",
    "create_task_plan",
    "revise_task_plan",
    "start_task",
    "resume_task",
    "record_research_finding",
    "complete_task",
    "block_task",
    "submit_output",
}
_SOURCE_TYPE_LABELS = {
    "official": "官方",
    "primary": "一手来源",
    "reputable_media": "可信媒体",
    "industry_report": "行业报告",
    "job_board": "招聘样本",
    "secondary": "二手来源",
}
_CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}


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
        if event_type == "workflow_selected":
            workflow_id = str(payload.get("workflow_id") or "workflow")
            label = workflow_id.replace("_", " ")
            self._console.print(f"◆ {label}", style="bold cyan", highlight=False)
            summary = str(payload.get("summary") or "").strip()
            if summary:
                self._console.print(f"  {summary}", style="dim", highlight=False)
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
        if event_type == "node_started":
            node_id = str(payload.get("node_id") or "node")
            self._console.print(f"… {node_id}", style="cyan", highlight=False)
            return None
        if event_type == "operation_started":
            adapter_id = str(payload.get("adapter_id") or "operation")
            if adapter_id in _PROTOCOL_TOOL_NAMES:
                return None
            self._console.print(f"▸ {adapter_id}", style="cyan", highlight=False)
            return None
        if event_type == "operation_completed":
            adapter_id = str(payload.get("adapter_id") or "operation")
            if adapter_id in _PROTOCOL_TOOL_NAMES:
                return None
            self._console.print(f"← {adapter_id} done", style="cyan", highlight=False)
            return None
        if event_type == "completion_rejected":
            feedback = _truncate(str(payload.get("feedback") or "completion rejected"), 240)
            if feedback == "complete_node requires result":
                return None
            self._console.print(f"↻ {feedback}", style="yellow", highlight=False)
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
        self._suppress_model_output = False
        self._deep_research = False
        self._scope_subject = ""
        self._scope_question = ""
        self._scope_preview_active = False
        self._scope_request_ids: set[str] = set()
        self._task_graph_mode = False

    def render_event(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == "workflow_selected" and payload.get("workflow_id") == "deep_research":
            self.title = "Research questions"
            self._suppress_model_output = True
            self._deep_research = True
            return None
        if event_type in self._TASK_EVENTS:
            plan = payload.get("task_plan")
            if isinstance(plan, Mapping) and plan.get("kind") == "task_graph":
                self._task_graph_mode = True
        if self._deep_research and event_type in {
            "node_started",
            "operation_started",
            "operation_completed",
            "tool_call_proposal",
            "tool_call_result",
            "completion_rejected",
            "finalization_started",
            "output_repair_started",
        }:
            return None
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
                if not self._deep_research:
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
        if event_type == "model_delta" and (
            self.plan is not None or self._suppress_model_output
        ):
            return None
        if event_type == "interaction_requested" and self._render_scope_review(payload):
            return dict(payload)
        if event_type in ("approval_request", "interaction_requested"):
            self.prepare_for_prompt()
            return dict(payload)
        if event_type == "terminal":
            self.close(final=True)
            if self._deep_research:
                terminal_response = event.get("terminal_response") or payload.get("response")
                if isinstance(terminal_response, dict) and terminal_response.get("status") == (
                    "completed"
                ):
                    output_text = _format_terminal_output(terminal_response.get("output"))
                    if output_text:
                        self.console.print(output_text, highlight=False, markup=False)
                    return terminal_response
        return super().render_event(event)

    def prepare_for_prompt(self) -> None:
        if self._scope_preview_active:
            return
        self.close()

    def resume_after_prompt(
        self,
        interaction: Mapping[str, Any],
        decision: str,
    ) -> None:
        """End the static scope preview before live research progress begins."""

        if not self._scope_preview_active:
            return
        self._scope_preview_active = False
        del interaction, decision

    def _render_scope_review(self, interaction: Mapping[str, Any]) -> bool:
        if not self._deep_research or interaction.get("kind") != "node_review":
            return False
        payload = interaction.get("payload")
        draft = payload.get("draft") if isinstance(payload, Mapping) else None
        if not isinstance(draft, Mapping):
            return False
        planning_context = draft.get("planning_context")
        planning_context = (
            planning_context if isinstance(planning_context, Mapping) else {}
        )
        self._scope_subject = str(
            draft.get("subject") or planning_context.get("subject") or ""
        ).strip()
        self._scope_question = str(
            draft.get("research_question")
            or planning_context.get("research_question")
            or draft.get("goal")
            or ""
        ).strip()
        raw_plan = draft.get("task_plan")
        raw_items = (
            raw_plan.get("items")
            if isinstance(raw_plan, Mapping)
            else planning_context.get("candidate_dimensions")
        )
        if not isinstance(raw_items, list | tuple):
            return False
        self.plan = {
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "title": str(
                        item.get("title")
                        or item.get("question")
                        or item.get("dimension")
                        or ""
                    ),
                    "status": "pending",
                    "summary": None,
                }
                for item in raw_items
                if isinstance(item, Mapping)
            ],
            "current_action": None,
        }
        self._scope_preview_active = True
        request_id = str(interaction.get("interaction_id") or "")
        if request_id and request_id in self._scope_request_ids:
            return True
        if request_id:
            self._scope_request_ids.add(request_id)
        self.console.print(self._build_renderable())
        return True

    def close(self, *, final: bool = False) -> None:
        if self._live is not None:
            if final and self.plan is not None:
                self._live.update(self._build_renderable(), refresh=True)
            self._live.stop()
            self._live = None
        self._scope_preview_active = False

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
                self._live = Live(
                    renderable,
                    console=self.console,
                    refresh_per_second=4,
                )
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
                summary = str(item.get("summary") or "")
                limited = summary.startswith("[limited]")
                marker = "△" if limited else "✓"
                style = "yellow" if limited else "green"
                self.console.print(
                    f"{marker} {item.get('title', '')}  {_compact_summary(summary)}".rstrip(),
                    style=style,
                    highlight=False,
                )
                self._printed_task_states.add(state_key)
            elif status == "blocked":
                summary = str(item.get("summary") or "")
                insufficient = summary.startswith("Evidence insufficient")
                self.console.print(
                    f"{'△' if insufficient else '!'} {item.get('title', '')}  "
                    f"{_compact_summary(summary)}".rstrip(),
                    style="yellow" if insufficient else "red",
                    highlight=False,
                )
                self._printed_task_states.add(state_key)

    def _build_renderable(self) -> ConsoleRenderable:
        assert self.plan is not None
        items = self.plan.get("items") or []
        completed = sum(item.get("status") == "completed" for item in items)
        graph_status = str(self.plan.get("graph_status") or "")
        graph_status_suffix = (
            f" · {graph_status.replace('_', ' ')}"
            if self._task_graph_mode and graph_status not in {"", "active"}
            else ""
        )
        running = self._deep_research and any(
            item.get("status") in {"pending", "in_progress"} for item in items
        )
        title = (
            f"Task Graph · {completed}/{len(items)}{graph_status_suffix}"
            if self._task_graph_mode
            else f"{self.title} · {completed}/{len(items)}"
        )
        if running:
            header: Any = Spinner("dots", text=title, style="cyan")
        else:
            header = Text(title, style="bold green" if self._deep_research else "bold")
        text = Text()
        visible_items = (
            items
            if self._deep_research or self._task_graph_mode
            else [item for item in items if item.get("status") != "completed"]
        )
        for index, item in enumerate(visible_items):
            status = item.get("status")
            summary = str(item.get("summary") or "")
            if item.get("retiring") or item.get("attempt_status") == "cancelled":
                marker, style = "↻", "yellow"
            elif status == "completed" and summary.startswith("[limited]"):
                marker, style = "△", "yellow"
            elif status == "completed":
                marker, style = "✓", "green"
            elif status == "blocked":
                marker, style = "△", "yellow"
            elif status == "cancelled":
                marker, style = "✗", "red"
            elif status == "waiting_human":
                marker, style = "?", "yellow"
            else:
                marker, style = {
                    "in_progress": ("●", "cyan"),
                    "pending": ("○", "dim"),
                    "blocked": ("!", "red"),
                }.get(status, ("?", "yellow"))
            text.append(f"{marker} {item.get('title', '')}", style=style)
            summary = (
                _compact_summary(item.get("summary"))
                if self._task_graph_mode
                else ""
            )
            if summary:
                text.append(f" — {summary}", style="dim")
            child = item.get("child")
            if self._task_graph_mode and isinstance(child, Mapping):
                child_run_id = str(child.get("run_id") or "child")
                child_status = str(child.get("status") or "unknown").replace("_", " ")
                child_revision = child.get("revision")
                revision = f" · r{child_revision}" if child_revision is not None else ""
                text.append(
                    f"\n  ↳ {child_run_id} · {child_status}{revision}",
                    style="dim",
                )
            if index < len(visible_items) - 1:
                text.append("\n")
        details: list[Any] = [text] if self._task_graph_mode else [header, text]
        if self.tool_activity and not self._deep_research:
            details.extend([Text(""), Text(self.tool_activity, style="yellow")])
        if self.finalization_activity:
            details.append(Spinner("dots", text=self.finalization_activity, style="cyan"))
        current_action = None if self._deep_research else self.plan.get("current_action")
        if current_action:
            details.append(Spinner("dots", text=str(current_action), style="cyan"))
        human_request = self.plan.get("current_human_request")
        if isinstance(human_request, Mapping):
            request_id = str(human_request.get("request_id") or "")
            prompt = str(human_request.get("prompt") or "human decision required")
            details.append(Text(f"? {prompt} [{request_id}]", style="yellow"))
        content = Group(*details)
        if self._task_graph_mode:
            return Panel(content, title=title, border_style="cyan")
        if self._deep_research:
            if not self._scope_preview_active:
                return Panel(
                    content,
                    title="Research progress",
                    border_style="cyan",
                )
            scope = Text()
            if self._scope_subject:
                scope.append(f"主体: {self._scope_subject}\n")
            if self._scope_question:
                scope.append(f"目标: {self._scope_question}\n\n")
            return Panel(
                Group(scope, content) if scope.plain else content,
                title="Research scope",
                border_style="cyan",
            )
        return content


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

    citations = output.get("citations") or output.get("sources")
    citation_list = (
        list(dict.fromkeys(str(item) for item in citations))
        if isinstance(citations, list | tuple)
        else []
    )
    citation_numbers = {url: index for index, url in enumerate(citation_list, start=1)}

    lines: list[str] = []
    summary = output.get("direct_answer") or output.get("executive_summary")
    if summary:
        lines.append(str(summary).strip())
    elif "text" in output:
        lines.append(str(output.get("text") or "").strip())
    elif "value" in output:
        lines.append(str(output.get("value") or "").strip())

    key_findings = output.get("key_findings")
    if isinstance(key_findings, list) and key_findings:
        lines.append("关键发现:")
        for item in key_findings[:5]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "研究发现").strip()
            conclusion = str(item.get("conclusion") or "").strip()
            implications = str(item.get("implications") or "").strip()
            confidence = str(item.get("confidence") or "").strip()
            lines.append(f"- {question}: {conclusion}".rstrip())
            if implications:
                lines.append(f"  意义: {implications}")
            evidence = item.get("evidence")
            if isinstance(evidence, list):
                for evidence_item in evidence[:3]:
                    if not isinstance(evidence_item, dict):
                        continue
                    claim = str(evidence_item.get("claim") or "").strip()
                    source_url = str(evidence_item.get("source_url") or "").strip()
                    number = citation_numbers.get(source_url)
                    reference = f" [{number}]" if number is not None else ""
                    source_type = str(evidence_item.get("source_type") or "").strip()
                    source_type = _SOURCE_TYPE_LABELS.get(source_type, source_type)
                    as_of = str(evidence_item.get("as_of") or "").strip()
                    qualifier = ", ".join(item for item in (source_type, as_of) if item)
                    suffix = f" ({qualifier})" if qualifier else ""
                    if claim:
                        lines.append(f"  证据: {claim}{reference}{suffix}")
            if confidence:
                lines.append(f"  置信度: {_CONFIDENCE_LABELS.get(confidence, confidence)}")
    else:
        task_results = output.get("task_results")
        if not isinstance(task_results, list):
            task_results = []
        for item in task_results[:5]:
            if not isinstance(item, dict):
                continue
            task = str(item.get("question") or item.get("task") or "").strip()
            result = str(item.get("result") or "").strip()
            if task or result:
                lines.append(f"- {task}: {_truncate(result, 140)}".strip())

    recommendations = output.get("recommendations")
    if isinstance(recommendations, list) and recommendations:
        lines.append("建议: " + "; ".join(str(item) for item in recommendations[:3]))

    limitations = output.get("limitations") or output.get("source_limitations")
    if isinstance(limitations, list) and limitations:
        lines.append("限制:")
        lines.extend(f"- {item!s}" for item in limitations[:5])

    if citation_list:
        lines.append("来源:")
        lines.extend(f"[{index}] {url}" for index, url in enumerate(citation_list[:8], start=1))

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
