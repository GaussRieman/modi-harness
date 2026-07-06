"""Native user-input protocol executed by the graph runtime."""

from __future__ import annotations

from typing import Any

from .._utils import new_ulid, now_iso
from ..types import AgentProfile, ToolCallProposal, ToolCallRecord, ToolSpec, TraceEvent
from .state import MainGraphState

REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
_INPUT_TYPES = {"text", "multiline", "url_list", "confirm"}
_AFFIRMATIVE_INPUTS = {"go", "y", "yes", "ok", "确认", "开始"}


def is_affirmative_input(value: str) -> bool:
    return value.strip().lower() in _AFFIRMATIVE_INPUTS


def normalize_choice_input(value: str, choices: list[Any]) -> str:
    stripped = value.strip()
    if not stripped.isdecimal():
        return value
    index = int(stripped) - 1
    if 0 <= index < len(choices):
        return str(choices[index])
    return value


def interaction_protocol_specs(profile: AgentProfile) -> dict[str, ToolSpec]:
    config = (profile.get("metadata") or {}).get("interaction_protocol") or {}
    if config.get("startup", "prompt") != "agent":
        return {}
    return {REQUEST_USER_INPUT_TOOL_NAME: _spec()}


def is_interaction_protocol_tool(name: str) -> bool:
    return name == REQUEST_USER_INPUT_TOOL_NAME


def execute_interaction_protocol(
    state: MainGraphState,
    proposal: ToolCallProposal,
) -> dict[str, Any]:
    arguments = proposal.get("arguments") or {}
    error = _validate_request(arguments)
    record = _record(proposal)
    if error is not None:
        record["error"] = {"code": "invalid_user_input_request", "message": error}
        record["finished_at"] = now_iso()
        return {
            "tool_calls": [record],
            "messages": [_tool_message(record, f"user input request rejected: {error}")],
            "pending_trace_events": [
                _event(state, "interaction_rejected", {"reason": error})
            ],
        }

    input_type = arguments.get("input_type", "text")
    field = arguments.get("field")
    if field is None and input_type == "url_list":
        field = "source_urls"
    interaction = {
        "interaction_id": new_ulid(),
        "kind": "user_input",
        "prompt": str(arguments["prompt"]).strip(),
        "payload": {
            "input_type": input_type,
            "required": arguments.get("required", True),
            "field": field,
            "default": arguments.get("default"),
            "choices": arguments.get("choices") or [],
        },
        "tool_call_id": record["tool_call_id"],
    }
    record["result"] = {"interaction_id": interaction["interaction_id"]}
    record["finished_at"] = now_iso()
    return {
        "tool_calls": [record],
        "pending_interaction": interaction,
        "pending_trace_events": [
            _event(state, "interaction_requested", dict(interaction))
        ],
    }


def validate_user_input_response(interaction: dict[str, Any], value: Any) -> str | None:
    payload = interaction.get("payload") or {}
    input_type = payload.get("input_type", "text")
    required = payload.get("required", True)
    if input_type == "url_list":
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            return "url_list value must be a list of non-empty strings"
        if required and not value:
            return "a value is required"
        return None
    if not isinstance(value, str):
        return f"{input_type} value must be a string"
    if required and not value.strip() and payload.get("default") in (None, ""):
        return "a value is required"
    choices = payload.get("choices") or []
    default = payload.get("default")
    effective = value.strip()
    if input_type == "confirm" and default is not None and is_affirmative_input(effective):
        effective = str(default)
    if not effective and default is not None:
        effective = str(default)
    if choices:
        effective = normalize_choice_input(effective, choices)
    if choices and effective not in choices:
        return "value must match one of the declared choices"
    return None


def _validate_request(arguments: dict[str, Any]) -> str | None:
    prompt = arguments.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 500:
        return "prompt must contain 1-500 characters"
    input_type = arguments.get("input_type", "text")
    if input_type not in _INPUT_TYPES:
        return f"unsupported input_type: {input_type}"
    required = arguments.get("required", True)
    if not isinstance(required, bool):
        return "required must be a boolean"
    field = arguments.get("field")
    if field is not None and (not isinstance(field, str) or not field.strip()):
        return "field must be a non-empty string"
    choices = arguments.get("choices") or []
    if not isinstance(choices, list) or len(choices) > 20 or any(
        not isinstance(choice, str) or not choice.strip() for choice in choices
    ):
        return "choices must contain at most 20 non-empty strings"
    if input_type == "url_list" and choices:
        return "url_list does not support choices"
    return None


def _record(proposal: ToolCallProposal) -> ToolCallRecord:
    return ToolCallRecord(
        tool_call_id=proposal["tool_call_id"],
        tool_name=proposal["tool_name"],
        arguments=proposal.get("arguments") or {},
        decision="allow",
        result=None,
        error=None,
        started_at=now_iso(),
        finished_at=None,
    )


def _tool_message(record: ToolCallRecord, content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": record["tool_call_id"],
        "metadata": {"tool_name": record["tool_name"]},
    }


def _event(state: MainGraphState, event_type: str, payload: dict[str, Any]) -> TraceEvent:
    return TraceEvent(
        event_id=new_ulid(),
        run_id=state["run_id"],
        root_run_id=state["root_run_id"],
        parent_run_id=state["parent_run_id"],
        thread_id=state["thread_id"],
        timestamp=now_iso(),
        event_type=event_type,
        payload=payload,
        payload_ref=None,
    )


def _spec() -> ToolSpec:
    return ToolSpec(
        name=REQUEST_USER_INPUT_TOOL_NAME,
        description=(
            "Pause and ask the user for information required to continue. "
            "Use this for interactive startup or genuine missing user input."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "maxLength": 500},
                "input_type": {"type": "string", "enum": sorted(_INPUT_TYPES)},
                "required": {"type": "boolean"},
                "field": {"type": ["string", "null"]},
                "default": {},
                "choices": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        output_schema=None,
        risk_level="L0",
        side_effect=False,
        permission_scope="",
        allowed_agents=[],
        allowed_skills=[],
        timeout_seconds=0,
        retry=None,
        idempotent=False,
        dry_run_supported=False,
        tags=["interaction_protocol"],
        kind="protocol",
        subagent_target=None,
    )


__all__ = [
    "REQUEST_USER_INPUT_TOOL_NAME",
    "execute_interaction_protocol",
    "interaction_protocol_specs",
    "is_interaction_protocol_tool",
    "normalize_choice_input",
    "validate_user_input_response",
]
