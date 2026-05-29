"""Graph node functions for the V0.2 LangGraph main runtime.

The main graph has four nodes plus three conditional edges:

::

    START -> setup -> model_turn -> route_after_model -> execute_tool | validate_output
                          ^                                  |              |
                          |---- route_after_tool ------------+              |
                          |---- route_after_validate -----------------------+

``setup`` runs once on the first invocation (gated by ``step_count == 0``).
``model_turn`` builds the ContextPack, calls the model, and stages the next
action in ``pending_tool_calls`` / ``draft_output``. ``execute_tool`` calls
:func:`langgraph.types.interrupt` when policy requires approval — the same
node resumes after :class:`langgraph.types.Command` ``resume=`` is sent.

Side effects are forbidden inside nodes (LangGraph may replay them). Trace
events are appended to ``state["pending_trace_events"]``; the trace
middleware flushes the queue between transitions.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from .._utils import compute_fingerprint, new_ulid, now_iso
from ..types import (
    AgentProfile,
    DeniedAction,
    LoadedSkill,
    Message,
    PendingApproval,
    ToolCallProposal,
    TraceEvent,
)
from .deps import GraphDeps, deps_from_config
from .state import MainGraphState


def _trace_event(state: MainGraphState, event_type: str, payload: dict[str, Any]) -> TraceEvent:
    return TraceEvent(  # type: ignore[typeddict-item]
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


# ----------------------------------------------------------------------
# nodes
# ----------------------------------------------------------------------


def setup_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """Run once at the start of a run: load agent, load skills, init workspace."""
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    skills = _resolve_skills(deps, profile)
    permission_mode = state["permission_mode"] or (
        (profile["permission_profile"] or {}).get("mode") or "ask"
    )
    workspace_dir = deps.workspace.create_run(state["run_id"])
    event = _trace_event(state, "run_start", {"agent": state["agent_name"], "input": state["task"]})
    return {
        "permission_mode": permission_mode,
        "loaded_skills": [s["name"] for s in skills],
        "pending_trace_events": [event],
        "workspace_refs": [
            {
                "run_id": state["run_id"],
                "kind": "log",
                "path": str(workspace_dir),
                "artifact_id": None,
                "mime_type": None,
                "trust_level": "trusted",
                "size_bytes": 0,
                "metadata": {},
            }
        ],
    }


def model_turn_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """Build context + call model. Stages the next action in state."""
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    skills = _resolve_skills(deps, profile)
    workspace_index = deps.workspace.index_workspace(state["run_id"])
    memory_index = deps.memory.load_index(["user", "agent", "project", "conversation"])
    tool_catalog = {
        name: deps.tools._registry.get(name)
        for name in profile["default_tools"]
        if deps.tools._registry.has(name)
    }
    pack = deps.context.build_context(
        state=state,
        agent=profile,
        skills=skills,
        memory_index=memory_index,
        workspace_index=workspace_index,
        tool_catalog=tool_catalog,
        output_contract=profile["output_contract"],
    )
    context_event = _trace_event(state, "context_built", {"context_hash": pack["context_hash"]})
    call_event = _trace_event(state, "model_call", {"step": state["step_count"] + 1})
    result = deps.model.call(pack)
    result_event = _trace_event(state, "model_result", {"finish_reason": result["finish_reason"]})

    return {
        "step_count": state["step_count"] + 1,
        "messages": [result["message"]],
        "pending_tool_calls": list(result["tool_calls"]),
        "pending_draft": (
            result["message"]["content"] if not result["tool_calls"] else None
        ),
        "pending_trace_events": [context_event, call_event, result_event],
    }


def execute_tool_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    pending = state.get("pending_tool_calls") or []
    if not pending:
        return {}

    proposal = pending[0]
    if proposal.get("malformed"):
        return _handle_malformed(state, deps, proposal)

    from ..subagent import dispatch_subagent

    dispatch = deps.tools.execute_tool_call(
        proposal,
        agent=profile,
        state=state,
        subagent_dispatcher=dispatch_subagent,
        subagent_max_depth=getattr(deps, "subagent_max_depth", 3),
        graph_deps=deps,
    )
    record = dispatch.record
    base_event = _trace_event(
        state,
        "tool_result",
        {
            "tool_call_id": record["tool_call_id"],
            "tool_name": record["tool_name"],
            "decision": record["decision"],
            "outcome": dispatch.outcome,
        },
    )

    if dispatch.outcome == "interrupt" and dispatch.decision is not None:
        approval = PendingApproval(  # type: ignore[typeddict-item]
            approval_id=dispatch.decision.get("approval_id") or new_ulid(),
            tool_call_id=record["tool_call_id"],
            decision=dispatch.decision["decision"],  # type: ignore[arg-type]
            summary=f"{record['tool_name']}({record['arguments']})",
            risk_level=deps.tools._registry.get(record["tool_name"])["risk_level"],
            requested_at=now_iso(),
        )
        approval_event = _trace_event(
            state, "approval_request", {"approval_id": approval["approval_id"]}
        )
        decision_payload = interrupt({
            "approval_id": approval["approval_id"],
            "tool_call_id": approval["tool_call_id"],
            "summary": approval["summary"],
            "risk_level": approval["risk_level"],
            "decision_kind": approval["decision"],
        })
        return _apply_resume_decision(
            state,
            deps,
            profile,
            proposal,
            decision_payload,
            initial_record=record,
            initial_event=base_event,
            approval_event=approval_event,
            approval=approval,
        )

    update: dict[str, Any] = {
        "tool_calls": [record],
        "pending_trace_events": [base_event],
        "pending_tool_calls": [],
    }

    if dispatch.outcome == "denied_retry":
        update["pending_trace_events"].append(
            _trace_event(
                state, "denial", {"reason": "denied_retry", "tool_name": record["tool_name"]}
            )
        )
        update["messages"] = [_tool_msg(record, f"tool {record['tool_name']} denied (previously rejected)")]
        return update

    if dispatch.outcome == "executed":
        update["messages"] = [_tool_msg(record, str(record["result"]))]
        if dispatch.propagated_denied_actions:
            update["denied_actions"] = list(dispatch.propagated_denied_actions)
        if dispatch.propagated_workspace_refs:
            update["workspace_refs"] = list(dispatch.propagated_workspace_refs)
        return update

    err_text = dispatch.error_message or f"tool {record['tool_name']} {dispatch.outcome}"
    update["messages"] = [_tool_msg(record, err_text)]
    return update


def _apply_resume_decision(
    state: MainGraphState,
    deps: GraphDeps,
    profile: AgentProfile,
    proposal: ToolCallProposal,
    payload: dict[str, Any],
    *,
    initial_record: Any,
    initial_event: TraceEvent,
    approval_event: TraceEvent,
    approval: PendingApproval,
) -> dict[str, Any]:
    """Handle the value LangGraph hands us back from ``Command(resume=...)``."""
    decision = (payload or {}).get("decision", "rejected")
    approval_id = approval["approval_id"]

    update: dict[str, Any] = {
        "pending_approval": None,
        "status": "running",
        "tool_calls": [initial_record],
        "pending_trace_events": [initial_event, approval_event],
        "pending_tool_calls": [],
    }

    if decision != "approved":
        reason = (payload or {}).get("reason") or f"decision={decision}"
        denied = DeniedAction(  # type: ignore[typeddict-item]
            fingerprint=compute_fingerprint(
                {"tool": proposal["tool_name"], "args": proposal["arguments"]}
            ),
            tool_name=proposal["tool_name"],
            arguments=proposal["arguments"],
            reason=reason,
            decided_at=now_iso(),
        )
        update["denied_actions"] = [denied]
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "denial",
                {
                    "approval_id": approval_id,
                    "reason": reason,
                    "fingerprint": denied["fingerprint"],
                },
            )
        )
        update["messages"] = [
            _tool_msg(initial_record, f"tool {proposal['tool_name']} rejected: {reason}")
        ]
        return update

    # Approved: re-run with permission_mode elevated to bypass.
    elevated_state = dict(state)
    elevated_state["permission_mode"] = "bypass"
    update["pending_trace_events"].append(
        _trace_event(state, "approval_granted", {"approval_id": approval_id})
    )
    dispatch = deps.tools.execute_tool_call(proposal, agent=profile, state=elevated_state)  # type: ignore[arg-type]
    record = dispatch.record
    update["tool_calls"] = [record]
    update["pending_trace_events"].append(
        _trace_event(
            state,
            "tool_result",
            {
                "tool_call_id": record["tool_call_id"],
                "tool_name": record["tool_name"],
                "decision": record["decision"],
                "outcome": dispatch.outcome,
            },
        )
    )
    if dispatch.outcome == "executed":
        update["messages"] = [_tool_msg(record, str(record["result"]))]
    else:
        err_text = dispatch.error_message or f"tool {record['tool_name']} {dispatch.outcome}"
        update["messages"] = [_tool_msg(record, err_text)]
    return update


def _handle_malformed(
    state: MainGraphState, deps: GraphDeps, proposal: ToolCallProposal
) -> dict[str, Any]:
    repair_used = state["repair_used"] + 1
    over_budget = repair_used > deps.repair_budget
    event = _trace_event(
        state,
        "error",
        {"code": "malformed_tool_call", "tool_name": proposal["tool_name"]},
    )
    update: dict[str, Any] = {
        "repair_used": repair_used,
        "pending_trace_events": [event],
        "pending_tool_calls": [],
        "messages": [
            Message(  # type: ignore[typeddict-item]
                role="tool",
                content=f"malformed call: {proposal.get('parse_error') or 'unparseable arguments'}",
                tool_call_id=proposal.get("tool_call_id") or "",
                metadata={},
            )
        ],
    }
    if over_budget:
        update["status"] = "failed"
        update["pending_trace_events"].append(
            _trace_event(state, "error", {"code": "repair_budget_exhausted"})
        )
        update["pending_trace_events"].append(
            _trace_event(state, "run_end", {"status": "failed"})
        )
    return update


def validate_output_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    draft = state.get("pending_draft")
    if draft is None:
        return {"pending_draft": None}

    contract = profile["output_contract"] or _free_form_contract()
    validation = deps.output.validate(draft, contract, state)
    event = _trace_event(
        state,
        "output_validation",
        {"status": validation["status"], "issues": validation["issues"]},
    )
    update: dict[str, Any] = {
        "draft_output": {"value": draft},
        "pending_trace_events": [event],
        "pending_draft": None,
    }
    if validation["status"] in ("validated", "final"):
        update["final_output"] = validation["output"]
        update["status"] = "completed"
        update["pending_trace_events"].append(
            _trace_event(state, "run_end", {"status": "completed"})
        )
    elif validation["status"] == "needs_review":
        update["status"] = "blocked"
        update["pending_trace_events"].append(
            _trace_event(state, "run_end", {"status": "blocked"})
        )
    else:
        repair_used = state["repair_used"] + 1
        update["repair_used"] = repair_used
        if repair_used > deps.repair_budget:
            update["status"] = "failed"
            update["pending_trace_events"].append(
                _trace_event(state, "error", {"code": "repair_budget_exhausted"})
            )
            update["pending_trace_events"].append(
                _trace_event(state, "run_end", {"status": "failed"})
            )
    return update


# ----------------------------------------------------------------------
# conditional edges
# ----------------------------------------------------------------------


def route_after_model(state: MainGraphState) -> Literal["execute_tool", "validate_output"]:
    if state.get("pending_tool_calls"):
        return "execute_tool"
    return "validate_output"


def route_after_tool(
    state: MainGraphState,
) -> Literal["model_turn", "__end__"]:
    if state["status"] in ("interrupted", "blocked", "completed", "failed"):
        return "__end__"
    if state["step_count"] >= state.get("max_steps", 20):  # type: ignore[arg-type]
        return "__end__"
    return "model_turn"


def route_after_validate(
    state: MainGraphState,
) -> Literal["model_turn", "__end__"]:
    if state["status"] in ("blocked", "completed", "failed"):
        return "__end__"
    if state["step_count"] >= state.get("max_steps", 20):  # type: ignore[arg-type]
        return "__end__"
    return "model_turn"


def route_after_setup(state: MainGraphState) -> Literal["model_turn"]:
    return "model_turn"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _tool_msg(record: dict[str, Any], content: str) -> Message:
    return Message(  # type: ignore[typeddict-item]
        role="tool",
        content=content,
        tool_call_id=record["tool_call_id"],
        metadata={},
    )


def _resolve_skills(deps: GraphDeps, profile: AgentProfile) -> list[LoadedSkill]:
    if not deps.skills or not profile["default_skills"]:
        return []
    return deps.skills.load_skills(profile["default_skills"])


def _free_form_contract() -> dict[str, Any]:
    return {
        "schema": None,
        "required_fields": [],
        "citation_required": False,
        "risk_label_required": False,
        "forbidden_patterns": [],
        "review_required": False,
        "free_form": True,
    }
