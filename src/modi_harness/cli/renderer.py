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

import ast
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
        elif status in ("failed", "blocked"):
            self._console.print(f"✗ {status}{suffix}", style="red", highlight=False)
        elif status == "interrupted":
            self._console.print(f"⏸ {status}{suffix}", style="yellow", highlight=False)
        else:
            self._console.print(f"  {status}{suffix}", highlight=False)


class WebagentWorkflowRenderer(StreamRenderer):
    """Render webagent runs as a live 4-step checklist panel.

    Checklist steps (pending → in_progress → completed / blocked / skipped):
      1. parse_police_intake  读取警情文件
      2. confirm_draft        确认草稿
      3. run_police_intake    提交网页表单
      4. save_evidence        保存证据

    The checklist is rendered as a ``rich.Live`` region so spinners animate
    during tool execution.  Persistent details (draft, input hint, results,
    evidence) are printed above the live region while it is paused, then the
    live region resumes below them.  Before interactive prompts the live
    region is stopped so the REPL input line has the terminal to itself.
    """

    _TOOL_LABELS: ClassVar[dict[str, str]] = {
        "parse_police_intake": "读取警情文件",
        "run_police_intake": "提交网页表单",
    }

    _STEP_IDS = ("parse_police_intake", "confirm_draft", "run_police_intake", "save_evidence")

    _STEP_TITLES: ClassVar[dict[str, str]] = {
        "parse_police_intake": "读取警情文件",
        "confirm_draft": "确认草稿",
        "run_police_intake": "提交网页表单",
        "save_evidence": "保存证据",
    }

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def __init__(self, console: Console | None = None) -> None:
        super().__init__(console)
        self._steps: dict[str, str] = {sid: "pending" for sid in self._STEP_IDS}
        self._tool_names: dict[str, str] = {}
        self._tool_activity = ""
        self._finalization_activity = ""
        self._result_rendered = False
        self._diagnostics: list[str] = []
        self._last_draft_signature = ""
        self._live: Live | None = None

    @property
    def _title(self) -> str:
        completed = sum(s == "completed" for s in self._steps.values())
        total = len(self._STEP_IDS)
        return f"网页自动化 · 警情录入 · {completed}/{total}"

    def render_run_start(self, agent: str) -> None:
        self.console.print(
            f"[{agent}] 网页自动化", style="bold", markup=False, highlight=False
        )
        self.console.print("应用", style="bold cyan")
        self.console.print("  警情录入", highlight=False)
        self.console.print("  说明: 读取警情 Markdown, 填写网页表单并提交", highlight=False)

    # ------------------------------------------------------------------
    # event dispatch
    # ------------------------------------------------------------------

    def render_event(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}

        if event_type == "model_delta":
            return None  # suppress chatter

        if event_type == "tool_call_proposal":
            return self._on_tool_proposal(payload)

        if event_type == "tool_call_result":
            return self._on_tool_result(payload)

        if event_type == "approval_request":
            return dict(payload)

        if event_type == "interaction_requested":
            return self._on_interaction(payload)

        if event_type == "output_repair_started":
            issues = payload.get("issues") if isinstance(payload, dict) else None
            if isinstance(issues, list):
                for issue in issues:
                    if isinstance(issue, dict):
                        message = issue.get("message") or issue.get("code")
                        if message:
                            self._remember_diagnostic(str(message))
            return None

        if event_type == "error":
            code = payload.get("code") if isinstance(payload, dict) else None
            if code:
                self._remember_diagnostic(str(code))
            return None

        if event_type == "terminal":
            return self._on_terminal(payload)

        return super().render_event(event)

    # ------------------------------------------------------------------
    # tool proposal / result handlers
    # ------------------------------------------------------------------

    def _on_tool_proposal(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        tool_name = str(payload.get("tool_name") or "")
        call_id = str(payload.get("tool_call_id") or "")
        if call_id:
            self._tool_names[call_id] = tool_name
        if tool_name in _PROTOCOL_TOOL_NAMES:
            return None

        if tool_name == "parse_police_intake":
            self._steps["parse_police_intake"] = "in_progress"
            self._tool_activity = self._format_tool_start(tool_name, payload.get("arguments") or {})
            self._refresh()
            return None

        if tool_name == "run_police_intake":
            # confirm_draft becomes completed when user moves to submission
            if self._steps["confirm_draft"] == "in_progress":
                self._steps["confirm_draft"] = "completed"
            self._steps["run_police_intake"] = "in_progress"
            self._tool_activity = self._format_tool_start(tool_name, payload.get("arguments") or {})
            self._refresh()
            return None

        self._tool_activity = self._format_tool_start(tool_name, payload.get("arguments") or {})
        self._refresh()
        return None

    def _on_tool_result(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        call_id = str(payload.get("tool_call_id") or "")
        tool_name = self._tool_names.get(call_id, "")
        if tool_name in _PROTOCOL_TOOL_NAMES:
            return None

        content = payload.get("content", "")
        result = _coerce_mapping(content)

        if tool_name == "parse_police_intake":
            self._tool_activity = ""
            if result.get("ok") is True:
                self._steps["parse_police_intake"] = "completed"
            else:
                self._steps["parse_police_intake"] = "blocked"
                self._steps["run_police_intake"] = "skipped"
                self._steps["save_evidence"] = "skipped"
            self._refresh()
            self.close()
            self._print_parse_persistent(result)
            return None

        if tool_name == "run_police_intake":
            self._tool_activity = ""
            if result.get("ok") is True:
                self._steps["run_police_intake"] = "completed"
                if result.get("evidence_dir") or result.get("trace_path"):
                    self._steps["save_evidence"] = "completed"
                else:
                    self._steps["save_evidence"] = "skipped"
            else:
                self._steps["run_police_intake"] = "blocked"
                self._steps["save_evidence"] = "skipped"
            self._refresh()
            self.close()
            self._print_run_persistent(result)
            self._result_rendered = True
            return None

        self._tool_activity = ""
        self._refresh()
        return None

    # ------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------

    def _on_interaction(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        interaction_payload = payload.get("payload")
        if not isinstance(interaction_payload, dict):
            return None
        if interaction_payload.get("field") != "draft_confirmation":
            return None

        draft = interaction_payload.get("draft")
        if not isinstance(draft, dict):
            return None

        if self._steps["parse_police_intake"] == "completed":
            self._steps["confirm_draft"] = "in_progress"

        # Freeze the checklist so the REPL prompt has the terminal to itself.
        self._refresh()
        self.prepare_for_prompt()

        if not self._remember_draft(draft):
            return dict(payload)

        fields = draft.get("fields") if isinstance(draft.get("fields"), dict) else {}
        self.console.print("流程", style="bold cyan")
        self.console.print("  应用: 警情录入", highlight=False)
        self.console.print(f"  数据源: {draft.get('intake_path', '')}", highlight=False)
        self.console.print(f"  目标网页: {draft.get('url', '')}", highlight=False)
        self._render_draft_fields(fields, title="草稿已更新")
        self._render_draft_input_hint()
        return dict(payload)

    # ------------------------------------------------------------------
    # terminal
    # ------------------------------------------------------------------

    def _on_terminal(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = payload.get("response") or payload
        if isinstance(response, dict):
            output = response.get("output")
            if isinstance(output, dict) and not self._result_rendered:
                self._print_output_result(output)
            if response.get("status") == "failed":
                self._render_failure_details(response)
        self.close(final=True)
        super()._render_terminal(response)
        return response

    # ------------------------------------------------------------------
    # persistent detail prints (called while live is paused)
    # ------------------------------------------------------------------

    def _print_parse_persistent(self, result: dict[str, Any]) -> None:
        """Print parse result details above the frozen live checklist."""
        if result.get("ok") is not True:
            self.console.print("  ! 读取警情文件失败", style="red", highlight=False)
            error = result.get("error")
            if error:
                self.console.print(f"    原因: {error}", style="red", highlight=False)
            return

        fields = result.get("fields") if isinstance(result.get("fields"), dict) else {}
        self._remember_draft(
            {
                "intake_path": result.get("intake_path", ""),
                "url": result.get("url", ""),
                "fields": fields,
            }
        )
        self.console.print("  ✓ 读取警情文件", style="green", highlight=False)
        self.console.print("流程", style="bold cyan")
        self.console.print("  应用: 警情录入", highlight=False)
        self.console.print(f"  数据源: {result.get('intake_path', '')}", highlight=False)
        self.console.print(f"  目标网页: {result.get('url', '')}", highlight=False)
        self._render_draft_fields(fields, title="草稿")
        self._render_draft_input_hint()

    def _print_run_persistent(self, result: dict[str, Any]) -> None:
        """Print run result details above the frozen live checklist."""
        if result.get("ok") is not True:
            self.console.print("  ! 提交网页表单失败", style="red", highlight=False)
            error = result.get("error")
            if error:
                self.console.print(f"    原因: {error}", style="red", highlight=False)
            self._render_evidence(result)
            return

        submitted = "已提交" if result.get("submitted") else "已填写, 未提交"
        self.console.print(f"  ✓ 提交网页表单: {submitted}", style="green", highlight=False)
        self.console.print("结果", style="bold cyan")
        record_id = result.get("record_id")
        if record_id:
            self.console.print(f"  警情编号: {record_id}", highlight=False)
        self._render_evidence(result)

    def _print_output_result(self, output: dict[str, Any]) -> None:
        self.console.print("结果", style="bold cyan")
        status = output.get("status")
        if status:
            self.console.print(f"  状态: {status}", highlight=False)
        summary = output.get("summary")
        if summary:
            self.console.print(f"  摘要: {summary}", highlight=False)
        self._render_evidence(output)
        failures = output.get("failures")
        if failures:
            self.console.print(f"  问题: {failures}", style="red", highlight=False)
        self._result_rendered = True

    # ------------------------------------------------------------------
    # helper methods (preserved from prior implementation)
    # ------------------------------------------------------------------

    def _format_tool_start(self, tool_name: str, arguments: dict[str, Any]) -> str:
        label = self._TOOL_LABELS.get(tool_name, tool_name)
        return f"{label} ..."

    def _remember_draft(self, draft: dict[str, Any]) -> bool:
        signature = json.dumps(draft, ensure_ascii=False, sort_keys=True)
        if signature == self._last_draft_signature:
            return False
        self._last_draft_signature = signature
        return True

    def _render_draft_fields(self, fields: dict[str, Any], *, title: str) -> None:
        self.console.print(title, style="bold cyan")
        self.console.print(f"  报警人: {fields.get('报警人姓名', '')}", highlight=False)
        self.console.print(f"  电话: {fields.get('报警人联系电话', '')}", highlight=False)
        self.console.print(f"  处警人员: {fields.get('处警人员', '')}", highlight=False)
        self.console.print(f"  地址: {fields.get('警情地址', '')}", highlight=False)
        self.console.print(
            f"  类别: {fields.get('警情类别', '')} / {fields.get('警情类型', '')}",
            highlight=False,
        )
        self.console.print(f"  内容: {fields.get('报警内容描述', '')}", highlight=False)

    def _render_draft_input_hint(self) -> None:
        self.console.print("输入", style="bold cyan")
        self.console.print("  go / 回车 / 确认: 提交录入", highlight=False)
        self.console.print("  字段改成 XXX: 修改后再提交", highlight=False)
        self.console.print("  /cancel: 取消", highlight=False)

    def _render_evidence(self, result: dict[str, Any]) -> None:
        evidence_dir = result.get("evidence_dir")
        trace_path = result.get("trace_path")
        if evidence_dir:
            self.console.print(f"  证据目录: {evidence_dir}", highlight=False)
        if trace_path:
            self.console.print(f"  Trace: {trace_path}", highlight=False)

    def _remember_diagnostic(self, message: str) -> None:
        if message and message not in self._diagnostics:
            self._diagnostics.append(message)

    def _render_failure_details(self, response: dict[str, Any]) -> None:
        self.console.print("错误", style="bold red")
        error = response.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            if code:
                self.console.print(f"  code: {code}", style="red", highlight=False)
            if message:
                self.console.print(f"  message: {message}", style="red", highlight=False)
        for diagnostic in self._diagnostics[-3:]:
            self.console.print(f"  detail: {diagnostic}", style="red", highlight=False)

    # ------------------------------------------------------------------
    # live region helpers
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        renderable = self._build_renderable()
        if self.console.is_terminal:
            if self._live is None:
                self._live = Live(renderable, console=self.console, refresh_per_second=10)
                self._live.start()
            else:
                self._live.update(renderable, refresh=True)
        else:
            self.console.print(renderable)

    def close(self, *, final: bool = False) -> None:
        if self._live is not None:
            if final:
                self._live.update(
                    Text(self._title, style="bold green"), refresh=True
                )
            self._live.stop()
            self._live = None

    def prepare_for_prompt(self) -> None:
        self.close()

    def _build_renderable(self) -> Group:
        text = Text(f"{self._title}\n", style="bold")

        MARKERS: dict[str, tuple[str, str]] = {
            "completed": ("✓", "green"),
            "in_progress": ("●", "cyan"),
            "pending": ("○", "dim"),
            "blocked": ("✗", "red"),
            "skipped": ("-", "dim"),
        }
        for idx, step_id in enumerate(self._STEP_IDS):
            status = self._steps[step_id]
            marker, style = MARKERS.get(status, ("?", "yellow"))
            label = self._STEP_TITLES[step_id]
            text.append(f"{marker} {label}", style=style)
            if idx < len(self._STEP_IDS) - 1:
                text.append("\n")

        details: list[Any] = [text]
        if self._tool_activity:
            details.append(Spinner("dots", text=self._tool_activity, style="cyan"))
        if self._finalization_activity:
            details.append(Spinner("dots", text=self._finalization_activity, style="cyan"))
        return Group(*details)


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


def _coerce_mapping(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {}
    text = content.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


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
    "WebagentWorkflowRenderer",
    "_truncate",
]
