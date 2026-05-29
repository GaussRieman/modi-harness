"""Tests for ``modi_harness.cli.renderer``.

Validates dispatch of stream events into rich console output and the side
return values used by the future REPL (approval payload, terminal response).
"""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console

from modi_harness.cli.renderer import StreamRenderer, _truncate


def _renderer() -> tuple[StreamRenderer, Console]:
    console = Console(record=True, width=200, force_terminal=False)
    return StreamRenderer(console), console


def test_model_delta_inline() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "model_delta",
        "run_id": "r",
        "sequence": 1,
        "payload": {"delta": "hello"},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    text = console.export_text(styles=False)
    assert "hello" in text
    # No trailing newline appended by the renderer itself.
    assert not text.endswith("\n\n")
    # Single delta should not introduce a leading newline.
    assert text.startswith("hello")


def test_model_delta_falls_back_to_content() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "model_delta",
        "run_id": "r",
        "sequence": 1,
        "payload": {"content": "world"},
        "terminal_response": None,
    }

    renderer.render_event(event)

    assert "world" in console.export_text(styles=False)


def test_tool_call_proposal_marker() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "tool_call_proposal",
        "run_id": "r",
        "sequence": 2,
        "payload": {"tool_name": "fs.read", "arguments": {"path": "/tmp/x.txt"}},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    text = console.export_text(styles=False)
    assert "▸" in text
    assert "fs.read" in text
    assert "path" in text
    assert text.endswith("\n")


def test_tool_call_proposal_truncates_arguments() -> None:
    renderer, console = _renderer()
    long_args = {"payload": "x" * 500}
    event = {
        "event_type": "tool_call_proposal",
        "run_id": "r",
        "sequence": 2,
        "payload": {"tool_name": "fs.write", "arguments": long_args},
        "terminal_response": None,
    }

    renderer.render_event(event)

    text = console.export_text(styles=False)
    assert "..." in text
    # Sanity: the line must remain bounded.
    assert len(text.splitlines()[0]) < 200


def test_tool_call_result_marker() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "tool_call_result",
        "run_id": "r",
        "sequence": 3,
        "payload": {"tool_call_id": "tc1", "content": "file contents here"},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    text = console.export_text(styles=False)
    assert "←" in text
    assert "file contents here" in text


def test_tool_call_result_truncates_long_content() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "tool_call_result",
        "run_id": "r",
        "sequence": 3,
        "payload": {"content": "a" * 500},
        "terminal_response": None,
    }

    renderer.render_event(event)

    text = console.export_text(styles=False)
    assert "..." in text


def test_approval_request_returns_payload() -> None:
    renderer, _console = _renderer()
    payload: dict[str, Any] = {
        "approval_id": "ap1",
        "tool_call_id": "tc1",
        "summary": "delete file",
        "risk_level": "high",
        "decision_kind": "require_approval",
    }
    event = {
        "event_type": "approval_request",
        "run_id": "r",
        "sequence": 4,
        "payload": payload,
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result == payload


def test_approval_request_does_not_print() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "approval_request",
        "run_id": "r",
        "sequence": 4,
        "payload": {"approval_id": "ap", "summary": "x"},
        "terminal_response": None,
    }

    renderer.render_event(event)

    assert console.export_text(styles=False) == ""


def test_terminal_completed_green() -> None:
    renderer, console = _renderer()
    response = {
        "run_id": "r",
        "thread_id": "t",
        "status": "completed",
        "output": {"text": "done"},
        "pending_approval": None,
        "error": None,
        "elapsed": 1.234,
    }
    event = {
        "event_type": "terminal",
        "run_id": "r",
        "sequence": 5,
        "payload": {"response": response},
        "terminal_response": response,
    }

    result = renderer.render_event(event)

    assert result == response
    text = console.export_text(styles=False)
    assert "✓" in text
    assert "completed" in text
    assert "1.2" in text


def test_terminal_failed_red() -> None:
    renderer, console = _renderer()
    response = {
        "run_id": "r",
        "thread_id": "t",
        "status": "failed",
        "output": None,
        "pending_approval": None,
        "error": {"code": "boom", "message": "fail"},
    }
    event = {
        "event_type": "terminal",
        "run_id": "r",
        "sequence": 5,
        "payload": {"response": response},
        "terminal_response": response,
    }

    result = renderer.render_event(event)

    assert result == response
    text = console.export_text(styles=False)
    assert "✗" in text
    assert "failed" in text


def test_terminal_interrupted_yellow() -> None:
    renderer, console = _renderer()
    response = {
        "run_id": "r",
        "thread_id": "t",
        "status": "interrupted",
        "output": None,
        "pending_approval": None,
        "error": None,
    }
    event = {
        "event_type": "terminal",
        "run_id": "r",
        "sequence": 5,
        "payload": {"response": response},
        "terminal_response": response,
    }

    renderer.render_event(event)

    text = console.export_text(styles=False)
    assert "⏸" in text
    assert "interrupted" in text


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("hello", 10, "hello"),
        ("hello", 5, "hello"),
        ("hello world", 5, "hello..."),
        ("", 10, ""),
    ],
)
def test_truncate_helper(text: str, limit: int, expected: str) -> None:
    assert _truncate(text, limit) == expected


def test_unknown_event_type_returns_none() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "policy_decision",
        "run_id": "r",
        "sequence": 9,
        "payload": {"foo": "bar"},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    # Unknown events are silently ignored at this stage.
    assert console.export_text(styles=False) == ""
