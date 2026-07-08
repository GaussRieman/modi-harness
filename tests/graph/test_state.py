"""AgentState reducer behavior under LangGraph semantics."""

from __future__ import annotations

import operator
from typing import get_args, get_origin, get_type_hints

from modi_harness.types import AgentState


def _reducer(field: str):
    hints = get_type_hints(AgentState, include_extras=True)
    tp = hints[field]
    assert get_origin(tp) is not None, f"{field} should be Annotated"
    args = get_args(tp)
    assert len(args) >= 2, f"{field} Annotated must carry a reducer"
    return args[1]


def test_messages_uses_add_reducer() -> None:
    assert _reducer("messages") is operator.add


def test_tool_calls_uses_add_reducer() -> None:
    assert _reducer("tool_calls") is operator.add


def test_denied_actions_uses_add_reducer() -> None:
    assert _reducer("denied_actions") is operator.add


def test_workspace_refs_uses_add_reducer() -> None:
    assert _reducer("workspace_refs") is operator.add


def test_pending_trace_events_field_present() -> None:
    hints = get_type_hints(AgentState, include_extras=True)
    assert "pending_trace_events" in hints
    assert _reducer("pending_trace_events") is operator.add


def test_repair_used_and_parent_thread_id_present() -> None:
    hints = get_type_hints(AgentState, include_extras=True)
    assert "repair_used" in hints
    assert "parent_thread_id" in hints


def test_loop_runtime_fields_present() -> None:
    hints = get_type_hints(AgentState, include_extras=True)
    assert "loop_state" in hints
    assert "step_records" in hints
    assert "current_step" in hints
    assert "last_continuation_decision" in hints
    assert _reducer("step_records") is operator.add
