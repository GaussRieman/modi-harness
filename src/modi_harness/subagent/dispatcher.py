"""Subagent dispatch.

When :class:`ToolGateway` sees a ToolSpec with ``kind == "subagent"`` it
delegates here. The dispatcher:

1. Validates schema + visibility (parent ``allowed_subagents``).
2. Runs the denied-retry guard against the child-call fingerprint.
3. Asks Policy Gate for a subagent decision (depth + permission_mode
   tightening on top of the standard L2 risk evaluation).
4. Builds a child :class:`MainGraphState` with parent thread/run lineage
   propagated and the parent's denied_actions copied in.
5. Invokes the child compiled graph (same builder as the main graph),
   passing the child thread_id ``"<parent_thread>/sub/<ulid>"``.
6. Translates the child terminal state into a :class:`ToolDispatchResult`
   that the parent's ``execute_tool`` node consumes like any other tool
   result, with the child output wrapped as untrusted.
"""

from __future__ import annotations

from typing import Any

from .._utils import compute_fingerprint, new_ulid, now_iso, task_input_to_text
from ..graph.deps import GraphDeps
from ..tools.gateway import ToolDispatchResult
from ..types import (
    AgentProfile,
    AgentState,
    DeniedAction,
    Message,
    PermissionMode,
    ToolCallProposal,
    ToolCallRecord,
    ToolSpec,
    TrustAnnotation,
    WorkspaceRef,
)


_MODE_RANK: dict[str, int] = {"bypass": 0, "trust": 0, "auto": 1, "ask": 2, "plan": 3, "preview": 3}


class SubagentError(Exception):
    """Subagent dispatch failure surfaced as a tool error."""


def dispatch_subagent(
    *,
    proposal: ToolCallProposal,
    spec: ToolSpec,
    parent_agent: AgentProfile,
    parent_state: AgentState,
    deps: GraphDeps,
    subagent_max_depth: int,
) -> ToolDispatchResult:
    started_at = now_iso()
    args = proposal["arguments"]

    # 1. Visibility (parent allowed_subagents).
    permission_profile = parent_agent.get("permission_profile") or {}
    allowed = permission_profile.get("allowed_subagents") or []
    target = spec["subagent_target"]
    if not _allowed(allowed, target):
        return _error_result(
            proposal,
            started_at,
            f"agent {parent_agent['name']!r} cannot dispatch subagent {target!r}",
        )

    # 2. Denied-retry guard at the dispatch level.
    fingerprint = compute_fingerprint(
        {"tool": spec["name"], "args": args}
    )
    denied_fps = {d["fingerprint"] for d in parent_state["denied_actions"]}
    if fingerprint in denied_fps:
        return _denied_result(proposal, started_at, "denied_retry")

    # 3. Depth check.
    parent_thread = parent_state["thread_id"] or ""
    depth = parent_thread.count("/sub/") + 1
    cap = permission_profile.get("subagent_max_depth") or subagent_max_depth
    if depth > cap:
        return _denied_result(proposal, started_at, f"subagent_depth_exceeded ({depth} > {cap})")

    # 4. Permission mode tightening.
    requested_mode = args.get("permission_mode") or parent_state["permission_mode"]
    parent_rank = _MODE_RANK[parent_state["permission_mode"]]
    requested_rank = _MODE_RANK[requested_mode]
    if requested_rank < parent_rank:
        return _denied_result(
            proposal,
            started_at,
            f"child mode {requested_mode!r} laxer than parent {parent_state['permission_mode']!r}",
        )

    # 5. Build child state.
    child_run_id = new_ulid()
    child_thread_id = f"{parent_thread or 'root'}/sub/{new_ulid()}"
    child_agent_name = target
    if not child_agent_name:
        return _error_result(proposal, started_at, "subagent_target missing on spec")

    child_input = args.get("task") or {}
    child_state: dict[str, Any] = {
        "run_id": child_run_id,
        "root_run_id": parent_state["root_run_id"],
        "parent_run_id": parent_state["run_id"],
        "parent_thread_id": parent_thread,
        "thread_id": child_thread_id,
        "agent_name": child_agent_name,
        "permission_mode": requested_mode,
        "task": child_input,
        "messages": [
            Message(  # type: ignore[typeddict-item]
                role="user",
                content=task_input_to_text(child_input),
                tool_call_id=None,
                metadata={},
            )
        ],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": list(parent_state["denied_actions"]),
        "workspace_refs": [],
        "pending_approval": None,
        "human_context": {"version": 0, "inputs": {}, "decisions": [], "feedback": []},
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
        "pending_trace_events": [],
        "repair_used": 0,
        "max_steps": deps.max_steps,
    }

    # 6. Invoke child graph (lazy import avoids cycle).
    from ..graph import build_main_graph
    from ..graph.trace_middleware import TraceMiddleware

    # Use the same checkpointer? For child runs we want a separate ephemeral
    # MemorySaver — child threads live for the duration of the parent dispatch
    # and don't need cross-process resume independent of the parent.
    from langgraph.checkpoint.memory import MemorySaver

    child_graph = build_main_graph(deps, checkpointer=MemorySaver())
    child_config = {
        "configurable": {
            "thread_id": child_thread_id,
            "modi_deps": deps,
        }
    }
    child_final = child_graph.invoke(child_state, config=child_config)
    TraceMiddleware(deps.workspace).flush(child_final)

    # 7. Wire child workspace as ref under parent.
    new_denied = _diff_denied(parent_state["denied_actions"], child_final.get("denied_actions") or [])
    child_workspace_refs: list[WorkspaceRef] = list(child_final.get("workspace_refs") or [])

    # 8. Wrap child output as untrusted block conceptually; here we just produce
    # the tool result payload.
    status = child_final.get("status")
    if status == "completed":
        result_payload = {
            "output": child_final.get("final_output"),
            "child_thread_id": child_thread_id,
            "child_run_id": child_run_id,
        }
    elif status == "interrupted":
        result_payload = {
            "interrupted": True,
            "pending_approval": child_final.get("pending_approval"),
            "child_thread_id": child_thread_id,
        }
    elif status == "blocked":
        result_payload = {
            "draft": child_final.get("draft_output"),
            "needs_review": True,
            "child_thread_id": child_thread_id,
        }
    else:
        return _error_result(
            proposal, started_at, f"child run {status}: {child_run_id}"
        )

    record = ToolCallRecord(  # type: ignore[typeddict-item]
        tool_call_id=proposal["tool_call_id"],
        tool_name=spec["name"],
        arguments=args,
        decision="allow",
        result=result_payload,
        error=None,
        started_at=started_at,
        finished_at=now_iso(),
    )
    trust = TrustAnnotation(  # type: ignore[typeddict-item]
        trust_level="untrusted",
        source_kind="subagent_result",
        source_id=child_run_id,
        sanitizer="default",
    )
    return ToolDispatchResult(
        outcome="executed",
        record=record,
        decision=None,
        hook_results=[],
        trust=trust,
        error=None,
        error_message=None,
        propagated_denied_actions=new_denied,
        propagated_workspace_refs=child_workspace_refs,
    )


def _allowed(allowed: list[str], target: str | None) -> bool:
    if not target:
        return False
    if not allowed:
        return False
    if "*" in allowed:
        return True
    return target in allowed


def _diff_denied(
    parent: list[DeniedAction], child: list[DeniedAction]
) -> list[DeniedAction]:
    seen = {d["fingerprint"] for d in parent}
    return [d for d in child if d["fingerprint"] not in seen]


def _denied_result(proposal: ToolCallProposal, started_at: str, reason: str) -> ToolDispatchResult:
    record = ToolCallRecord(  # type: ignore[typeddict-item]
        tool_call_id=proposal["tool_call_id"],
        tool_name=proposal["tool_name"],
        arguments=proposal["arguments"],
        decision="deny",
        result=None,
        error=None,
        started_at=started_at,
        finished_at=now_iso(),
    )
    return ToolDispatchResult(
        outcome="denied_retry",
        record=record,
        error_message=reason,
    )


def _error_result(proposal: ToolCallProposal, started_at: str, reason: str) -> ToolDispatchResult:
    record = ToolCallRecord(  # type: ignore[typeddict-item]
        tool_call_id=proposal["tool_call_id"],
        tool_name=proposal["tool_name"],
        arguments=proposal["arguments"],
        decision="deny",
        result=None,
        error={"message": reason},
        started_at=started_at,
        finished_at=now_iso(),
    )
    return ToolDispatchResult(
        outcome="error",
        record=record,
        error=SubagentError(reason),
        error_message=reason,
    )
