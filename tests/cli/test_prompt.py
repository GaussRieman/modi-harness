"""Tests for ``modi_harness.cli.prompt``.

Validates ``ApprovalPrompt.ask`` for the three keypress paths (approve,
reject, details-then-loop) by patching ``rich.prompt.Prompt.ask`` with a
scripted ``Mock(side_effect=[...])``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

from rich.console import Console

from modi_harness.cli.prompt import ApprovalPrompt


def _prompt() -> tuple[ApprovalPrompt, Console]:
    console = Console(record=True, width=200, force_terminal=False)
    return ApprovalPrompt(console), console


def _approval(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "approval_id": "ap1",
        "tool_call_id": "tc1",
        "summary": "search(query='cats')",
        "risk_level": "medium",
        "decision_kind": "require_approval",
    }
    base.update(overrides)
    return base


def test_approve_returns_approved_none() -> None:
    prompt_obj, _console = _prompt()
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "approved"
    assert reason is None
    assert mock.call_count == 1


def test_reject_returns_with_reason() -> None:
    prompt_obj, _console = _prompt()
    mock = Mock(side_effect=["r", "too risky"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "rejected"
    assert reason == "too risky"
    assert mock.call_count == 2


def test_details_then_approve() -> None:
    prompt_obj, console = _prompt()
    mock = Mock(side_effect=["d", "a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "approved"
    assert reason is None
    assert mock.call_count == 2
    text = console.export_text(styles=False)
    # The detail panel should have been printed at least once.
    assert "require_approval" in text
    # The summary text should be present (full, untruncated).
    assert "search(query='cats')" in text


def test_details_then_reject() -> None:
    prompt_obj, _console = _prompt()
    mock = Mock(side_effect=["d", "r", "reason text"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "rejected"
    assert reason == "reason text"
    assert mock.call_count == 3


def test_panel_contains_tool_info() -> None:
    prompt_obj, console = _prompt()
    approval = _approval(summary="search(query='X')")
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(approval)

    text = console.export_text(styles=False)
    # Panel labels and key fields show up in the rendered output.
    assert "Tool:" in text
    assert "Args:" in text
    assert "Risk:" in text
    assert "Reason:" in text
    assert "search" in text
    assert "query='X'" in text
    assert "medium" in text
    assert "require_approval" in text


def test_panel_truncates_long_args() -> None:
    prompt_obj, console = _prompt()
    long_args = "x" * 500
    approval = _approval(summary=f"big_tool(payload='{long_args}')")
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(approval)

    text = console.export_text(styles=False)
    assert "..." in text
    # The full 500-char payload must not appear in the rendered output;
    # truncation kicks in well before that.
    assert "x" * 200 not in text
    args_lines = [line for line in text.splitlines() if "Args:" in line]
    assert args_lines


def test_panel_falls_back_to_summary_when_no_tool_call() -> None:
    prompt_obj, console = _prompt()
    approval = _approval(tool_call_id="", summary="raw_summary_no_parens")
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(approval)

    text = console.export_text(styles=False)
    # Without a parsable name(args) summary we still render something useful
    # for the Tool/Args lines.
    assert "raw_summary_no_parens" in text


def test_details_panel_includes_agent_safety_constraints() -> None:
    prompt_obj, console = _prompt()
    agent = {
        "name": "researcher",
        "safety_constraints": ["no_destructive_io", "review_external_calls"],
    }
    mock = Mock(side_effect=["d", "a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(_approval(), agent=agent)

    text = console.export_text(styles=False)
    assert "researcher" in text
    assert "no_destructive_io" in text
    assert "review_external_calls" in text


def test_details_loops_until_decision() -> None:
    prompt_obj, _console = _prompt()
    # Three details prints in a row, then approve.
    mock = Mock(side_effect=["d", "d", "d", "a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "approved"
    assert reason is None
    assert mock.call_count == 4
