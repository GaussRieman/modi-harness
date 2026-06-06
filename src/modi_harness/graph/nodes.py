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

import json
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from .._utils import compute_fingerprint, new_ulid, now_iso
from ..agents import SUBMIT_OUTPUT_TOOL_NAME
from ..types import (
    AgentProfile,
    DeniedAction,
    LoadedSkill,
    MemoryIndex,
    MemoryLevel,
    MemoryRecord,
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
    from ..policy.modes import normalize_mode
    permission_mode = state["permission_mode"] or normalize_mode(
        (profile["permission_profile"] or {}).get("mode") or "auto"
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

    # Determine memory level from agent profile metadata.
    memory_level: MemoryLevel = profile["metadata"].get("memory_level", "moderate")
    scopes = ["user", "agent", "project", "conversation"]
    selected_records = deps.memory.select_for_context(
        task=state["task"],
        agent_name=state["agent_name"],
        scopes=scopes,
        level=memory_level,
    )
    memory_index = _build_memory_index(selected_records)

    tool_catalog = {
        name: deps.tools._registry.get(name)
        for name in profile["default_tools"]
        if deps.tools._registry.has(name)
    }
    # Seed builtin tools so they are offered to every agent regardless of the
    # agent.md `tools:` list. The execution layer already treats builtins as
    # callable by any agent (tools/gateway.py: "builtins bypass agent allowlist
    # by design"), and ContextMan._resolve_visible_tools re-merges them — but
    # only if they are present in this catalog. Without this seeding the model
    # is never told the builtins exist (e.g. save_artifact / save_draft), so it
    # cannot honor a "save your results" instruction. Agent-scoped deny lists in
    # the permission_profile still suppress individual builtins downstream.
    for name in deps.tools._registry.names():
        if name in tool_catalog:
            continue
        spec = deps.tools._registry.get(name)
        if spec.get("kind") == "builtin":
            tool_catalog[name] = spec
    # Synthesize the per-agent submit_output protocol tool when the contract
    # is structured. Schema is the contract's schema verbatim, so the SDK
    # parses model args directly into a validated dict shape and we never
    # have to JSON-decode message.content. See `model_turn_node` below for
    # the interception logic.
    contract_for_protocol = profile["output_contract"]
    if (
        SUBMIT_OUTPUT_TOOL_NAME in profile["default_tools"]
        and not contract_for_protocol["free_form"]
        and contract_for_protocol.get("schema")
    ):
        tool_catalog[SUBMIT_OUTPUT_TOOL_NAME] = {  # type: ignore[assignment]
            "name": SUBMIT_OUTPUT_TOOL_NAME,
            "description": (
                "Submit your final answer as a structured payload. Call this "
                "exactly once with arguments matching the output schema. The "
                "harness validates and returns the payload to the caller; do "
                "not also emit JSON in the assistant message."
            ),
            "input_schema": contract_for_protocol["schema"],
            "output_schema": None,
            "risk_level": "L0",
            "side_effect": False,
            "permission_scope": "",
            "allowed_agents": [],
            "allowed_skills": [],
            "timeout_seconds": 0,
            "retry": None,
            "idempotent": True,
            "dry_run_supported": False,
            "tags": [],
            "kind": "protocol",
            "subagent_target": None,
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
    # Resolve adapter via per-agent cache when available (N2). Otherwise
    # fall back to the deps-level adapter for tests that wire deps manually.
    agent_model_config = profile["metadata"].get("model")
    if deps.model_cache is not None:
        adapter = deps.model_cache.get_or_create(agent_model_config)
    else:
        adapter = deps.model
    result = adapter.call(pack)
    result_event = _trace_event(state, "model_result", {"finish_reason": result["finish_reason"]})

    trace_events = [context_event, call_event, result_event]

    if result.get("fallback_used"):
        fallback_cfg = getattr(adapter, "_fallback_config", None) or {}
        trace_events.append(
            _trace_event(
                state,
                "model_fallback",
                {
                    "fallback_provider": fallback_cfg.get("provider", ""),
                    "fallback_name": fallback_cfg.get("name", ""),
                },
            )
        )

    _filtered_tool_calls, _pending_draft = _split_submit_output(
        list(result["tool_calls"]),
        result["message"]["content"],
    )

    # When submit_output was intercepted, persist its payload to the run's
    # drafts/ directory automatically. The agent contract is "submit_output
    # IS the answer" — making it ALSO be a file on disk gives humans
    # something to read post-run, and keeps a trail of pre-validated
    # attempts for debugging when the answer is later rejected.
    _new_workspace_refs: list[Any] = []
    if isinstance(_pending_draft, dict):
        try:
            ref = deps.workspace.save_draft(
                state["run_id"],
                "output.json",
                _pending_draft,
            )
            _new_workspace_refs.append(ref)
        except Exception:  # pragma: no cover — defensive: never block validation
            pass
        # Auto-render a Markdown view of the same payload to artifacts/. The
        # model can still call save_artifact("briefing.md", ...) with a
        # curated rendering — this default just guarantees humans always
        # have a readable artifact regardless of model discipline.
        try:
            md = _payload_to_markdown(_pending_draft)
            md_ref = deps.workspace.save_artifact(
                state["run_id"],
                "output.md",
                md.encode("utf-8"),
                trust="trusted",
                mime_type="text/markdown",
            )
            _new_workspace_refs.append(md_ref)
        except Exception:  # pragma: no cover — defensive
            pass

    # Carry tool-call metadata inside the assistant message so
    # _message_to_langchain can reconstruct a proper AIMessage with
    # tool_calls. Without this, the stripped Modi Message stores only
    # text content and the downstream langchain_anthropic formatter
    # sees no tool_use blocks, causing "unexpected tool_use_id in
    # tool_result blocks" when the next turn feeds tool results back
    # to the model.
    assistant_msg = dict(result["message"])
    if _filtered_tool_calls:
        assistant_msg["metadata"] = {
            **dict(assistant_msg.get("metadata") or {}),
            "_tool_call_proposals": list(_filtered_tool_calls),
        }

    return {
        "step_count": state["step_count"] + 1,
        "messages": [assistant_msg],
        "pending_tool_calls": _filtered_tool_calls,
        "pending_draft": _pending_draft,
        "pending_trace_events": trace_events,
        "workspace_refs": _new_workspace_refs,
    }


def _split_submit_output(
    tool_calls: list[dict[str, Any]],
    raw_message_content: Any,
) -> tuple[list[dict[str, Any]], Any]:
    """Pull a ``submit_output`` proposal out of the model's tool_calls list.

    Returns ``(remaining_tool_calls, draft)``:

    - If a ``submit_output`` call is present, its already-parsed dict args
      become the draft. Any sibling tool calls in the same turn are
      discarded — submit_output is contractually the model's final action,
      so executing further tools after it would either lose the draft or
      double the work. Models that mix the two will see their other calls
      ignored once and re-issue them on the validation-rejection retry if
      needed.
    - Otherwise the draft falls back to the assistant message content (a
      string) when no tool calls are pending; ``None`` while the model is
      still using tools, so ``validate_output`` only fires on a stop turn.
    """
    submit_idx = next(
        (
            i for i, c in enumerate(tool_calls)
            if c.get("tool_name") == SUBMIT_OUTPUT_TOOL_NAME
        ),
        None,
    )
    if submit_idx is not None:
        submit_call = tool_calls[submit_idx]
        return [], submit_call.get("arguments") or {}
    if not tool_calls:
        return [], raw_message_content
    return list(tool_calls), None


def _payload_to_markdown(payload: dict[str, Any]) -> str:
    """Render a submit_output dict to a default human-readable Markdown view.

    Generic, schema-agnostic. Each top-level key becomes an ``##`` section.
    The agent can still write a curated ``briefing.md`` (or similar) via
    save_artifact — this default exists so artifacts/ is never empty when
    the agent skips the rendering step.
    """
    lines: list[str] = ["# Output", ""]
    for key, value in payload.items():
        lines.append(f"## {key}")
        lines.append("")
        lines.append(_render_md_value(value))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_md_value(value: Any) -> str:
    if isinstance(value, str):
        return value if value else "_(empty)_"
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return str(value)
    if isinstance(value, list):
        if not value:
            return "_(empty)_"
        # List of dicts → bulleted entries with **k**: v · **k2**: v2 form,
        # nested objects/arrays inline as compact JSON so the structure is
        # legible without flattening lossily.
        if all(isinstance(item, dict) for item in value):
            return "\n".join(f"- {_render_md_dict_inline(item)}" for item in value)
        return "\n".join(f"- {_render_md_inline(item)}" for item in value)
    if isinstance(value, dict):
        return "\n".join(
            f"- **{k}**: {_render_md_inline(v)}" for k, v in value.items()
        )
    return str(value)


def _render_md_dict_inline(d: dict[str, Any]) -> str:
    return " · ".join(f"**{k}**: {_render_md_inline(v)}" for k, v in d.items())


def _render_md_inline(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return f"`{json.dumps(value, ensure_ascii=False)}`"
    return str(value)


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
    elif dispatch.outcome == "executed":
        update["messages"] = [_tool_msg(record, str(record["result"]))]
        if dispatch.propagated_denied_actions:
            update["denied_actions"] = list(dispatch.propagated_denied_actions)
        if dispatch.propagated_workspace_refs:
            update["workspace_refs"] = list(dispatch.propagated_workspace_refs)
    else:
        err_text = dispatch.error_message or f"tool {record['tool_name']} {dispatch.outcome}"
        update["messages"] = [_tool_msg(record, err_text)]

    # If the model issued multiple parallel tool_calls, the conversation
    # protocol requires a tool_result for EACH tool_use. We only execute the
    # first one this turn; emit synthetic "deferred" tool_results for the
    # rest so the message history is well-formed and the model can re-issue
    # them sequentially on the next turn.
    if len(pending) > 1:
        for skipped in pending[1:]:
            skipped_record = {
                "tool_call_id": skipped.get("tool_call_id") or "",
                "tool_name": skipped.get("tool_name") or "",
                "arguments": skipped.get("arguments") or {},
                "result": None,
                "decision": "deferred",
            }
            update["messages"].append(
                _tool_msg(
                    skipped_record,
                    "deferred: this build executes one tool per turn; please re-issue this call sequentially on the next turn.",
                )
            )

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
    elevated_state["permission_mode"] = "trust"
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
        else:
            # Surface the validation issues back into the conversation so the
            # next model_turn can repair instead of retrying blind. Use
            # role="user" — multiple non-consecutive system messages break
            # some Anthropic-compatible proxies (GLM gateways).
            update["messages"] = [_repair_message(validation["issues"])]
    return update


def _repair_message(issues: list[Any]) -> Message:
    lines = ["[validation_failed] Your previous output was rejected:"]
    for issue in issues:
        field = issue.get("field")
        prefix = f"- {issue['code']}"
        if field:
            prefix += f" ({field})"
        lines.append(f"{prefix}: {issue['message']}")
        hint = issue.get("hint")
        if hint:
            lines.append(f"  hint: {hint}")
    lines.append(
        "Produce a single JSON object that satisfies the output_contract. "
        "Return it as your final assistant message — no Markdown, no prose."
    )
    return Message(  # type: ignore[typeddict-item]
        role="user",
        content="\n".join(lines),
        tool_call_id=None,
        metadata={"kind": "repair_feedback"},
    )


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


def _build_memory_index(records: list[MemoryRecord]) -> MemoryIndex:
    """Construct a MemoryIndex from a list of selected records."""
    by_scope: dict[str, list[str]] = {}
    by_type: dict[str, list[str]] = {}
    by_tag: dict[str, list[str]] = {}
    for r in records:
        by_scope.setdefault(r["scope"], []).append(r["id"])
        by_type.setdefault(r["type"], []).append(r["id"])
        for tag in r["tags"]:
            by_tag.setdefault(tag, []).append(r["id"])
    return MemoryIndex(records=records, by_scope=by_scope, by_type=by_type, by_tag=by_tag)


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
