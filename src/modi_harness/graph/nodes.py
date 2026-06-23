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
import time
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from .._utils import compute_fingerprint, new_ulid, now_iso
from ..agents import SUBMIT_OUTPUT_TOOL_NAME
from ..memory import MemoryScopeKeys
from ..memory.admission import admit_candidates, annotate_selected
from ..tasks import plan_is_complete
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
from .interaction_protocol import (
    execute_interaction_protocol,
    interaction_protocol_specs,
    is_interaction_protocol_tool,
    validate_user_input_response,
)
from .state import MainGraphState
from .task_protocol import execute_task_protocol, is_task_protocol_tool, task_protocol_specs


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
    """Run once at the start of a run: load agent, load skills, init workspace.

    Also establishes the intent-aligned runtime state before the first model
    turn: clarity (model-estimated when an estimator is wired, deterministically
    floored otherwise) and the derived autonomy scope.
    """
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    skills = _resolve_skills(deps, profile)
    from ..policy.modes import normalize_mode
    permission_mode = state["permission_mode"] or normalize_mode(
        (profile["permission_profile"] or {}).get("mode") or "auto"
    )
    workspace_dir = deps.workspace.create_run(state["run_id"])

    intent, clarity, scope, intent_events = _establish_intent(state, deps, profile)

    event = _trace_event(state, "run_start", {"agent": state["agent_name"], "input": state["task"]})
    return {
        "permission_mode": permission_mode,
        "loaded_skills": [s["name"] for s in skills],
        "human_intent": intent,
        "intent_version": intent["version"],
        "stage_id": intent["current_stage"]["id"],
        "intent_clarity": clarity,
        "autonomy_scope": scope,
        "pending_trace_events": [event, *intent_events],
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


def _establish_intent(
    state: MainGraphState,
    deps: GraphDeps,
    profile: AgentProfile,
) -> tuple[Any, Any, Any, list[TraceEvent]]:
    """Build (or self-heal) the intent field and derive clarity + autonomy scope.

    The adapter seeds ``human_intent`` for top-level runs; subagent runs may
    arrive without one, so we extract it here as a fallback. Returns the intent,
    clarity, scope, and the three lineage trace events.
    """
    from ..autonomy import derive_autonomy_scope
    from ..intent.clarity import estimate_clarity, run_estimator
    from ..intent.extractor import extract_intent

    intent = state.get("human_intent")
    if intent is None:
        intent = extract_intent(state["task"], agent=profile)

    verdict = run_estimator(
        getattr(deps, "clarity_estimator", None), intent, state["task"]
    )
    clarity = estimate_clarity(intent, verdict)
    scope = derive_autonomy_scope(clarity, intent)

    events = [
        _trace_event(
            state,
            "intent_initialized",
            {
                "intent_version": intent["version"],
                "goal": intent["goal"][:200],
                "stage": intent["current_stage"]["kind"],
                "stage_id": intent["current_stage"]["id"],
            },
        ),
        _trace_event(
            state,
            "intent_clarity_estimated",
            {
                "intent_version": intent["version"],
                "level": clarity["level"],
                "confidence": clarity["confidence"],
                "model_verdict": verdict is not None,
                "unknowns": clarity["unknowns"],
            },
        ),
        _trace_event(
            state,
            "autonomy_scope_derived",
            {
                "intent_version": intent["version"],
                "mode": scope["mode"],
                "max_tool_risk_without_judgment": scope["max_tool_risk_without_judgment"],
            },
        ),
    ]
    return intent, clarity, scope, events


def model_turn_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """Build context + call model. Stages the next action in state."""
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    skills = _resolve_skills(deps, profile)
    workspace_index = deps.workspace.index_workspace(state["run_id"])

    # Determine memory level from agent profile metadata.
    memory_level: MemoryLevel = profile["metadata"].get("memory_level", "moderate")
    scopes = ["user", "workspace", "agent", "thread"]
    base_scope_keys = deps.memory_scope_keys or MemoryScopeKeys()
    memory_scope_keys = base_scope_keys.for_run(
        agent_name=state["agent_name"],
        thread_id=state["thread_id"],
    )
    def compute_memory() -> tuple[list[Any], list[MemoryRecord]]:
        recalled, _memory_budget = deps.memory.recall_candidates_for_context(
            task=state["task"],
            agent_name=state["agent_name"],
            scopes=scopes,
            level=memory_level,
            scope_keys=memory_scope_keys,
        )
        selected = []
        used = 0
        for candidate in admit_candidates(recalled):
            record = candidate["record"]
            tokens = max(1, len(record["body"].encode("utf-8")) // 4)
            if used + tokens > _memory_budget:
                continue
            selected.append(annotate_selected(candidate))
            used += tokens
        return recalled, selected

    if deps.recall_cache is None:
        recalled_candidates, selected_records = compute_memory()
    else:
        recalled_candidates, selected_records = deps.recall_cache.get_or_compute(
            state["run_id"],
            compute_memory,
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
    tool_catalog.update(task_protocol_specs(profile))
    tool_catalog.update(interaction_protocol_specs(profile))
    pack = deps.context.build_context(
        state=state,
        agent=profile,
        skills=skills,
        memory_index=memory_index,
        workspace_index=workspace_index,
        tool_catalog=tool_catalog,
        output_contract=profile["output_contract"],
    )
    recall_event = _trace_event(
        state,
        "memory_recall_candidates",
        {
            "source": "harness_memory",
            "level": memory_level,
            "candidates": [
                {
                    "id": c["record"]["id"],
                    "scope": c["record"]["scope"],
                    "type": c["record"]["type"],
                    "score": c["score"],
                    "reasons": c["reasons"],
                    "signals": c["signals"],
                }
                for c in recalled_candidates
            ],
        },
    )
    admission_event = _trace_event(
        state,
        "memory_admission",
        {
            "source": "harness_memory",
            "selected": [
                {
                    "id": r["id"],
                    "authority": (r.get("metadata") or {}).get("authority", "trusted"),
                    "score": (r.get("metadata") or {}).get("selection_score", 0.0),
                    "reasons": (r.get("metadata") or {}).get("selection_reasons", []),
                }
                for r in selected_records
            ],
        },
    )
    memory_event = _trace_event(
        state,
        "memory_selection",
        {
            "source": "harness_memory",
            "level": memory_level,
            "records": [
                {
                    "id": r["id"],
                    "scope": r["scope"],
                    "type": r["type"],
                    "authority": (r.get("metadata") or {}).get("authority", "trusted"),
                    "score": (r.get("metadata") or {}).get("selection_score", 0.0),
                    "reasons": (r.get("metadata") or {}).get("selection_reasons", []),
                }
                for r in selected_records
            ],
        },
    )
    context_metrics = _context_metrics(pack)
    context_event = _trace_event(
        state,
        "context_built",
        {"context_hash": pack["context_hash"], **context_metrics},
    )
    call_event = _trace_event(
        state,
        "model_call",
        {
            "step": state["step_count"] + 1,
            "input_tokens": context_metrics["input_tokens"],
            "token_breakdown": context_metrics["token_breakdown"],
            "context_chars": context_metrics["context_chars"],
            "payload_bytes": context_metrics["payload_bytes"],
            "payload_ref": context_metrics["payload_ref"],
        },
    )
    # Resolve adapter via per-agent cache when available (N2). Otherwise
    # fall back to the deps-level adapter for tests that wire deps manually.
    agent_model_config = profile["metadata"].get("model")
    if deps.model_cache is not None:
        adapter = deps.model_cache.get_or_create(agent_model_config)
    else:
        adapter = deps.model
    started_at = time.perf_counter()
    result = adapter.call(pack)
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    result_event = _trace_event(
        state,
        "model_result",
        {
            "finish_reason": result["finish_reason"],
            "elapsed_ms": elapsed_ms,
            "usage": dict(result["usage"]),
            "output_tokens": (
                result["usage"]["completion_tokens"]
                or _estimate_tokens(
                    {
                        "message": result["message"]["content"],
                        "tool_calls": result["tool_calls"],
                    }
                )
            ),
        },
    )

    trace_events = [recall_event, admission_event, memory_event, context_event, call_event, result_event]

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

    update: dict[str, Any] = {
        "tool_calls": [],
        "pending_trace_events": [],
        "pending_tool_calls": [],
        "messages": [],
    }

    from ..subagent import dispatch_subagent

    for index, proposal in enumerate(pending):
        if proposal.get("malformed"):
            malformed_update = _handle_malformed(state, deps, proposal)
            _merge_tool_update(update, malformed_update)
            if malformed_update.get("status") == "failed":
                return update
            continue

        if is_task_protocol_tool(proposal["tool_name"]):
            protocol_update = execute_task_protocol(state, profile, proposal)
            _merge_tool_update(update, protocol_update)
            if protocol_update.get("pending_interaction") is not None:
                update["messages"].extend(_deferred_tool_messages(pending[index + 1 :]))
                return update
            continue

        if is_interaction_protocol_tool(proposal["tool_name"]):
            interaction_update = execute_interaction_protocol(state, proposal)
            _merge_tool_update(update, interaction_update)
            if interaction_update.get("pending_interaction") is not None:
                update["messages"].extend(_deferred_tool_messages(pending[index + 1 :]))
                return update
            continue

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
        memory_events = _memory_trace_events(state, record)

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
            resumed_update = _apply_resume_decision(
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
            _merge_tool_update(update, resumed_update)
            update["messages"].extend(_deferred_tool_messages(pending[index + 1 :]))
            return update

        update["tool_calls"].append(record)
        update["pending_trace_events"].extend([base_event, *memory_events])

        if dispatch.outcome == "denied_retry":
            update["pending_trace_events"].append(
                _trace_event(
                    state, "denial", {"reason": "denied_retry", "tool_name": record["tool_name"]}
                )
            )
            update["messages"].append(
                _tool_msg(record, f"tool {record['tool_name']} denied (previously rejected)")
            )
        elif dispatch.outcome == "executed":
            update["messages"].append(_tool_msg(record, str(record["result"])))
            if dispatch.propagated_denied_actions:
                update.setdefault("denied_actions", []).extend(dispatch.propagated_denied_actions)
            if dispatch.propagated_workspace_refs:
                update.setdefault("workspace_refs", []).extend(dispatch.propagated_workspace_refs)
            if _memory_write_committed(record) and deps.recall_cache is not None:
                deps.recall_cache.invalidate(state["run_id"])
        else:
            err_text = dispatch.error_message or f"tool {record['tool_name']} {dispatch.outcome}"
            update["messages"].append(_tool_msg(record, err_text))

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
    update["pending_trace_events"].extend(_memory_trace_events(state, record))
    if dispatch.outcome == "executed":
        update["messages"] = [_tool_msg(record, str(record["result"]))]
        if _memory_write_committed(record) and deps.recall_cache is not None:
            deps.recall_cache.invalidate(state["run_id"])
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

    task_config = (profile.get("metadata") or {}).get("task_protocol") or {}
    if task_config.get("mode") == "required" and not plan_is_complete(state.get("task_plan")):
        return {
            "pending_draft": None,
            "messages": [Message(
                role="user",
                content=(
                    "[task_plan_incomplete] Final output cannot be submitted until every "
                    "task is completed through the native task protocol."
                ),
                tool_call_id=None,
                metadata={"kind": "task_protocol_feedback"},
            )],
            "pending_trace_events": [
                _trace_event(state, "task_transition_rejected", {"reason": "task_plan_incomplete"})
            ],
        }

    contract = profile["output_contract"] or _free_form_contract()
    if (
        task_config.get("mode") == "required"
        and plan_is_complete(state.get("task_plan"))
        and not contract.get("free_form", False)
        and not isinstance(draft, dict)
    ):
        issue = {
            "code": "finalization.submit_output_required",
            "severity": "error",
            "field": None,
            "message": "structured finalization must use submit_output",
            "hint": "Call submit_output exactly once with arguments matching the output contract.",
        }
        repair_used = state["repair_used"] + 1
        update: dict[str, Any] = {
            "draft_output": {"value": draft},
            "pending_draft": None,
            "repair_used": repair_used,
            "pending_trace_events": [
                _trace_event(state, "output_validation", {"status": "rejected", "issues": [issue]})
            ],
        }
        if repair_used > deps.repair_budget:
            update["status"] = "failed"
            update["pending_trace_events"].extend([
                _trace_event(state, "error", {"code": "repair_budget_exhausted"}),
                _trace_event(state, "run_end", {"status": "failed"}),
            ])
        else:
            update["messages"] = [Message(  # type: ignore[typeddict-item]
                role="user",
                content=(
                    "[finalization_submit_required] Do not return ordinary assistant text. "
                    "Call submit_output exactly once with arguments matching the output contract."
                ),
                tool_call_id=None,
                metadata={"kind": "finalization_feedback"},
            )]
            update["pending_trace_events"].append(
                _trace_event(
                    state,
                    "output_repair_started",
                    {"repair_attempt": repair_used, "issues": [issue]},
                )
            )
        return update
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
            _trace_event(
                state,
                "output_submitted",
                _output_submitted_payload(state, draft, contract, validation),
            )
        )
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
            update["pending_trace_events"].append(
                _trace_event(
                    state,
                    "output_repair_started",
                    {"repair_attempt": repair_used, "issues": validation["issues"]},
                )
            )
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


def _output_submitted_payload(
    state: MainGraphState,
    draft: str | dict[str, Any],
    contract: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    output = validation.get("output")
    refs = _submitted_output_refs(state)
    payload: dict[str, Any] = {
        "status": validation["status"],
        "source": "submit_output" if isinstance(draft, dict) else "assistant_content",
        "schema_valid": validation["status"] in ("validated", "final"),
        "issues": validation.get("issues") or [],
        "output_hash": compute_fingerprint(output),
        "schema_hash": compute_fingerprint(contract.get("schema") or {}),
        "draft_ref": refs.get("draft_ref"),
        "artifact_ref": refs.get("artifact_ref"),
    }
    if isinstance(output, dict):
        payload["output_keys"] = sorted(str(k) for k in output.keys())
    return payload


def _submitted_output_refs(state: MainGraphState) -> dict[str, str | None]:
    refs: dict[str, str | None] = {"draft_ref": None, "artifact_ref": None}
    for ref in state.get("workspace_refs") or []:
        path = str(ref.get("path") or "")
        kind = ref.get("kind")
        if kind == "draft" and path.endswith("/drafts/output.json"):
            refs["draft_ref"] = path
        elif kind == "artifact" and path.endswith("/artifacts/output.md"):
            refs["artifact_ref"] = path
    return refs


def _memory_trace_events(state: MainGraphState, record: Any) -> list[TraceEvent]:
    tool_name = record.get("tool_name")
    result = record.get("result") or {}
    if tool_name == "recall_memory":
        records = result.get("records") or []
        return [
            _trace_event(
                state,
                "memory_recall_candidates",
                {
                    "source": "agent_recall_memory",
                    "tool_call_id": record.get("tool_call_id"),
                    "count": result.get("count", len(records)),
                    "record_ids": [r.get("id") for r in records if isinstance(r, dict)],
                },
            )
        ]
    if tool_name not in ("propose_memory", "save_memory"):
        return []
    if tool_name == "save_memory" and result.get("id"):
        return [
            _trace_event(
                state,
                "memory_write",
                {
                    "id": result.get("id"),
                    "scope": result.get("scope"),
                    "type": result.get("type"),
                    "tool_name": tool_name,
                },
            )
        ]
    if tool_name == "propose_memory":
        events = [
            _trace_event(
                state,
                "memory_write_proposed",
                {
                    "id": record.get("arguments", {}).get("id"),
                    "scope": record.get("arguments", {}).get("scope"),
                    "type": record.get("arguments", {}).get("type"),
                    "status": result.get("status"),
                },
            )
        ]
        if result.get("status") == "committed":
            events.append(
                _trace_event(
                    state,
                    "memory_write",
                    {
                        "id": result.get("id"),
                        "scope": result.get("scope"),
                        "type": result.get("type"),
                        "tool_name": tool_name,
                    },
                )
            )
        return events
    return []


def _memory_write_committed(record: Any) -> bool:
    tool_name = record.get("tool_name")
    result = record.get("result") or {}
    if tool_name == "save_memory":
        return bool(result.get("id"))
    if tool_name == "propose_memory":
        return result.get("status") == "committed"
    return False


def _deferred_tool_messages(pending_tail: list[ToolCallProposal]) -> list[Message]:
    messages: list[Message] = []
    for skipped in pending_tail:
        skipped_record = {
            "tool_call_id": skipped.get("tool_call_id") or "",
            "tool_name": skipped.get("tool_name") or "",
            "arguments": skipped.get("arguments") or {},
            "result": None,
            "decision": "deferred",
        }
        messages.append(
            _tool_msg(
                skipped_record,
                "deferred: this batch stopped for approval; please re-issue this call sequentially on the next turn.",
            )
        )
    return messages


def _merge_tool_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    list_keys = (
        "messages",
        "tool_calls",
        "denied_actions",
        "workspace_refs",
        "pending_trace_events",
    )
    for key in list_keys:
        if key in source:
            target.setdefault(key, []).extend(source[key])
    for key, value in source.items():
        if key in list_keys:
            continue
        target[key] = value


# ----------------------------------------------------------------------
# conditional edges
# ----------------------------------------------------------------------


def route_after_model(state: MainGraphState) -> Literal["execute_tool", "validate_output"]:
    if state.get("pending_tool_calls"):
        return "execute_tool"
    return "validate_output"


def route_after_tool(
    state: MainGraphState,
) -> Literal["model_turn", "await_interaction", "max_steps_exceeded", "__end__"]:
    if state.get("pending_interaction") is not None:
        return "await_interaction"
    if state["status"] in ("interrupted", "blocked", "completed", "failed", "cancelled"):
        return "__end__"
    if state["step_count"] >= state.get("max_steps", 20):  # type: ignore[arg-type]
        if plan_is_complete(state.get("task_plan")):
            return "model_turn"
        return "max_steps_exceeded"
    return "model_turn"


def route_after_validate(
    state: MainGraphState,
) -> Literal["model_turn", "max_steps_exceeded", "__end__"]:
    if state["status"] in ("blocked", "completed", "failed", "cancelled"):
        return "__end__"
    if state["step_count"] >= state.get("max_steps", 20):  # type: ignore[arg-type]
        if plan_is_complete(state.get("task_plan")):
            return "model_turn"
        return "max_steps_exceeded"
    return "model_turn"


def max_steps_exceeded_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """End an exhausted run with an explicit, inspectable failure."""
    del config
    return {
        "status": "failed",
        "pending_trace_events": [
            _trace_event(state, "error", {"code": "max_steps_exceeded"}),
            _trace_event(state, "run_end", {"status": "failed", "reason": "max_steps_exceeded"}),
        ],
    }


def route_after_setup(state: MainGraphState) -> Literal["model_turn"]:
    return "model_turn"


def await_interaction_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """Pause after checkpointing an interaction, then apply the user's response."""
    del config
    pending = state.get("pending_interaction")
    if pending is None:
        return {}
    payload = interrupt(dict(pending))
    if payload.get("interaction_id") != pending["interaction_id"]:
        raise ValueError("interaction response does not match the pending interaction")
    decision = payload.get("decision")
    if pending["kind"] == "user_input":
        if decision == "cancelled":
            return {
                "pending_interaction": None,
                "status": "cancelled",
                "pending_trace_events": [
                    _trace_event(
                        state,
                        "interaction_resolved",
                        {"interaction_id": pending["interaction_id"], "decision": decision},
                    ),
                    _trace_event(state, "run_end", {"status": "cancelled"}),
                ],
            }
        if decision != "submitted":
            raise ValueError(f"unsupported user-input decision: {decision}")
        value = payload.get("value")
        error = validate_user_input_response(pending, value)
        if error is not None:
            raise ValueError(error)
        effective_value = value
        if value == "" and pending["payload"].get("default") is not None:
            effective_value = pending["payload"]["default"]
        result = {
            "field": pending["payload"].get("field"),
            "value": effective_value,
        }
        field = str(result["field"] or "input")
        human_context = _update_human_context(
            state,
            input_item=(field, effective_value),
        )
        record = {
            "tool_call_id": pending.get("tool_call_id") or "",
            "tool_name": "request_user_input",
        }
        return {
            "pending_interaction": None,
            "status": "running",
            "human_context": human_context,
            "messages": [
                _tool_msg(record, json.dumps(result, ensure_ascii=False)),
                _human_message(
                    interaction_id=pending["interaction_id"],
                    version=human_context["version"],
                    content=_human_input_content(field, effective_value),
                ),
            ],
            "pending_trace_events": [
                _trace_event(
                    state,
                    "interaction_resolved",
                    {
                        "interaction_id": pending["interaction_id"],
                        "decision": decision,
                        "field": result["field"],
                    },
                )
            ],
        }
    record = {
        "tool_call_id": pending.get("tool_call_id") or "",
        "tool_name": "revise_task_plan" if decision == "revise" else "create_task_plan",
    }
    update: dict[str, Any] = {
        "pending_interaction": None,
        "status": "running",
        "pending_trace_events": [
            _trace_event(
                state,
                "interaction_resolved",
                {"interaction_id": pending["interaction_id"], "decision": decision},
            )
        ],
    }
    if decision == "approved":
        human_context = _update_human_context(
            state,
            decision_item={
                "kind": "plan_review",
                "decision": "approved",
                "plan_version": (state.get("pending_task_plan") or {}).get("version"),
            },
        )
        update["task_plan"] = state.get("pending_task_plan")
        update["pending_task_plan"] = None
        update["human_context"] = human_context
        update["messages"] = [
            _tool_msg(record, "task plan approved; begin execution"),
            _human_message(
                interaction_id=pending["interaction_id"],
                version=human_context["version"],
                content="用户已批准当前任务计划, 请按确认后的计划继续执行。",
            ),
        ]
    elif decision == "revise":
        feedback = str(payload.get("feedback") or "Revise the task plan.")
        human_context = _update_human_context(
            state,
            feedback_item={
                "kind": "plan_review",
                "value": feedback,
                "plan_version": (state.get("pending_task_plan") or {}).get("version"),
            },
        )
        update["human_context"] = human_context
        update["messages"] = [
            _tool_msg(record, f"task plan revision requested: {feedback}"),
            _human_message(
                interaction_id=pending["interaction_id"],
                version=human_context["version"],
                content=f"用户对任务计划的修改意见: {feedback}",
            ),
        ]
    elif decision == "cancelled":
        update["pending_task_plan"] = None
        update["status"] = "cancelled"
        update["messages"] = [_tool_msg(record, "task plan cancelled by user")]
        update["pending_trace_events"].append(_trace_event(state, "run_end", {"status": "cancelled"}))
    else:
        raise ValueError(f"unsupported interaction decision: {decision}")
    return update


def route_after_interaction(state: MainGraphState) -> Literal["model_turn", "__end__"]:
    return "__end__" if state["status"] == "cancelled" else "model_turn"


def _update_human_context(
    state: MainGraphState,
    *,
    input_item: tuple[str, Any] | None = None,
    decision_item: dict[str, Any] | None = None,
    feedback_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = state.get("human_context") or {}
    inputs = dict(current.get("inputs") or {})
    decisions = list(current.get("decisions") or [])
    feedback = list(current.get("feedback") or [])
    if input_item is not None:
        inputs[input_item[0]] = input_item[1]
    if decision_item is not None:
        decisions.append(decision_item)
    if feedback_item is not None:
        feedback.append(feedback_item)
    return {
        "version": int(current.get("version", 0)) + 1,
        "inputs": inputs,
        "decisions": decisions[-20:],
        "feedback": feedback[-20:],
    }


def _human_message(*, interaction_id: str, version: int, content: str) -> Message:
    return Message(  # type: ignore[typeddict-item]
        role="user",
        content=content,
        tool_call_id=None,
        metadata={
            "kind": "human_input",
            "interaction_id": interaction_id,
            "human_context_version": version,
        },
    )


def _human_input_content(field: str, value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return f"用户确认的 {field}:\n{rendered}"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _tool_msg(record: dict[str, Any], content: str) -> Message:
    return Message(  # type: ignore[typeddict-item]
        role="tool",
        content=content,
        tool_call_id=record["tool_call_id"],
        metadata={"tool_name": record.get("tool_name")},
    )


def _context_metrics(pack: dict[str, Any]) -> dict[str, Any]:
    memory_tokens = _estimate_tokens(pack.get("memory_blocks") or [])
    reference_tokens = _estimate_tokens(pack.get("references") or [])
    schema_tokens = _estimate_tokens(pack.get("output_requirement") or {})
    tool_tokens = _estimate_tokens(pack.get("tool_descriptions") or [])
    instruction_tokens = _estimate_tokens(
        {
            "system": pack.get("system_instruction") or "",
            "agent": pack.get("agent_instruction") or "",
            "skills": pack.get("skill_instructions") or [],
            "state": pack.get("state_summary") or "",
        }
    )
    message_tokens = 0
    source_tokens = reference_tokens
    for message in pack.get("recent_messages") or []:
        tokens = _estimate_tokens(message.get("content") or "")
        if _is_source_message(message):
            source_tokens += tokens
        else:
            message_tokens += tokens
    workspace_tokens = _estimate_tokens(pack.get("workspace_index") or [])
    token_breakdown = {
        "instruction_tokens": instruction_tokens,
        "source_tokens": source_tokens,
        "memory_tokens": memory_tokens,
        "schema_tokens": schema_tokens,
        "tool_tokens": tool_tokens,
        "message_tokens": message_tokens,
        "workspace_tokens": workspace_tokens,
    }
    payload_data = _stable_json_bytes(pack)
    return {
        "input_tokens": sum(token_breakdown.values()),
        "token_breakdown": token_breakdown,
        "context_chars": len(payload_data.decode("utf-8", errors="replace")),
        "payload_bytes": len(payload_data),
        "payload_ref": pack.get("payload_ref"),
    }


def _is_source_message(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata") or {}
    tool_name = metadata.get("tool_name")
    return tool_name in {"fetch_url", "source_extract", "parallel_fetch_urls"}


def _estimate_tokens(value: Any) -> int:
    data = _stable_json_bytes(value) if not isinstance(value, str) else value.encode("utf-8")
    if not data:
        return 0
    return max(1, len(data) // 4)


def _stable_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


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
