"""Helpers for tests that script the Brain-loop runtime."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


def as_step_decision_message(msg: AIMessage) -> AIMessage:
    calls = list(getattr(msg, "tool_calls", None) or [])
    if calls and calls[0].get("name") == "submit_step_decision":
        return msg
    if calls:
        call = calls[0]
        tool_name = call["name"]
        args = dict(call.get("args") or {})
        operation = _operation(tool_name, args)
        return step_message(
            {
                "step_kind": "verify" if operation["kind"] == "output_finalize" else "act",
                "reason": f"structured slow Brain selected {tool_name}",
                "intent_patch": None,
                "ask": None,
                "operation": operation,
                "expected_state_change": (
                    {"pending_draft": True}
                    if operation["kind"] == "output_finalize"
                    else None
                ),
                "postcheck": None,
                "continuation": "continue",
                "human_judgment": {
                    "required": False,
                    "reason": "operation is inside the current autonomy scope",
                    "trigger": "none",
                },
                "continuation_basis": {
                    "source": "slow_plan",
                    "reference": tool_name,
                    "reason": f"continue after {tool_name}",
                },
            },
            call_id=f"brain_{call.get('id') or tool_name}",
        )
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    return final_step_message(text)


def final_step_message(text: str) -> AIMessage:
    return step_message(
        {
            "step_kind": "verify",
            "reason": "structured slow Brain finalized the answer",
            "intent_patch": None,
            "ask": None,
            "operation": {
                "kind": "output_finalize",
                "summary": "finalize output",
                "target": "validate_output",
                "arguments": {"draft": text},
                "expected_outcome": "output is validated",
            },
            "expected_state_change": {"pending_draft": True},
            "postcheck": None,
            "continuation": "continue",
            "human_judgment": {
                "required": False,
                "reason": "final output follows the current intent",
                "trigger": "none",
            },
            "continuation_basis": {
                "source": "slow_plan",
                "reference": "output_finalize",
                "reason": "continue into output validation",
            },
        }
    )


def step_message(args: dict[str, Any], *, call_id: str = "brain_step") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "submit_step_decision", "args": args, "id": call_id}],
    )


def _operation(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "submit_output":
        return {
            "kind": "output_finalize",
            "summary": "finalize output",
            "target": "validate_output",
            "arguments": {"draft": args},
            "expected_outcome": "output is validated",
        }
    if tool_name == "transition_stage":
        kind = "stage_transition"
    elif tool_name in {"save_memory", "propose_memory"}:
        kind = "memory_write"
    else:
        kind = "tool"
    return {
        "kind": kind,
        "summary": f"call {tool_name}",
        "target": tool_name,
        "arguments": args,
        "expected_outcome": f"{tool_name} completes",
    }
