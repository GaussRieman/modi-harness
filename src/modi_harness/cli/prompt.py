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

_ARGS_TRUNCATE = 80


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
        panel = Panel(
            body,
            title="Approval details",
            border_style="cyan",
        )
        self._console.print(panel)


__all__ = ["ApprovalPrompt"]
