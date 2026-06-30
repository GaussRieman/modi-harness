"""Interactive approval prompt for the Modi Harness CLI (V0.4b N1).

The :class:`ApprovalPrompt` is invoked by the streaming runner whenever the
graph yields an ``approval_request`` event. It renders a yellow-bordered
panel summarising the pending action, asks the operator to choose
``[a]pprove`` / ``[r]eject`` / ``[d]etails``, and either returns the decision
to the caller or shows an extended detail panel and re-prompts.

The prompt is purely synchronous; the streaming runner integrates it via the
event payload returned from :class:`modi_harness.cli.renderer.StreamRenderer`
and then resumes the graph through ``Harness.approve_action`` /
``Harness.reject_action``.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .._utils import new_ulid
from .input import read_cli_input

_ARGS_TRUNCATE = 80
_AFFIRMATIVE_INPUTS = {"go", "y", "yes", "ok", "确认", "开始"}


def _is_affirmative(value: str) -> bool:
    return value.strip().lower() in _AFFIRMATIVE_INPUTS


def _split_summary(summary: str) -> tuple[str, str]:
    """Split a ``"tool_name(args)"`` summary into ``(tool, args)``.

    Falls back to ``(summary, "")`` if the shape isn't recognised.
    """

    if not summary:
        return ("", "")
    open_idx = summary.find("(")
    if open_idx <= 0 or not summary.endswith(")"):
        return (summary, "")
    tool = summary[:open_idx].strip()
    args = summary[open_idx + 1 : -1]
    return (tool, args)


def _truncate(text: str, limit: int = _ARGS_TRUNCATE) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "..."


class ApprovalPrompt:
    """Render the approval panel and read the operator's decision."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else Console()

    @property
    def console(self) -> Console:
        return self._console

    def ask(
        self,
        approval: dict[str, Any],
        agent: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Print the approval panel and prompt for a decision.

        Returns ``(decision, reason)`` where ``decision`` is ``"approved"``
        or ``"rejected"``. ``reason`` is ``None`` for approvals and the
        user-supplied string for rejections. The ``[d]etails`` keypress
        prints an extended panel and loops until ``a`` or ``r`` is chosen.
        """

        self._render_summary_panel(approval)
        while True:
            choice = Prompt.ask(
                "[a]pprove [r]eject [d]etails",
                choices=["a", "r", "d"],
                console=self._console,
            )
            if choice == "a":
                return ("approved", None)
            if choice == "r":
                reason = Prompt.ask("Reason", console=self._console)
                return ("rejected", reason)
            # choice == "d"
            self._render_detail_panel(approval, agent)

    # ------------------------------------------------------------------
    # rendering helpers
    # ------------------------------------------------------------------

    def _render_summary_panel(self, approval: dict[str, Any]) -> None:
        summary = str(approval.get("summary", ""))
        tool_name, args = _split_summary(summary)
        # Prefer the human-readable tool name when we can recover it from the
        # summary. Fall back to ``tool_call_id`` (useful when the summary is
        # opaque) and finally to a placeholder.
        tool_label = (
            tool_name
            or approval.get("tool_call_id")
            or "<unknown>"
        )
        risk_level = approval.get("risk_level", "")
        decision_kind = approval.get("decision_kind", "")
        body = (
            f"Tool:  {tool_label}\n"
            f"Args:  {_truncate(args or summary)}\n"
            f"Risk:  {risk_level}\n"
            f"Reason: {decision_kind}"
        )
        panel = Panel(
            body,
            title="Approval requested",
            border_style="yellow",
        )
        self._console.print(panel)

    def _render_detail_panel(
        self,
        approval: dict[str, Any],
        agent: dict[str, Any] | None,
    ) -> None:
        summary = str(approval.get("summary", ""))
        risk_level = approval.get("risk_level", "")
        decision_kind = approval.get("decision_kind", "")
        lines = [
            f"Summary: {summary}",
            f"Risk:    {risk_level}",
            f"Reason:  {decision_kind}",
        ]
        if agent is not None:
            name = agent.get("name")
            if name:
                lines.append(f"Agent:   {name}")
            constraints = agent.get("safety_constraints") or []
            if constraints:
                lines.append("Safety constraints:")
                for item in constraints:
                    lines.append(f"  - {item}")
        body = "\n".join(lines)
        panel = Panel(body, title="Approval details", border_style="cyan")
        self._console.print(panel)


class JudgmentPrompt:
    """Render the judgment panel and collect a human judgment.

    Human participation is judgment, not just approval. Returns
    ``(kind, rationale, intent_updates)`` where ``kind`` is a
    ``HumanJudgmentKind`` and ``intent_updates`` is an ``IntentPatch`` dict
    (empty when the kind carries no edit). The runner feeds these straight to
    ``ModiSession.respond_to_judgment``.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else Console()

    @property
    def console(self) -> Console:
        return self._console

    def ask(
        self,
        judgment: dict[str, Any],
        agent: dict[str, Any] | None = None,
    ) -> tuple[str, str | None, dict[str, Any]]:
        self._render_summary_panel(judgment)
        while True:
            choice = Prompt.ask(
                "[a]pprove [r]eject re[v]ise [c]onstrain [d]etails",
                choices=["a", "r", "v", "c", "d"],
                console=self._console,
            )
            if choice == "a":
                return ("approve", None, {})
            if choice == "r":
                reason = Prompt.ask("Reason", console=self._console)
                return ("reject", reason or None, {})
            if choice == "v":
                new_goal = Prompt.ask("New goal", console=self._console)
                return ("revise", None, {"goal": new_goal})
            if choice == "c":
                statement = Prompt.ask("Boundary", console=self._console)
                boundary = {
                    "id": new_ulid(),
                    "kind": "external_commitment",
                    "statement": statement,
                    "severity": "hard",
                    "escalation": "deny",
                }
                return ("constrain", statement or None, {"add_boundaries": [boundary]})
            # choice == "d"
            self._render_detail_panel(judgment, agent)

    # ------------------------------------------------------------------

    def _render_summary_panel(self, judgment: dict[str, Any]) -> None:
        summary = str(judgment.get("summary", ""))
        tool_name, args = _split_summary(summary)
        tool_label = tool_name or judgment.get("tool_call_id") or "<unknown>"
        risk_level = judgment.get("risk_level", "")
        allowed = ", ".join(judgment.get("allowed_kinds", []) or [])
        body = (
            f"Tool:     {tool_label}\n"
            f"Args:     {_truncate(args or summary)}\n"
            f"Risk:     {risk_level}\n"
            f"Judgment: {allowed}"
        )
        panel = Panel(body, title="Judgment requested", border_style="yellow")
        self._console.print(panel)

    def _render_detail_panel(
        self,
        judgment: dict[str, Any],
        agent: dict[str, Any] | None,
    ) -> None:
        summary = str(judgment.get("summary", ""))
        lines = [
            f"Summary: {summary}",
            f"Risk:    {judgment.get('risk_level', '')}",
            f"Prompt:  {judgment.get('prompt', '')}",
        ]
        if agent is not None:
            name = agent.get("name")
            if name:
                lines.append(f"Agent:   {name}")
            for item in agent.get("safety_constraints") or []:
                lines.append(f"  - {item}")
        panel = Panel("\n".join(lines), title="Judgment details", border_style="cyan")
        self._console.print(panel)


class PlanReviewPrompt:
    """Collect approve, revise, or cancel decisions for a task-plan review."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else Console()

    def ask(
        self,
        interaction: dict[str, Any],
        agent: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        del interaction, agent
        self._console.print()
        self._console.print(
            "Press Enter or type go to approve; type feedback to revise; type /cancel to cancel.",
            style="dim",
        )
        try:
            feedback = read_cli_input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            self._console.print()
            return ("cancelled", None)
        if not feedback or _is_affirmative(feedback):
            return ("approved", None)
        if feedback.lower() == "/cancel":
            return ("cancelled", None)
        return ("revise", feedback)


def _display_prompt(prompt: str, payload: dict[str, Any], agent: dict[str, Any] | None) -> str:
    del payload, agent
    return prompt


class UserInputPrompt:
    """Render a canonical user-input interaction in a terminal."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else Console()

    def ask(
        self,
        interaction: dict[str, Any],
        agent: dict[str, Any] | None = None,
    ) -> tuple[str, Any]:
        prompt = str(interaction.get("prompt") or "Input required")
        payload = interaction.get("payload") or {}
        input_type = payload.get("input_type", "text")
        choices = payload.get("choices") or []
        default = payload.get("default")
        prompt = _display_prompt(prompt, payload, agent)
        self._console.print()
        self._console.print(prompt, style="bold")
        if choices:
            self._console.print(" / ".join(str(choice) for choice in choices), style="dim")
        if input_type in ("confirm", "multiline", "url_list") and default is not None:
            self._console.print(f"默认: {default}", style="cyan", highlight=False)
            hint = (
                "回车/go=使用默认  |  直接输入=替换  |  /cancel=取消"
                if input_type == "confirm"
                else "回车=默认  |  输入内容后空行=结束  |  /cancel=取消"
            )
            self._console.print(hint, style="dim")
        try:
            if input_type in ("multiline", "url_list"):
                return self._read_lines(
                    input_type=input_type,
                    required=payload.get("required", True),
                    default=default,
                )
            value = read_cli_input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            self._console.print()
            return ("cancelled", None)
        if value.lower() == "/cancel":
            return ("cancelled", None)
        if input_type == "confirm" and _is_affirmative(value) and default is not None:
            value = str(default)
        if not value and default is not None:
            value = str(default)
        if payload.get("required", True) and not value:
            self._console.print("需要输入一个值。", style="red")
            return self.ask(interaction)
        if choices and value not in choices:
            self._console.print("Choose one of the listed values.", style="red")
            return self.ask(interaction)
        return ("submitted", value)

    def _read_lines(
        self,
        *,
        input_type: str,
        required: bool,
        default: Any = None,
    ) -> tuple[str, Any]:
        values: list[str] = []
        while True:
            value = read_cli_input("> ").strip()
            if value.lower() == "/cancel":
                return ("cancelled", None)
            if not value:
                if not values and default is not None:
                    return ("submitted", default)
                if required and not values:
                    self._console.print("Enter at least one value.", style="red")
                    continue
                result: Any = values if input_type == "url_list" else "\n".join(values)
                return ("submitted", result)
            if input_type == "url_list" and not value.startswith(("http://", "https://")):
                self._console.print("URL must start with http:// or https://", style="red")
                continue
            values.append(value)


class InteractionPrompt:
    """Dispatch canonical interactions to the matching terminal control."""

    def __init__(self, console: Console | None = None) -> None:
        resolved = console if console is not None else Console()
        self._plan = PlanReviewPrompt(resolved)
        self._input = UserInputPrompt(resolved)

    def ask(
        self,
        interaction: dict[str, Any],
        agent: dict[str, Any] | None = None,
    ) -> tuple[str, Any]:
        if interaction.get("kind") == "plan_review":
            return self._plan.ask(interaction, agent=agent)
        if interaction.get("kind") == "user_input":
            return self._input.ask(interaction, agent=agent)
        raise ValueError(f"unsupported interaction kind: {interaction.get('kind')}")

__all__ = ["ApprovalPrompt", "InteractionPrompt", "PlanReviewPrompt", "UserInputPrompt"]
