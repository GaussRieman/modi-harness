"""Native task-protocol tools executed by the graph runtime."""

from __future__ import annotations

from typing import Any

from .._utils import new_ulid, now_iso
from ..tasks import (
    TASK_PROTOCOL_TOOL_NAMES,
    TaskTransitionError,
    block_task,
    complete_task,
    create_task_plan,
    plan_is_complete,
    resume_task,
    revise_task_plan,
    start_task,
)
from ..types import AgentProfile, Message, ToolCallProposal, ToolCallRecord, ToolSpec, TraceEvent
from .state import MainGraphState


def task_protocol_specs(profile: AgentProfile) -> dict[str, ToolSpec]:
    """Return native tool specs only for Agents that opted into the protocol."""
    config = _config(profile)
    if config["mode"] == "off":
        return {}
    schemas: dict[str, tuple[str, dict[str, Any]]] = {
        "create_task_plan": (
            "Create the task plan before beginning work.",
            _object_schema({"tasks": _tasks_schema()}, ["tasks"]),
        ),
        "revise_task_plan": (
            "Replace an unstarted task plan in response to user feedback.",
            _object_schema({"tasks": _tasks_schema()}, ["tasks"]),
        ),
        "start_task": (
            "Mark one pending task as actively being worked on.",
            _object_schema(
                {"task_id": {"type": "string"}, "current_action": {"type": "string"}},
                ["task_id", "current_action"],
            ),
        ),
        "resume_task": (
            "Resume one blocked task after new information or an external condition resolves its blocker.",
            _object_schema(
                {"task_id": {"type": "string"}, "current_action": {"type": "string"}},
                ["task_id", "current_action"],
            ),
        ),
        "complete_task": (
            "Complete the active task and optionally begin the next task.",
            _object_schema(
                {
                    "task_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "next_task_id": {"type": ["string", "null"]},
                    "current_action": {"type": ["string", "null"]},
                },
                ["task_id", "summary"],
            ),
        ),
        "block_task": (
            "Mark the active task blocked and explain why.",
            _object_schema(
                {"task_id": {"type": "string"}, "reason": {"type": "string"}},
                ["task_id", "reason"],
            ),
        ),
    }
    return {name: _spec(name, *schemas[name]) for name in TASK_PROTOCOL_TOOL_NAMES}


def is_task_protocol_tool(name: str) -> bool:
    return name in TASK_PROTOCOL_TOOL_NAMES


def execute_task_protocol(
    state: MainGraphState,
    profile: AgentProfile,
    proposal: ToolCallProposal,
) -> dict[str, Any]:
    """Apply one native transition and return a normal graph state update."""
    name = proposal["tool_name"]
    arguments = proposal.get("arguments") or {}
    record = _record(proposal)
    config = _config(profile)
    try:
        pending_plan = state.get("pending_task_plan")
        active_plan = state.get("task_plan")
        event_type: str
        if name == "create_task_plan":
            plan = create_task_plan(
                arguments.get("tasks") or [],
                min_items=config["min_items"],
                max_items=config["max_items"],
            )
            event_type = "task_plan_created"
        elif name == "revise_task_plan":
            base = pending_plan or active_plan
            if base is None:
                raise TaskTransitionError("no task plan exists to revise")
            plan = revise_task_plan(
                base,
                arguments.get("tasks") or [],
                min_items=config["min_items"],
                max_items=config["max_items"],
            )
            event_type = "task_plan_revised"
        else:
            if active_plan is None:
                raise TaskTransitionError("create and confirm a task plan before execution")
            plan, event_type = _transition_existing(active_plan, name, arguments)
    except TaskTransitionError as exc:
        record["error"] = {"code": "task_transition_rejected", "message": str(exc)}
        record["finished_at"] = now_iso()
        return {
            "tool_calls": [record],
            "messages": [_tool_message(record, f"task transition rejected: {exc}")],
            "pending_trace_events": [
                _event(state, "task_transition_rejected", {"tool_name": name, "reason": str(exc)})
            ],
        }

    record["result"] = {"task_plan": plan}
    record["finished_at"] = now_iso()
    events = [_event(state, event_type, {"task_plan": plan, "tool_call_id": record["tool_call_id"]})]
    if event_type == "task_completed" and plan_is_complete(plan):
        events.append(
            _event(
                state,
                "finalization_started",
                {"task_plan": plan, "tool_call_id": record["tool_call_id"]},
            )
        )
    update: dict[str, Any] = {"tool_calls": [record], "pending_trace_events": events}
    if name in ("create_task_plan", "revise_task_plan") and config["review"] == "before_execution":
        interaction_id = new_ulid()
        interaction = {
            "interaction_id": interaction_id,
            "kind": "plan_review",
            "prompt": "Review the proposed task plan before execution.",
            "payload": {"task_plan": plan},
            "tool_call_id": record["tool_call_id"],
        }
        update["pending_task_plan"] = plan
        update["pending_interaction"] = interaction
        events.append(_event(state, "interaction_requested", interaction))
    else:
        update["task_plan"] = plan
        update["messages"] = [_tool_message(record, "task plan updated")]
    return update


def _transition_existing(plan: Any, name: str, arguments: dict[str, Any]) -> tuple[Any, str]:
    if name == "start_task":
        return start_task(
            plan, str(arguments.get("task_id", "")), current_action=str(arguments.get("current_action", ""))
        ), "task_started"
    if name == "complete_task":
        return complete_task(
            plan,
            str(arguments.get("task_id", "")),
            summary=str(arguments.get("summary", "")),
            next_task_id=arguments.get("next_task_id"),
            current_action=arguments.get("current_action"),
        ), "task_completed"
    if name == "resume_task":
        return resume_task(
            plan,
            str(arguments.get("task_id", "")),
            current_action=str(arguments.get("current_action", "")),
        ), "task_resumed"
    if name == "block_task":
        return block_task(
            plan, str(arguments.get("task_id", "")), reason=str(arguments.get("reason", ""))
        ), "task_blocked"
    raise TaskTransitionError(f"unknown task protocol tool: {name}")


def _config(profile: AgentProfile) -> dict[str, Any]:
    raw = (profile.get("metadata") or {}).get("task_protocol") or {}
    return {
        "mode": raw.get("mode", "off"),
        "review": raw.get("review", "never"),
        "min_items": int(raw.get("min_items", 1)),
        "max_items": int(raw.get("max_items", 8)),
    }


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


def _tool_message(record: ToolCallRecord, content: str) -> Message:
    return Message(
        role="tool",
        content=content,
        tool_call_id=record["tool_call_id"],
        metadata={"tool_name": record["tool_name"]},
    )


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


def _spec(name: str, description: str, schema: dict[str, Any]) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema=schema,
        output_schema=None,
        risk_level="L0",
        side_effect=False,
        permission_scope="",
        allowed_agents=[],
        allowed_skills=[],
        timeout_seconds=0,
        retry=None,
        idempotent=True,
        dry_run_supported=False,
        tags=["task_protocol"],
        kind="protocol",
        subagent_target=None,
    )


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _tasks_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "title": {"type": "string"}},
            "required": ["id", "title"],
            "additionalProperties": False,
        },
    }


__all__ = ["execute_task_protocol", "is_task_protocol_tool", "task_protocol_specs"]
