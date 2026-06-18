from __future__ import annotations

from typing import Any

from modi_harness.graph.task_protocol import execute_task_protocol, task_protocol_specs


def _profile(mode: str = "required", review: str = "never") -> dict[str, Any]:
    return {
        "metadata": {
            "task_protocol": {
                "mode": mode,
                "review": review,
                "min_items": 1,
                "max_items": 8,
            }
        }
    }


def _state() -> dict[str, Any]:
    return {
        "run_id": "run",
        "root_run_id": "run",
        "parent_run_id": None,
        "thread_id": "thread",
        "task_plan": None,
        "pending_task_plan": None,
    }


def test_protocol_specs_are_opt_in() -> None:
    assert task_protocol_specs(_profile(mode="off")) == {}  # type: ignore[arg-type]
    assert set(task_protocol_specs(_profile())) == {  # type: ignore[arg-type]
        "create_task_plan",
        "revise_task_plan",
        "start_task",
        "resume_task",
        "complete_task",
        "block_task",
    }


def test_create_plan_requests_first_class_review() -> None:
    update = execute_task_protocol(  # type: ignore[arg-type]
        _state(),
        _profile(review="before_execution"),
        {
            "tool_call_id": "call",
            "tool_name": "create_task_plan",
            "arguments": {"tasks": [{"id": "one", "title": "First"}]},
        },
    )

    assert update["pending_task_plan"]["items"][0]["status"] == "pending"
    assert update["pending_interaction"]["kind"] == "plan_review"
    assert "messages" not in update


def test_invalid_transition_returns_tool_feedback_without_mutating_plan() -> None:
    state = _state()
    update = execute_task_protocol(  # type: ignore[arg-type]
        state,
        _profile(),
        {
            "tool_call_id": "call",
            "tool_name": "start_task",
            "arguments": {"task_id": "missing", "current_action": "Work"},
        },
    )

    assert "task_plan" not in update
    assert update["tool_calls"][0]["error"]["code"] == "task_transition_rejected"
