"""Graph node functions for the V0.2 LangGraph main runtime.

The main graph has four nodes plus three conditional edges:

::

    START -> setup -> brain_step -> route_after_brain_step -> execute_tool | validate_output
                          ^                                  |              |
                          |---- route_after_tool ------------+              |
                          |---- route_after_validate -----------------------+

``setup`` runs once on the first invocation (gated by ``step_count == 0``).
``brain_step`` asks Brain for the next StepDecision, then executes the
model-backed slow planning detail or stages the resulting operation. It stores the next
action in ``pending_tool_calls`` / ``draft_output``. ``execute_tool`` calls
:func:`langgraph.types.interrupt` when policy requires approval — the same
node resumes after :class:`langgraph.types.Command` ``resume=`` is sent.

Side effects are forbidden inside nodes (LangGraph may replay them). Trace
events are appended to ``state["pending_trace_events"]``; the trace
middleware flushes the queue between transitions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from .._utils import compute_fingerprint, new_ulid, now_iso
from ..actions.integrity import hash_tool_call
from ..agents import SUBMIT_OUTPUT_TOOL_NAME
from ..brain import default_brain
from ..context.manager import _resolve_visible_tools
from ..loop import (
    AgentLoop,
    initialize_loop_state,
)
from ..loop.types import LoopState, StepContext, StepDecision, StepRecord
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
    PendingInteraction,
    PendingJudgment,
    ToolCallProposal,
    TraceEvent,
)
from .deps import GraphDeps, deps_from_config
from .interaction_protocol import (
    execute_interaction_protocol,
    interaction_protocol_specs,
    is_affirmative_input,
    is_interaction_protocol_tool,
    normalize_choice_input,
    validate_user_input_response,
)
from .state import MainGraphState
from .task_protocol import execute_task_protocol, is_task_protocol_tool, task_protocol_specs

SUBMIT_STEP_DECISION_TOOL_NAME = "submit_step_decision"


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


def _model_step_id(state: MainGraphState) -> str:
    return f"model-{state['step_count'] + 1:04d}"


def _tool_step_id(state: MainGraphState, proposal: ToolCallProposal) -> str:
    raw_id = str(proposal.get("tool_call_id") or new_ulid())
    safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw_id)
    return f"tool-{state['step_count']:04d}-{safe_id}"


def _validation_step_id(state: MainGraphState) -> str:
    return f"validation-{state['step_count']:04d}"


def _output_step_id(state: MainGraphState) -> str:
    return f"output-{state['step_count']:04d}"


def _run_end_step_id(state: MainGraphState) -> str:
    return f"run-end-{state['step_count']:04d}"


def _loop_step_id(state: MainGraphState) -> str:
    loop = state.get("loop_state")
    prefix = str(loop["loop_id"])[-6:] if loop else "pending"
    return f"loop-{prefix}-{state['step_count'] + 1:04d}"


def _ensure_loop_state(
    state: MainGraphState,
    *,
    intent_version: int,
    stage_id: str,
    agent_name: str,
) -> LoopState:
    loop = state.get("loop_state")
    if loop is not None:
        return loop
    return initialize_loop_state(
        run_id=state["run_id"],
        agent_name=agent_name,
        intent_version=intent_version,
        stage_id=stage_id,
        max_auto_steps=int(state.get("max_steps") or 20),
    )


def _build_tool_catalog(
    deps: GraphDeps,
    profile: AgentProfile,
) -> dict[str, Any]:
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
    # parses protocol args directly into a validated dict shape and we never
    # have to JSON-decode message.content. Brain-stage operation adaptation
    # intercepts this protocol before normal tool execution.
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
    return tool_catalog


def _capability_summary(
    *,
    tool_catalog: dict[str, Any],
    skills: list[LoadedSkill],
    profile: AgentProfile,
    deps: GraphDeps,
    state: MainGraphState,
) -> dict[str, Any]:
    visible_tool_names = set(
        _resolve_visible_tools(
            deps.policy,
            profile,
            skills,
            state,
            tool_catalog,
        )
    )
    return {
        "tools": {
            name: {
                "risk_level": spec.get("risk_level"),
                "side_effect": spec.get("side_effect"),
                "kind": spec.get("kind"),
            }
            for name, spec in sorted(tool_catalog.items())
            if name in visible_tool_names
        },
        "skills": [skill["name"] for skill in skills],
        "output_contract": profile["output_contract"],
    }


def _submit_step_decision_tool() -> dict[str, Any]:
    return {
        "name": SUBMIT_STEP_DECISION_TOOL_NAME,
        "description": "Submit the next Brain StepDecision. Call exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "step_kind": {
                    "type": "string",
                    "enum": ["clarify", "plan", "observe", "act", "verify", "handoff", "finish"],
                    "description": (
                        "Use act/verify for consequential runtime operations. "
                        "Use finish only when no ask or operation is needed."
                    ),
                },
                "reason": {"type": "string"},
                "intent_patch": {"type": ["object", "null"]},
                "ask": {
                    "type": ["object", "null"],
                    "properties": {
                        "prompt": {"type": "string"},
                        "reason": {"type": "string"},
                        "allowed_kinds": {"type": "array", "items": {"type": "string"}},
                        "field": {"type": "string"},
                        "input_type": {
                            "type": "string",
                            "enum": ["text", "multiline", "url_list", "confirm"],
                        },
                        "required": {"type": "boolean"},
                        "default": {},
                        "choices": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["prompt", "reason", "allowed_kinds"],
                    "additionalProperties": False,
                },
                "operation": {
                    "type": ["object", "null"],
                    "description": (
                        "Consequential work to run inside this step. Final answers "
                        "must use kind=output_finalize with step_kind=verify, not finish."
                    ),
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["tool", "output_finalize", "stage_transition", "memory_write"],
                        },
                        "summary": {"type": "string"},
                        "target": {"type": "string"},
                        "arguments": {"type": "object"},
                        "expected_outcome": {"type": ["string", "null"]},
                    },
                    "required": ["kind", "summary", "target", "arguments", "expected_outcome"],
                    "additionalProperties": False,
                },
                "expected_state_change": {"type": ["object", "null"]},
                "postcheck": {
                    "type": ["object", "null"],
                    "properties": {
                        "conditions": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "continuation": {
                    "type": "string",
                    "enum": ["continue", "wait", "stop"],
                },
                "human_judgment": {
                    "type": "object",
                    "properties": {
                        "required": {"type": "boolean"},
                        "reason": {"type": "string"},
                        "trigger": {
                            "type": "string",
                            "enum": [
                                "none",
                                "missing_input",
                                "boundary",
                                "stage_gate",
                                "autonomy_scope",
                                "operation_risk",
                                "failure_recovery",
                            ],
                        },
                    },
                    "required": ["required", "reason", "trigger"],
                    "additionalProperties": False,
                },
                "continuation_basis": {
                    "type": ["object", "null"],
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": [
                                "fast_rule",
                                "stage_exit_criteria",
                                "postcheck_result",
                                "autonomy_budget",
                                "slow_plan",
                            ],
                        },
                        "reference": {"type": ["string", "null"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["source", "reference", "reason"],
                    "additionalProperties": False,
                },
            },
            "required": [
                "step_kind",
                "reason",
                "intent_patch",
                "ask",
                "operation",
                "expected_state_change",
                "postcheck",
                "continuation",
                "human_judgment",
                "continuation_basis",
            ],
            "additionalProperties": False,
        },
        "risk_level": "L0",
        "side_effect": False,
        "kind": "protocol",
    }


class ModelStructuredSlowPlanner:
    """Model-backed slow planner with a narrow Brain adapter/normalizer."""

    def __init__(
        self,
        deps: GraphDeps,
        profile: AgentProfile,
        state: MainGraphState,
        skills: list[LoadedSkill],
        tool_catalog: dict[str, Any],
    ) -> None:
        self._deps = deps
        self._profile = profile
        self._state = state
        self._skills = skills
        self._tool_catalog = tool_catalog

    def plan_structured_step(self, context: StepContext) -> StepDecision:
        preflight = _research_question_ask_after_fetch(context)
        if preflight is not None:
            return preflight
        preflight = _research_plan_after_question(context)
        if preflight is not None:
            return preflight
        pack = self._build_pack(context)
        result = self._deps.model.call(pack)
        calls = [
            call
            for call in result["tool_calls"]
            if call.get("tool_name") == SUBMIT_STEP_DECISION_TOOL_NAME
        ]
        if len(calls) == 1:
            args = dict(calls[0].get("arguments") or {})
            return self._decision_from_args(context, args)
        if len(calls) > 1:
            raise ValueError("slow planner must call submit_step_decision at most once")
        return self._normalize_model_result(context, result)

    def _decision_from_args(
        self,
        context: StepContext,
        args: dict[str, Any],
    ) -> StepDecision:
        operation = args.get("operation")
        continuation = args["continuation"]
        continuation_basis = args.get("continuation_basis")
        human_judgment = args["human_judgment"]
        if (
            isinstance(operation, dict)
            and continuation == "wait"
            and args.get("ask") is None
            and isinstance(human_judgment, dict)
            and human_judgment.get("required") is False
        ):
            continuation = "continue"
            if not isinstance(continuation_basis, dict):
                continuation_basis = {
                    "source": "slow_plan",
                    "reference": str(operation.get("target") or operation.get("kind") or ""),
                    "reason": "continue after runtime operation execution",
                }
        return StepDecision(
            id=context["step_id"],
            step_kind=args["step_kind"],
            reasoning_mode="slow",
            reason=args["reason"],
            rule_ref=None,
            intent_patch=args.get("intent_patch"),
            ask=args.get("ask"),
            operation=args.get("operation"),
            expected_state_change=args.get("expected_state_change"),
            postcheck=args.get("postcheck"),
            continuation=continuation,
            human_judgment=args["human_judgment"],
            continuation_basis=continuation_basis,
        )

    def _normalize_model_result(
        self,
        context: StepContext,
        result: dict[str, Any],
    ) -> StepDecision:
        tool_calls = list(result.get("tool_calls") or [])
        business_calls = [
            call
            for call in tool_calls
            if call.get("tool_name") != SUBMIT_STEP_DECISION_TOOL_NAME
        ]
        if len(business_calls) == 1:
            call = business_calls[0]
            tool_name = str(call.get("tool_name") or "").strip()
            if tool_name:
                operation = _runtime_operation_from_model_tool_call(call)
                return self._decision_from_args(
                    context,
                    {
                        "step_kind": (
                            "verify" if operation["kind"] == "output_finalize" else "act"
                        ),
                        "reason": f"slow Brain normalized model-proposed {tool_name}",
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
                            "reason": "normalized operation is inside the current autonomy scope",
                            "trigger": "none",
                        },
                        "continuation_basis": {
                            "source": "slow_plan",
                            "reference": tool_name,
                            "reason": f"continue after {tool_name}",
                        },
                    },
                )
        if business_calls:
            raise ValueError("slow planner produced multiple business tool calls")
        message = result.get("message") or {}
        content = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
        if content:
            return self._decision_from_args(
                context,
                {
                    "step_kind": "verify",
                    "reason": "slow Brain normalized natural language output for finalization",
                    "intent_patch": None,
                    "ask": None,
                    "operation": {
                        "kind": "output_finalize",
                        "summary": "finalize output",
                        "target": "validate_output",
                        "arguments": {"draft": content},
                        "expected_outcome": "output is validated",
                    },
                    "expected_state_change": {"pending_draft": True},
                    "postcheck": None,
                    "continuation": "continue",
                    "human_judgment": {
                        "required": False,
                        "reason": "natural output can be validated by the output controller",
                        "trigger": "none",
                    },
                    "continuation_basis": {
                        "source": "slow_plan",
                        "reference": "output_finalize",
                        "reason": "continue into output validation",
                    },
                },
            )
        raise ValueError("slow planner produced no recoverable decision")

    def _build_pack(self, context: StepContext) -> dict[str, Any]:
        brain_spec = context.get("brain_spec") or {}
        slow_instruction = ""
        if isinstance(brain_spec, dict):
            slow_instruction = str(
                brain_spec.get("slow_instruction")
                or brain_spec.get("slow_prompt")
                or ""
            )
        system = (
            "You are the Agent Brain control layer. Decide exactly one next semantic "
            "StepDecision for the active intent. Do not execute tools. Do not write "
            "state. Call submit_step_decision exactly once. Stage changes must be "
            "runtime operations, never intent_patch fields. Final answers must be "
            "submitted as an output_finalize runtime operation with step_kind=verify. "
            "Do not use step_kind=finish when an ask or operation is present. If "
            "human judgment is needed, do not include an operation."
        )
        if slow_instruction:
            system += "\n\n[brain_slow_instruction]\n" + slow_instruction
        memory_index = self._memory_index()
        pack = self._deps.context.build_context(
            state=self._state,
            agent=self._profile,
            skills=self._skills,
            memory_index=memory_index,
            workspace_index=self._deps.workspace.index_workspace(self._state["run_id"]),
            tool_catalog=self._tool_catalog,
            output_contract=self._profile["output_contract"],
        )
        payload = {"step_context": context}
        pack["system_instruction"] = system + "\n\n" + pack["system_instruction"]
        original_agent_instruction = str(pack.get("agent_instruction") or "").strip()
        brain_control_instruction = (
            "[brain_control]\n"
            "Return the next structured StepDecision. Available runtime capabilities "
            "are listed in the step context; request operations by setting "
            "operation.kind/target/arguments, not by calling those tools directly."
        )
        pack["agent_instruction"] = (
            original_agent_instruction + "\n\n" + brain_control_instruction
            if original_agent_instruction
            else brain_control_instruction
        )
        pack["state_summary"] = (
            pack["state_summary"]
            + "\n\n[brain_step_context]\n"
            + json.dumps(payload, ensure_ascii=False, default=str)
        )
        pack["tool_descriptions"] = [_submit_step_decision_tool()]
        return pack

    def _memory_index(self) -> MemoryIndex:
        memory_level: MemoryLevel = self._profile["metadata"].get("memory_level", "moderate")
        scopes = ["user", "workspace", "agent", "thread"]
        base_scope_keys = self._deps.memory_scope_keys or MemoryScopeKeys()
        memory_scope_keys = base_scope_keys.for_run(
            agent_name=self._state["agent_name"],
            thread_id=self._state["thread_id"],
        )

        def compute_memory() -> tuple[list[Any], list[MemoryRecord]]:
            recalled, memory_budget = self._deps.memory.recall_candidates_for_context(
                task=self._state["task"],
                agent_name=self._state["agent_name"],
                scopes=scopes,
                level=memory_level,
                scope_keys=memory_scope_keys,
            )
            selected = []
            used = 0
            for candidate in admit_candidates(recalled):
                record = candidate["record"]
                tokens = max(1, len(record["body"].encode("utf-8")) // 4)
                if used + tokens > memory_budget:
                    continue
                selected.append(annotate_selected(candidate))
                used += tokens
            return recalled, selected

        if self._deps.recall_cache is None:
            _recalled_candidates, selected_records = compute_memory()
        else:
            _recalled_candidates, selected_records = self._deps.recall_cache.get_or_compute(
                self._state["run_id"],
                compute_memory,
            )
        return _build_memory_index(selected_records)


def _runtime_operation_from_model_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(call.get("tool_name") or "").strip()
    arguments = dict(call.get("arguments") or {})
    if tool_name == SUBMIT_OUTPUT_TOOL_NAME:
        return {
            "kind": "output_finalize",
            "summary": "finalize output",
            "target": "validate_output",
            "arguments": {"draft": arguments},
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
        "arguments": arguments,
        "expected_outcome": f"{tool_name} completes",
    }


def _research_question_ask_after_fetch(context: StepContext) -> StepDecision | None:
    agent_state = context.get("agent_state") or {}
    if agent_state.get("agent_name") != "research-assistant":
        return None
    intent = context.get("intent") or {}
    confirmed = intent.get("confirmed_inputs") or {}
    if not isinstance(confirmed, dict):
        return None
    if confirmed.get("research_question"):
        return None
    source_urls = confirmed.get("source_urls")
    if not source_urls:
        return None
    fetch_records = _recent_successful_fetches(context)
    if not fetch_records:
        return None
    question = _suggest_research_question(fetch_records)
    prompt = _research_question_prompt(fetch_records)
    return StepDecision(
        id=context["step_id"],
        step_kind="clarify",
        reasoning_mode="slow",
        reason="fetch succeeded; confirm a source-grounded research question before planning",
        rule_ref=None,
        intent_patch=None,
        ask={
            "prompt": prompt,
            "reason": "confirmed sources are available but research_question is not confirmed",
            "allowed_kinds": ["clarify", "revise", "cancel"],
            "field": "research_question",
            "input_type": "confirm",
            "required": True,
            "default": question,
        },
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="wait",
        human_judgment={
            "required": False,
            "reason": "the next required input is a user-confirmed research question",
            "trigger": "missing_input",
        },
        continuation_basis=None,
    )


def _research_plan_after_question(context: StepContext) -> StepDecision | None:
    agent_state = context.get("agent_state") or {}
    if agent_state.get("agent_name") != "research-assistant":
        return None
    intent = context.get("intent") or {}
    confirmed = intent.get("confirmed_inputs") or {}
    if not isinstance(confirmed, dict):
        return None
    source_urls = confirmed.get("source_urls")
    research_question = str(confirmed.get("research_question") or "").strip()
    if not source_urls or not research_question:
        return None
    if _has_task_plan(context):
        return None
    fetch_records = _recent_successful_fetches(context)
    if not fetch_records:
        return None
    tasks = _research_plan_tasks(research_question, fetch_records)
    return StepDecision(
        id=context["step_id"],
        step_kind="plan",
        reasoning_mode="slow",
        reason="source URLs and research question are confirmed; create the research task plan",
        rule_ref=None,
        intent_patch=None,
        ask=None,
        operation={
            "kind": "tool",
            "summary": "create research task plan",
            "target": "create_task_plan",
            "arguments": {"tasks": tasks},
            "expected_outcome": "task plan is created for evidence-bound research",
        },
        expected_state_change={"task_plan": True},
        postcheck=None,
        continuation="continue",
        human_judgment={
            "required": False,
            "reason": "confirmed inputs and fetched source coverage are sufficient to create a plan",
            "trigger": "none",
        },
        continuation_basis={
            "source": "slow_plan",
            "reference": "research_assistant.plan_preflight",
            "reason": "continue after creating the research task plan",
        },
    )


def _has_task_plan(context: StepContext) -> bool:
    agent_state = context.get("agent_state") or {}
    metadata = agent_state.get("metadata") if isinstance(agent_state, dict) else None
    if isinstance(metadata, dict) and metadata.get("task_plan_exists") is True:
        return True
    for step in context.get("recent_steps") or []:
        decision = step.get("decision") or {}
        operation = decision.get("operation")
        if isinstance(operation, dict) and operation.get("target") == "create_task_plan":
            return True
    return False


def _research_plan_tasks(
    research_question: str,
    fetch_records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    del research_question, fetch_records
    return [
        {"id": "scope", "title": "界定来源覆盖"},
        {"id": "evidence", "title": "提炼关键证据"},
        {"id": "judgment", "title": "形成取舍判断"},
    ]


def _recent_successful_fetches(context: StepContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in reversed(context.get("recent_steps") or []):
        decision = step.get("decision") or {}
        operation = decision.get("operation")
        if not isinstance(operation, dict) or operation.get("target") != "fetch_url":
            continue
        state_delta = step.get("state_delta") or {}
        if state_delta.get("tool_calls", 0) < 1:
            continue
        for item in state_delta.get("tool_results") or []:
            if isinstance(item, dict) and item.get("tool_name") == "fetch_url":
                out.append(dict(item))
        args = operation.get("arguments") if isinstance(operation, dict) else {}
        url = str((args or {}).get("url") or "").strip()
        if not any(item.get("url") == url for item in out):
            out.append({"url": url, "summary": operation.get("summary") or ""})
    return list(reversed(out))


def _suggest_research_question(fetch_records: list[dict[str, Any]]) -> str:
    urls = [str(item.get("url") or "").strip() for item in fetch_records if item.get("url")]
    if not urls:
        return "这些来源主要说明了什么, 哪些结论较突出且有哪些取舍?"
    if len(urls) == 1:
        label = _fetch_label(fetch_records[0])
        return f"{label} 主要覆盖了什么内容, 较突出的结论和局限是什么?"
    labels = "、".join(_fetch_label(item) for item in fetch_records[:3])
    return f"这些来源中, {labels} 覆盖的内容有哪些差异, 如何取舍?"


def _research_question_prompt(fetch_records: list[dict[str, Any]]) -> str:
    urls = [str(item.get("url") or "").strip() for item in fetch_records if item.get("url")]
    if len(urls) == 1:
        coverage = f"已成功获取 {_fetch_label(fetch_records[0])}, 可以围绕该来源覆盖的内容形成问题。"
    else:
        coverage = f"已成功获取 {len(urls)} 个来源, 可以围绕这些来源共同覆盖的内容形成问题。"
    return coverage + "请确认研究问题, 或直接输入修改后的完整问题。"


def _source_label(url: str) -> str:
    if not url:
        return "该来源"
    stripped = url.removeprefix("https://").removeprefix("http://")
    return stripped.split("/", 1)[0] or "该来源"


def _fetch_label(record: dict[str, Any]) -> str:
    title = str(record.get("title") or "").strip()
    if title:
        return title[:60]
    return _source_label(str(record.get("url") or ""))


def _brain_only_step_update(
    state: MainGraphState,
    *,
    agent_loop: AgentLoop,
    step_record: StepRecord,
    step_planned_event: TraceEvent,
) -> dict[str, Any]:
    completed = agent_loop.complete_step(step_record)
    completed_step = completed["record"]
    continuation = completed["continuation"]
    next_loop = completed["loop"]
    trace_events = [
        step_planned_event,
        _trace_event(state, "step_completed", _step_trace_payload(completed_step)),
        _trace_event(
            state,
            "loop_continuation_decision",
            {
                "loop_id": next_loop["loop_id"],
                "step_id": completed_step["step_id"],
                "outcome": continuation["outcome"],
                "requested": continuation["requested"],
                "basis": continuation["basis"],
                "blockers": continuation["blockers"],
                "reason": continuation["reason"],
            },
        ),
    ]

    update: dict[str, Any] = {
        "loop_state": next_loop,
        "step_records": [completed_step],
        "current_step": None,
        "last_continuation_decision": continuation,
        "step_count": state["step_count"] + 1,
        "pending_tool_calls": [],
        "pending_draft": None,
        "pending_trace_events": trace_events,
    }

    ask = completed_step["decision"]["ask"]
    human = completed_step["decision"]["human_judgment"]
    if ask is not None and not human["required"]:
        input_type = ask.get("input_type") or "multiline"
        field = ask.get("field") or "clarification"
        interaction = PendingInteraction(
            interaction_id=new_ulid(),
            kind="user_input",
            prompt=ask["prompt"],
            payload={
                "input_type": input_type,
                "required": ask.get("required", True),
                "field": field,
                "allowed_kinds": ask["allowed_kinds"],
                "reason": ask["reason"],
                "step_id": completed_step["step_id"],
                "rule_ref": completed_step["decision"]["rule_ref"],
            },
            tool_call_id=None,
        )
        if "default" in ask:
            interaction["payload"]["default"] = ask.get("default")
        if "choices" in ask:
            interaction["payload"]["choices"] = ask.get("choices") or []
        update["pending_interaction"] = interaction
        update["pending_trace_events"].append(
            _trace_event(state, "interaction_requested", dict(interaction))
        )

    if human["required"]:
        judgment = _judgment_from_brain_step(state, completed_step)
        update["pending_judgment"] = judgment
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "judgment_requested",
                {
                    "judgment_id": judgment["judgment_id"],
                    "target_stage_id": judgment["target_stage_id"],
                    "allowed_kinds": judgment["allowed_kinds"],
                    "risk_level": judgment["risk_level"],
                    "intent_version": state.get("intent_version"),
                    "step_id": completed_step["step_id"],
                    "trigger": human["trigger"],
                },
            )
        )

    if continuation["outcome"] in ("wait_for_user", "wait_for_judgment"):
        update["status"] = "interrupted"
    elif continuation["outcome"] == "complete":
        update["status"] = "completed"
    elif continuation["outcome"] == "fail":
        update["status"] = "failed"
    elif continuation["outcome"] == "cancel":
        update["status"] = "cancelled"

    return update


def _judgment_from_brain_step(
    state: MainGraphState,
    step: StepRecord,
) -> PendingJudgment:
    decision = step["decision"]
    ask = decision["ask"]
    human = decision["human_judgment"]
    judgment_id = new_ulid()
    return PendingJudgment(
        judgment_id=judgment_id,
        approval_id=judgment_id,
        tool_call_id=None,
        target_action_id=None,
        target_stage_id=state.get("stage_id"),
        reviewed_action_hash=None,
        prompt=ask["prompt"] if ask is not None else human["reason"],
        allowed_kinds=(
            list(ask["allowed_kinds"])
            if ask is not None
            else ["approve", "reject", "revise", "redirect", "constrain", "clarify", "cancel"]
        ),
        proposed_intent_patch=decision["intent_patch"],
        summary=decision["reason"],
        rationale=None,
        risk_level="L0",
        trigger=human["trigger"],
        requested_at=now_iso(),
    )


def _persist_output_draft(
    state: MainGraphState,
    deps: GraphDeps,
    draft: Any,
) -> list[Any]:
    if not isinstance(draft, dict):
        return []
    refs: list[Any] = []
    try:
        ref = deps.workspace.save_draft(
            state["run_id"],
            "output.json",
            draft,
        )
        refs.append(ref)
    except Exception:  # pragma: no cover — defensive: never block validation
        pass
    try:
        md = _payload_to_markdown(draft)
        md_ref = deps.workspace.save_artifact(
            state["run_id"],
            "output.md",
            md.encode("utf-8"),
            trust="trusted",
            mime_type="text/markdown",
        )
        refs.append(md_ref)
    except Exception:  # pragma: no cover — defensive
        pass
    return refs


def _runtime_output_finalize_update(
    state: MainGraphState,
    deps: GraphDeps,
    *,
    agent_loop: AgentLoop,
    step_record: StepRecord,
    step_planned_event: TraceEvent,
) -> dict[str, Any]:
    operation = step_record["decision"]["operation"]
    if operation is None:
        return _unsupported_operation_step_update(
            state,
            agent_loop=agent_loop,
            step_record=step_record,
            step_planned_event=step_planned_event,
        )
    arguments = dict(operation["arguments"])
    draft = arguments.get("draft", arguments.get("output", arguments.get("value", arguments)))
    completed = agent_loop.complete_step(
        step_record,
        state_delta={
            "pending_draft": True,
            "output_finalize": {"source": "runtime_operation"},
        },
    )
    completed_step = completed["record"]
    continuation = completed["continuation"]
    next_loop = completed["loop"]
    trace_events = [
        step_planned_event,
        _trace_event(
            state,
            "runtime_operation_staged",
                {
                    "loop_id": next_loop["loop_id"],
                    "step_id": completed_step["step_id"],
                    "operation": _runtime_operation_trace_payload(operation),
                    "target": "validate_output",
                },
            ),
        _trace_event(state, "step_completed", _step_trace_payload(completed_step)),
        _trace_event(
            state,
            "loop_continuation_decision",
            {
                "loop_id": next_loop["loop_id"],
                "step_id": completed_step["step_id"],
                "outcome": continuation["outcome"],
                "requested": continuation["requested"],
                "basis": continuation["basis"],
                "blockers": continuation["blockers"],
                "reason": continuation["reason"],
            },
        ),
    ]
    return {
        "loop_state": next_loop,
        "step_records": [completed_step],
        "current_step": None,
        "last_continuation_decision": continuation,
        "step_count": state["step_count"] + 1,
        "pending_tool_calls": [],
        "pending_draft": draft,
        "workspace_refs": _persist_output_draft(state, deps, draft),
        "pending_trace_events": trace_events,
    }


def _unsupported_operation_step_update(
    state: MainGraphState,
    *,
    agent_loop: AgentLoop,
    step_record: StepRecord,
    step_planned_event: TraceEvent,
) -> dict[str, Any]:
    completed = agent_loop.fail_unsupported_operation(step_record)
    completed_step = completed["record"]
    continuation = completed["continuation"]
    next_loop = completed["loop"]
    operation = completed_step["decision"]["operation"]
    return {
        "loop_state": next_loop,
        "step_records": [completed_step],
        "current_step": None,
        "last_continuation_decision": continuation,
        "step_count": state["step_count"] + 1,
        "status": "failed",
        "pending_tool_calls": [],
        "pending_draft": None,
        "pending_trace_events": [
            step_planned_event,
            _trace_event(
                state,
                "error",
                {
                    "code": "runtime_operation_not_wired",
                    "operation": _runtime_operation_trace_payload(operation),
                    "step_id": completed_step["step_id"],
                },
            ),
            _trace_event(state, "step_completed", _step_trace_payload(completed_step)),
            _trace_event(
                state,
                "loop_continuation_decision",
                {
                    "loop_id": next_loop["loop_id"],
                    "step_id": completed_step["step_id"],
                    "outcome": continuation["outcome"],
                    "requested": continuation["requested"],
                    "basis": continuation["basis"],
                    "blockers": continuation["blockers"],
                    "reason": continuation["reason"],
                },
            ),
        ],
    }


def _runtime_operation_tool_call(step: StepRecord) -> ToolCallProposal | None:
    operation = step["decision"]["operation"]
    if operation is None:
        return None
    if operation["kind"] == "output_finalize":
        return None
    target = _runtime_operation_tool_name(operation)
    if target is None:
        return None
    tool_call_id = f"runtime-op-{step['step_id']}"
    arguments = dict(operation["arguments"])
    return ToolCallProposal(  # type: ignore[typeddict-item]
        tool_call_id=tool_call_id,
        tool_name=target,
        arguments=arguments,
        malformed=False,
        parse_error=None,
        metadata={
            "parent_step_id": step["step_id"],
            "runtime_operation": True,
            "runtime_operation_kind": operation["kind"],
            "expected_outcome": operation["expected_outcome"],
            "reviewed_action_hash": hash_tool_call({
                "tool_call_id": tool_call_id,
                "tool_name": target,
                "arguments": arguments,
                "malformed": False,
                "parse_error": None,
            }),
        },
    )


def _runtime_operation_tool_name(operation: dict[str, Any]) -> str | None:
    kind = operation["kind"]
    target = str(operation.get("target") or "").strip()
    if kind == "stage_transition":
        if target in ("", "stage_transition", "transition_stage"):
            return "transition_stage"
        return None
    if kind == "memory_write":
        if target in ("save_memory", "propose_memory"):
            return target
        if target not in ("", "memory_write", "write_memory"):
            return None
        scope = str((operation.get("arguments") or {}).get("scope") or "")
        return "propose_memory" if scope in ("user", "workspace") else "save_memory"
    if kind == "tool":
        return target or None
    return None


def _runtime_operation_trace_payload(operation: dict[str, Any] | None) -> dict[str, Any] | None:
    if operation is None:
        return None
    arguments = operation.get("arguments") or {}
    argument_keys = sorted(str(key) for key in arguments.keys()) if isinstance(arguments, dict) else []
    summary = str(operation.get("summary") or "")
    expected_outcome = operation.get("expected_outcome")
    return {
        "kind": operation.get("kind"),
        "target": operation.get("target"),
        "argument_keys": argument_keys[:40],
        "argument_count": len(argument_keys),
        "summary_hash": compute_fingerprint(summary) if summary else None,
        "expected_outcome": expected_outcome if isinstance(expected_outcome, str) else None,
    }


def _runtime_operation_tool_update(
    state: MainGraphState,
    *,
    loop: LoopState,
    step_record: StepRecord,
    step_planned_event: TraceEvent,
    proposal: ToolCallProposal,
) -> dict[str, Any]:
    running_step = StepRecord(**step_record)
    running_step["status"] = "running"
    running_step["operation_ref"] = proposal["tool_call_id"]
    next_loop = LoopState(**loop)
    next_loop["pending_step_id"] = running_step["step_id"]
    return {
        "loop_state": next_loop,
        "current_step": running_step,
        "last_continuation_decision": None,
        "step_count": state["step_count"] + 1,
        "pending_tool_calls": [proposal],
        "pending_draft": None,
        "pending_trace_events": [
            step_planned_event,
            _trace_event(
                state,
                "runtime_operation_staged",
                {
                    "loop_id": loop["loop_id"],
                    "step_id": running_step["step_id"],
                    "operation": _runtime_operation_trace_payload(
                        running_step["decision"]["operation"]
                    ),
                    "tool_call_id": proposal["tool_call_id"],
                    "tool_name": proposal["tool_name"],
                },
            ),
        ],
    }


def _step_trace_payload(step: StepRecord) -> dict[str, Any]:
    decision = step["decision"]
    return {
        "loop_id": step["loop_id"],
        "step_id": step["step_id"],
        "step_type": "brain_step",
        "step_index": step["index"],
        "step_kind": step["step_kind"],
        "status": step["status"],
        "reasoning_mode": decision["reasoning_mode"],
        "rule_ref": decision["rule_ref"],
        "reason": decision["reason"],
        "intent_version": step["intent_version"],
        "stage_id": step["stage_id"],
        "human_judgment_required": decision["human_judgment"]["required"],
        "continuation": decision["continuation"],
    }


def _lineage_events(
    state: MainGraphState,
    dispatch: Any,
    *,
    judgment_id: str | None = None,
) -> list[TraceEvent]:
    """Emit the action_proposed / alignment_decision / intent_lineage_recorded
    trio for an action that flowed through the alignment path.

    Returns ``[]`` for dispatches that did not enter the alignment path, such
    as dry-run projections or failed operation staging. Those carry no
    ``alignment_decision`` to prove against. The
    ``intent_lineage_recorded`` event is the compact join a maintainer reads to
    answer "which intent version and stage produced this action, and what
    decided it?".
    """
    from ..trace.lineage import build_lineage

    decision = getattr(dispatch, "alignment_decision", None)
    action = getattr(dispatch, "action_proposal", None)
    if decision is None or action is None:
        return []

    lineage = build_lineage(
        proposal=action,
        decision=decision,
        judgment={"id": judgment_id} if judgment_id else None,
    )
    return [
        _trace_event(
            state,
            "action_proposed",
            {
                "action_id": action["id"],
                "kind": action["kind"],
                "tool_name": action["tool_name"],
                "summary": action["summary"],
                "intent_version": action["intent_version"],
                "stage_id": action["stage_id"],
                "parent_step_id": action.get("parent_step_id"),
            },
        ),
        _trace_event(
            state,
            "alignment_decision",
            {
                "alignment_decision_id": decision["id"],
                "action_id": decision["action_id"],
                "decision": decision["decision"],
                "reason": decision["reason"],
                "intent_version": decision["intent_version"],
                "stage_id": decision["stage_id"],
                "parent_step_id": action.get("parent_step_id"),
                "boundary_hits": decision["boundary_hits"],
                "model_judged": decision["model_judged"],
            },
        ),
        _trace_event(state, "intent_lineage_recorded", dict(lineage)),
    ]


def _stage_advance_update(
    state: MainGraphState, deps: GraphDeps, dispatch: Any
) -> dict[str, Any]:
    """When an *allowed* ``stage_transition`` executed, advance the live stage.

    The ``transition_stage`` builtin returns the resolved target ``IntentStage``
    in its result; alignment has already permitted the move (a transition that
    needed judgment would have interrupted instead of executing). Advancing the
    stage here — not in the tool handler — keeps the handler pure and the state
    write in the one place that owns ``human_intent``. The intent *version* is
    not bumped: a stage move inside the same intent is not an intent edit, so
    only ``current_stage`` / ``stage_id`` change. Autonomy is re-derived because
    ``allowed_stages`` can differ once the stage changes.
    """
    action = getattr(dispatch, "action_proposal", None)
    record = dispatch.record
    if action is None or action.get("kind") != "stage_transition":
        return {}
    result = record.get("result")
    if not isinstance(result, dict):
        return {}
    new_stage = result.get("stage")
    if not isinstance(new_stage, dict):
        return {}

    intent = state.get("human_intent")
    if intent is None:
        return {}

    import copy

    from ..autonomy import derive_autonomy_scope
    from ..intent.types import IntentStage

    new_intent = copy.deepcopy(intent)
    new_intent["current_stage"] = cast("IntentStage", copy.deepcopy(new_stage))
    update: dict[str, Any] = {
        "human_intent": new_intent,
        "stage_id": new_stage["id"],
    }
    clarity = state.get("intent_clarity")
    if clarity is not None:
        update["autonomy_scope"] = derive_autonomy_scope(clarity, new_intent)
    return update


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
    loop = _ensure_loop_state(
        state,
        intent_version=intent["version"],
        stage_id=intent["current_stage"]["id"],
        agent_name=state["agent_name"],
    )

    event = _trace_event(state, "run_start", {"agent": state["agent_name"], "input": state["task"]})
    loop_event = _trace_event(
        state,
        "loop_initialized",
        {
            "loop_id": loop["loop_id"],
            "status": loop["status"],
            "intent_version": loop["intent_version"],
            "stage_id": loop["stage_id"],
            "max_auto_steps": loop["max_auto_steps"],
        },
    )
    return {
        "permission_mode": permission_mode,
        "loaded_skills": [s["name"] for s in skills],
        "human_intent": intent,
        "intent_version": intent["version"],
        "stage_id": intent["current_stage"]["id"],
        "intent_clarity": clarity,
        "autonomy_scope": scope,
        "loop_state": loop,
        "current_step": None,
        "last_continuation_decision": None,
        "pending_trace_events": [event, *intent_events, loop_event],
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


def brain_step_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """Run one Brain-owned semantic step and stage the next action in state."""
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    skills = _resolve_skills(deps, profile)
    tool_catalog = _build_tool_catalog(deps, profile)
    intent = state.get("human_intent")
    loop = _ensure_loop_state(
        state,
        intent_version=state.get("intent_version", (intent or {}).get("version", 1)),
        stage_id=state.get("stage_id", (intent or {}).get("current_stage", {}).get("id", "")),
        agent_name=state["agent_name"],
    )
    loop_step_id = _loop_step_id(state)
    brain = deps.brain or default_brain(
        planner=ModelStructuredSlowPlanner(
            deps,
            profile,
            state,
            skills,
            tool_catalog,
        )
    )
    agent_loop = AgentLoop(state=loop, brain=brain)
    prepared_step = agent_loop.prepare_step(
        step_id=loop_step_id,
        event={
            "kind": "brain_step",
            "message_count": len(state.get("messages", [])),
            "task": state["task"],
            "task_plan_exists": state.get("task_plan") is not None,
        },
        intent=intent,
        intent_clarity=state.get("intent_clarity"),
        autonomy_scope=state.get("autonomy_scope"),
        agent_profile=profile,
        recent_steps=list(state.get("step_records", []))[-5:],
        available_capabilities=_capability_summary(
            tool_catalog=tool_catalog,
            skills=skills,
            profile=profile,
            deps=deps,
            state=state,
        ),
        brain_spec=profile["metadata"].get("brain"),
    )
    step_decision = prepared_step["decision"]
    step_record = prepared_step["record"]
    step_planned_event = _trace_event(
        state,
        "step_planned",
        _step_trace_payload(step_record),
    )
    if step_decision["operation"] is not None:
        if step_decision["operation"]["kind"] == "output_finalize":
            return _runtime_output_finalize_update(
                state,
                deps,
                agent_loop=agent_loop,
                step_record=step_record,
                step_planned_event=step_planned_event,
            )
        operation_proposal = _runtime_operation_tool_call(step_record)
        if operation_proposal is not None:
            return _runtime_operation_tool_update(
                state,
                loop=loop,
                step_record=step_record,
                step_planned_event=step_planned_event,
                proposal=operation_proposal,
            )
        return _unsupported_operation_step_update(
            state,
            agent_loop=agent_loop,
            step_record=step_record,
            step_planned_event=step_planned_event,
        )
    if (
        step_decision["ask"] is not None
        or step_decision["continuation"] in ("wait", "stop")
    ):
        return _brain_only_step_update(
            state,
            agent_loop=agent_loop,
            step_record=step_record,
            step_planned_event=step_planned_event,
        )
    return _brain_only_step_update(
        state,
        agent_loop=agent_loop,
        step_record=step_record,
        step_planned_event=step_planned_event,
    )


def _reviewed_action_hash(proposal: ToolCallProposal) -> str:
    metadata = proposal.get("metadata") if isinstance(proposal, dict) else None
    if isinstance(metadata, dict):
        value = metadata.get("reviewed_action_hash")
        if isinstance(value, str) and value:
            return value
    return hash_tool_call(cast(dict[str, Any], proposal))


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
            _tool_result_trace_payload(record, dispatch, state=state, proposal=proposal),
        )
        memory_events = _memory_trace_events(state, record)
        lineage_events = _lineage_events(state, dispatch)

        if dispatch.outcome == "interrupt" and dispatch.decision is not None:
            approval_id = dispatch.decision.get("approval_id") or new_ulid()
            decision_kind = dispatch.decision["decision"]
            risk_level = deps.tools._registry.get(record["tool_name"])["risk_level"]
            summary = f"{record['tool_name']}({record['arguments']})"
            # Judgment is the primary human-interaction primitive; approval is one
            # of its allowed kinds. ``PendingApproval`` is kept as a derived bridge
            # so existing approve/reject callers keep working.
            judgment = PendingJudgment(
                judgment_id=approval_id,
                approval_id=approval_id,
                tool_call_id=record["tool_call_id"],
                target_action_id=dispatch.action_id,
                target_stage_id=state.get("stage_id"),
                reviewed_action_hash=_reviewed_action_hash(proposal),
                prompt=f"Judge action: {summary}",
                allowed_kinds=["approve", "reject", "revise", "redirect", "constrain"],
                proposed_intent_patch=None,
                summary=summary,
                rationale=None,
                risk_level=risk_level,
                trigger="operation_risk",
                requested_at=now_iso(),
            )
            approval = PendingApproval(
                approval_id=approval_id,
                tool_call_id=record["tool_call_id"],
                decision=decision_kind,  # type: ignore[typeddict-item]
                summary=summary,
                risk_level=risk_level,
                requested_at=judgment["requested_at"],
            )
            approval_event = _trace_event(
                state, "approval_request", {"approval_id": approval_id}
            )
            judgment_requested_event = _trace_event(
                state,
                "judgment_requested",
                {
                    "judgment_id": judgment["judgment_id"],
                    "target_action_id": judgment["target_action_id"],
                    "target_stage_id": judgment["target_stage_id"],
                    "allowed_kinds": judgment["allowed_kinds"],
                    "risk_level": risk_level,
                    "intent_version": state.get("intent_version"),
                },
            )
            decision_payload = interrupt({
                "judgment_id": judgment["judgment_id"],
                "approval_id": approval_id,
                "tool_call_id": approval["tool_call_id"],
                "target_action_id": judgment["target_action_id"],
                "reviewed_action_hash": judgment["reviewed_action_hash"],
                "summary": approval["summary"],
                "risk_level": approval["risk_level"],
                "decision_kind": approval["decision"],
                "allowed_kinds": judgment["allowed_kinds"],
                "prompt": judgment["prompt"],
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
                reviewed_action_hash=judgment["reviewed_action_hash"],
                lineage_events=[*lineage_events, judgment_requested_event],
            )
            _merge_tool_update(update, resumed_update)
            update["messages"].extend(_deferred_tool_messages(pending[index + 1 :]))
            return update

        update["tool_calls"].append(record)
        update["pending_trace_events"].extend([base_event, *memory_events, *lineage_events])

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
            pending_interaction = _interaction_from_tool_result(state, record)
            update["messages"].append(_tool_msg(record, str(record["result"])))
            if dispatch.propagated_denied_actions:
                update.setdefault("denied_actions", []).extend(dispatch.propagated_denied_actions)
            if dispatch.propagated_workspace_refs:
                update.setdefault("workspace_refs", []).extend(dispatch.propagated_workspace_refs)
            if _memory_write_committed(record) and deps.recall_cache is not None:
                deps.recall_cache.invalidate(state["run_id"])
            # An allowed stage_transition advances the live stage in state.
            stage_update = _stage_advance_update(state, deps, dispatch)
            if stage_update:
                _merge_tool_update(update, stage_update)
            if pending_interaction is not None:
                update["pending_interaction"] = pending_interaction
                update["pending_trace_events"].append(
                    _trace_event(state, "interaction_requested", dict(pending_interaction))
                )
                update["messages"].extend(_deferred_tool_messages(pending[index + 1 :]))
                return update
        else:
            err_text = dispatch.error_message or f"tool {record['tool_name']} {dispatch.outcome}"
            update["messages"].append(_tool_msg(record, err_text))

    return _complete_current_runtime_operation_step(state, deps, update)


def _complete_current_runtime_operation_step(
    state: MainGraphState,
    deps: GraphDeps,
    update: dict[str, Any],
) -> dict[str, Any]:
    current = state.get("current_step")
    if current is None or current["decision"]["operation"] is None:
        return update
    if update.get("pending_interaction") is not None:
        return update
    if update.get("pending_judgment") is not None or update.get("pending_approval") is not None:
        return update
    tool_calls = update.get("tool_calls") or []
    if not tool_calls:
        return update

    failed = any(record.get("error") for record in tool_calls)
    agent_loop = AgentLoop(
        state=state["loop_state"],
        brain=deps.brain
        or default_brain(
            planner=ModelStructuredSlowPlanner(
                deps,
                deps.agents.load_agent(state["agent_name"]),
                state,
                [],
                {},
            )
        ),
    )
    completed = agent_loop.complete_step(
        current,
        status="failed" if failed else "completed",
        state_delta={
            "tool_calls": len(tool_calls),
            "tool_results": _step_tool_result_summaries(tool_calls),
            "stage_id": update.get("stage_id", state.get("stage_id")),
        },
    )
    completed_step = completed["record"]
    continuation = completed["continuation"]
    next_loop = completed["loop"]
    if update.get("stage_id") is not None:
        next_loop["stage_id"] = update["stage_id"]
    if update.get("intent_version") is not None:
        next_loop["intent_version"] = update["intent_version"]

    update["loop_state"] = next_loop
    update["step_records"] = [completed_step]
    update["current_step"] = None
    update["last_continuation_decision"] = continuation
    update.setdefault("pending_trace_events", []).extend([
        _trace_event(state, "step_completed", _step_trace_payload(completed_step)),
        _trace_event(
            state,
            "loop_continuation_decision",
            {
                "loop_id": next_loop["loop_id"],
                "step_id": completed_step["step_id"],
                "outcome": continuation["outcome"],
                "requested": continuation["requested"],
                "basis": continuation["basis"],
                "blockers": continuation["blockers"],
                "reason": continuation["reason"],
            },
        ),
    ])
    return update


def _interaction_from_tool_result(
    state: MainGraphState, record: dict[str, Any]
) -> PendingInteraction | None:
    result = record.get("result")
    if not isinstance(result, dict):
        return None
    raw = result.pop("_modi_pending_interaction", None)
    if not isinstance(raw, dict):
        return None
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        return None
    input_type = str(raw.get("input_type") or "confirm")
    payload = {
        "input_type": input_type,
        "required": bool(raw.get("required", True)),
        "field": raw.get("field"),
        "default": raw.get("default"),
        "choices": raw.get("choices") or [],
    }
    for key, value in raw.items():
        if key not in {"prompt", "input_type", "required", "field", "default", "choices"}:
            payload[key] = value
    return PendingInteraction(
        interaction_id=new_ulid(),
        kind="user_input",
        prompt=prompt,
        payload=payload,
        tool_call_id=None,
    )


def _step_tool_result_summaries(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for record in tool_calls:
        tool_name = str(record.get("tool_name") or "")
        result = record.get("result")
        if tool_name == "fetch_url" and isinstance(result, dict):
            summaries.append(
                {
                    "tool_name": tool_name,
                    "url": str(result.get("url") or ""),
                    "title": str(result.get("title") or ""),
                    "content_excerpt": _compact_excerpt(result.get("content")),
                    "truncated": bool(result.get("truncated")),
                }
            )
            continue
        summaries.append(
            {
                "tool_name": tool_name,
                "result_keys": sorted(str(key) for key in result.keys())[:20]
                if isinstance(result, dict)
                else [],
                "error": bool(record.get("error")),
            }
        )
    return summaries


def _compact_excerpt(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _intent_update_from_user_input(
    state: MainGraphState,
    *,
    deps: GraphDeps,
    field: str,
    value: Any,
    interaction_id: str,
    step_id: Any,
) -> dict[str, Any]:
    """Apply field-scoped user input as an IntentPatch.

    Brain asks for an intent slot; Loop owns the wait/resume boundary and turns
    the submitted value into ``confirmed_inputs``. Free-form clarification keeps
    flowing through ``human_context`` only.
    """
    if field in {"", "input", "clarification"}:
        return {}
    intent = state.get("human_intent")
    if intent is None:
        return {}

    from ..intent.types import HumanJudgment
    from ..intent.updater import apply_judgment, recompute_autonomy

    judgment = HumanJudgment(
        id=interaction_id,
        kind="clarify",
        target_action_id=None,
        target_stage_id=state.get("stage_id"),
        rationale=f"User supplied intent input `{field}`",
        intent_updates={"confirmed_inputs": {field: value}},
        created_at=now_iso(),
    )
    new_intent = apply_judgment(intent, judgment)
    clarity, scope = recompute_autonomy(
        new_intent,
        estimator=getattr(deps, "clarity_estimator", None),
        task=state["task"],
    )
    loop_update: dict[str, Any] = {}
    loop = state.get("loop_state")
    if loop is not None:
        next_loop = LoopState(**loop)
        next_loop["status"] = "active"
        next_loop["continuation"] = "continue"
        next_loop["intent_version"] = new_intent["version"]
        next_loop["stage_id"] = new_intent["current_stage"]["id"]
        next_loop["last_event_id"] = str(interaction_id)
        next_loop["pending_step_id"] = None
        loop_update["loop_state"] = next_loop
    return {
        **loop_update,
        "human_intent": new_intent,
        "intent_version": new_intent["version"],
        "stage_id": new_intent["current_stage"]["id"],
        "intent_clarity": clarity,
        "autonomy_scope": scope,
        "pending_trace_events": [
            _trace_event(
                state,
                "intent_updated",
                {
                    "judgment_id": interaction_id,
                    "kind": "clarify",
                    "intent_version": new_intent["version"],
                    "clarity_level": clarity["level"],
                    "autonomy_mode": scope["mode"],
                    "confirmed_input_field": field,
                    "step_id": step_id,
                },
            )
        ],
    }


def _normalize_judgment_payload(
    payload: dict[str, Any] | None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Resolve a resume payload into (kind, rationale, intent_updates).

    Two shapes are accepted. The judgment shape carries ``kind`` (a
    ``HumanJudgmentKind``) plus optional ``rationale`` and ``intent_updates``.
    The legacy approval shape carries ``decision`` ("approved"/"rejected") and
    ``reason``; it is bridged so old callers keep working.
    """
    payload = payload or {}
    if "kind" in payload:
        kind = str(payload["kind"])
        rationale = payload.get("rationale") or payload.get("reason")
        updates = dict(payload.get("intent_updates") or {})
        return kind, rationale, updates
    # Legacy approval bridge.
    decision = payload.get("decision", "rejected")
    kind = "approve" if decision == "approved" else "reject"
    return kind, payload.get("reason"), {}


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
    reviewed_action_hash: str | None = None,
    lineage_events: list[TraceEvent] | None = None,
) -> dict[str, Any]:
    """Handle the value LangGraph hands us back from ``Command(resume=...)``.

    Human input is a *judgment*, not just approval. ``approve`` authorizes the
    reviewed action (re-run elevated); every other kind (reject/revise/redirect/
    constrain/clarify/cancel) declines the reviewed action. Any judgment may
    additionally carry ``intent_updates`` — those are applied to the live intent
    field and clarity/autonomy are recomputed, so the agent re-plans under the
    corrected intent on its next turn.
    """
    from ..intent.types import HumanJudgment
    from ..intent.updater import apply_judgment, recompute_autonomy

    kind, rationale, intent_updates = _normalize_judgment_payload(payload)
    approval_id = approval["approval_id"]
    target_action_id = getattr(initial_record, "action_id", None)
    if target_action_id is None and isinstance(initial_record, dict):
        # The interrupting dispatch stamped lineage onto its result, but the
        # persisted tool-call record only carries tool_call_id. Fall back to the
        # lineage event payload so judgment joins stay action-centered.
        for event in lineage_events or []:
            if event["event_type"] == "judgment_requested":
                target_action_id = event["payload"].get("target_action_id")
                break

    # The lineage trio + judgment_requested were built before the interrupt;
    # replay them into the committed update so the action that triggered the
    # judgment is provable from trace, then record how the human resolved it.
    pre_events: list[TraceEvent] = [initial_event, approval_event, *(lineage_events or [])]
    update: dict[str, Any] = {
        "pending_approval": None,
        "pending_judgment": None,
        "status": "running",
        "tool_calls": [initial_record],
        "pending_trace_events": pre_events,
        "pending_tool_calls": [],
    }

    # Apply the human's intent edits (and record the judgment) when the judgment
    # carries updates or is a drift-correcting kind. Only possible when an intent
    # field exists in state (top-level runs always have one post-setup).
    intent = state.get("human_intent")
    correcting = kind in ("revise", "redirect", "constrain", "clarify")
    if intent is not None and (intent_updates or correcting):
        judgment = HumanJudgment(
            id=approval_id,
            kind=kind,  # type: ignore[typeddict-item]
            target_action_id=target_action_id,
            target_stage_id=state.get("stage_id"),
            rationale=rationale,
            intent_updates=intent_updates,  # type: ignore[typeddict-item]
            created_at=now_iso(),
        )
        new_intent = apply_judgment(intent, judgment)
        clarity, scope = recompute_autonomy(
            new_intent, estimator=getattr(deps, "clarity_estimator", None), task=state["task"]
        )
        update["human_intent"] = new_intent
        update["intent_version"] = new_intent["version"]
        update["stage_id"] = new_intent["current_stage"]["id"]
        update["intent_clarity"] = clarity
        update["autonomy_scope"] = scope
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "intent_updated",
                {
                    "judgment_id": approval_id,
                    "kind": kind,
                    "intent_version": new_intent["version"],
                    "clarity_level": clarity["level"],
                    "autonomy_mode": scope["mode"],
                },
            )
        )

    # Record how the human resolved the judgment — the closing half of the
    # judgment_requested event. ``intent_version`` reflects any version bump the
    # judgment caused (else the current version), so trace ties the resolution to
    # the intent it produced.
    update["pending_trace_events"].append(
        _trace_event(
            state,
            "judgment_resolved",
            {
                "judgment_id": approval_id,
                "kind": kind,
                "rationale": rationale,
                "intent_version": update.get("intent_version", state.get("intent_version")),
                "target_action_id": target_action_id,
            },
        )
    )

    if kind != "approve":
        reason = rationale or f"judgment={kind}"
        denied = DeniedAction(
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
                    "judgment_kind": kind,
                    "reason": reason,
                    "fingerprint": denied["fingerprint"],
                },
            )
        )
        update["messages"] = [
            _tool_msg(initial_record, f"tool {proposal['tool_name']} declined ({kind}): {reason}")
        ]
        return update

    if reviewed_action_hash is not None and reviewed_action_hash != hash_tool_call(
        cast(dict[str, Any], proposal)
    ):
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "denial",
                {
                    "approval_id": approval_id,
                    "judgment_kind": kind,
                    "reason": "integrity check failed: resumed action differs from reviewed action",
                    "expected_action_hash": reviewed_action_hash,
                    "actual_action_hash": hash_tool_call(cast(dict[str, Any], proposal)),
                },
            )
        )
        update["messages"] = [
            _tool_msg(
                initial_record,
                "tool "
                f"{proposal['tool_name']} declined (approve): integrity check failed; "
                "resumed action differs from reviewed action",
            )
        ]
        return update

    # Approved: re-run with permission_mode elevated to bypass.
    elevated_state = dict(state)
    elevated_state["permission_mode"] = "trust"
    elevated_state["approved_action_hash"] = reviewed_action_hash
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
            _tool_result_trace_payload(record, dispatch, state=state, proposal=proposal),
        )
    )
    update["pending_trace_events"].extend(_memory_trace_events(state, record))
    if dispatch.outcome == "executed":
        update["messages"] = [_tool_msg(record, str(record["result"]))]
        if _memory_write_committed(record) and deps.recall_cache is not None:
            deps.recall_cache.invalidate(state["run_id"])
        stage_update = _stage_advance_update(state, deps, dispatch)
        if stage_update:
            _merge_tool_update(update, stage_update)
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
            _trace_event(
                state,
                "run_end",
                {
                    "step_id": _run_end_step_id(state),
                    "step_type": "run_end",
                    "previous_step_id": _tool_step_id(state, proposal),
                    "status": "failed",
                },
            )
        )
    return update


def validate_output_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    deps = deps_from_config(config)
    profile = deps.agents.load_agent(state["agent_name"])
    draft = state.get("pending_draft")
    if draft is None:
        return {"pending_draft": None}

    step_id = _validation_step_id(state)
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
    if _should_continue_webagent_tool_loop(profile, draft):
        content = str(draft)
        return {
            "pending_draft": None,
            "messages": [Message(  # type: ignore[typeddict-item]
                role="user",
                content=(
                    "[webagent_tool_loop_required] 智证流程尚未进入最终输出。不要用普通助手文本"
                    "询问、解释或给出“结束采集/继续观察”等选项; 必须继续调用浏览器工具。"
                    "如果页面仍有真实业务动作, 调用 browser_observe 后生成动作卡片并用 "
                    "request_user_input 正式确认; 如果需要现场线下处理, 调用 "
                    "browser_request_manual_intervention(resume_expected=true); 只有最终成功、"
                    "最终失败或用户明确确认终止时, 才调用 submit_output。"
                ),
                tool_call_id=None,
                metadata={"kind": "webagent_tool_loop_feedback", "draft_preview": content[:500]},
            )],
            "pending_trace_events": [
                _trace_event(
                    state,
                    "tool_loop_required",
                    {
                        "agent": profile.get("name", ""),
                        "reason": "webagent_structured_draft_without_submit_output",
                        "draft_preview": content[:500],
                    },
                )
            ],
        }
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
                _trace_event(
                    state,
                    "output_validation",
                    {
                        "step_id": step_id,
                        "step_type": "validation",
                        "status": "rejected",
                        "issues": [issue],
                    },
                )
            ],
        }
        if repair_used > deps.repair_budget:
            update["status"] = "failed"
            update["pending_trace_events"].extend([
                _trace_event(state, "error", {"code": "repair_budget_exhausted"}),
                _trace_event(
                    state,
                    "run_end",
                    {
                        "step_id": _run_end_step_id(state),
                        "step_type": "run_end",
                        "previous_step_id": step_id,
                        "status": "failed",
                    },
                ),
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
        {
            "step_id": step_id,
            "step_type": "validation",
            "status": validation["status"],
            "issues": validation["issues"],
        },
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
                _output_submitted_payload(
                    state,
                    draft,
                    contract,
                    validation,
                    step_id=_output_step_id(state),
                    validation_step_id=step_id,
                ),
            )
        )
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "run_end",
                {
                    "step_id": _run_end_step_id(state),
                    "step_type": "run_end",
                    "previous_step_id": _output_step_id(state),
                    "status": "completed",
                },
            )
        )
    elif validation["status"] == "needs_review":
        update["status"] = "blocked"
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "run_end",
                {
                    "step_id": _run_end_step_id(state),
                    "step_type": "run_end",
                    "previous_step_id": step_id,
                    "status": "blocked",
                },
            )
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
                _trace_event(
                    state,
                    "run_end",
                    {
                        "step_id": _run_end_step_id(state),
                        "step_type": "run_end",
                        "previous_step_id": step_id,
                        "status": "failed",
                    },
                )
            )
        else:
            # Surface the validation issues back into the conversation so the
            # next slow Brain step can repair instead of retrying blind. Use
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


def _should_continue_webagent_tool_loop(profile: AgentProfile, draft: Any) -> bool:
    if not isinstance(draft, str) or not draft.strip():
        return False
    if profile.get("name") != "webagent":
        return False
    skill_names = {str(skill) for skill in profile.get("default_skills") or []}
    if "zhizheng" not in skill_names:
        return False
    contract = profile["output_contract"] or _free_form_contract()
    if contract.get("free_form", False):
        return False
    return True


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
    *,
    step_id: str,
    validation_step_id: str,
) -> dict[str, Any]:
    output = validation.get("output")
    refs = _submitted_output_refs(state)
    payload: dict[str, Any] = {
        "step_id": step_id,
        "step_type": "output",
        "validation_step_id": validation_step_id,
        "status": validation["status"],
        "source": "submit_output" if isinstance(draft, dict) else "assistant_content",
        "schema_valid": validation["status"] in ("validated", "final"),
        "issues": validation.get("issues") or [],
        "output_hash": compute_fingerprint(output),
        "schema_hash": compute_fingerprint(contract.get("schema") or {}),
        "draft_ref": refs.get("draft_ref"),
        "artifact_ref": refs.get("artifact_ref"),
        # Lineage: the final output is traceable to the intent version and stage
        # it was produced under (plan N8 acceptance: "final output can be traced
        # to intent version and stage").
        "intent_version": state.get("intent_version"),
        "stage_id": state.get("stage_id"),
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


def route_after_brain_step(
    state: MainGraphState,
) -> Literal["execute_tool", "await_interaction", "await_judgment", "validate_output", "__end__"]:
    if state.get("pending_interaction") is not None:
        return "await_interaction"
    if state.get("pending_judgment") is not None:
        return "await_judgment"
    if state["status"] in ("interrupted", "blocked", "completed", "failed", "cancelled"):
        return "__end__"
    if state.get("pending_tool_calls"):
        return "execute_tool"
    return "validate_output"


def route_after_tool(
    state: MainGraphState,
) -> Literal["brain_step", "await_interaction", "await_judgment", "max_steps_exceeded", "__end__"]:
    if state.get("pending_interaction") is not None:
        return "await_interaction"
    if state.get("pending_judgment") is not None:
        return "await_judgment"
    if state["status"] in ("interrupted", "blocked", "completed", "failed", "cancelled"):
        return "__end__"
    if state["step_count"] >= state.get("max_steps", 20):  # type: ignore[arg-type]
        if plan_is_complete(state.get("task_plan")):
            return "brain_step"
        return "max_steps_exceeded"
    return "brain_step"


def route_after_validate(
    state: MainGraphState,
) -> Literal["brain_step", "max_steps_exceeded", "__end__"]:
    if state["status"] in ("blocked", "completed", "failed", "cancelled"):
        return "__end__"
    if state["step_count"] >= state.get("max_steps", 20):  # type: ignore[arg-type]
        if plan_is_complete(state.get("task_plan")):
            return "brain_step"
        return "max_steps_exceeded"
    return "brain_step"


def route_after_judgment(
    state: MainGraphState,
) -> Literal["brain_step", "await_interaction", "__end__"]:
    if state.get("pending_interaction") is not None:
        return "await_interaction"
    if state["status"] in ("blocked", "completed", "failed", "cancelled"):
        return "__end__"
    return "brain_step"


def max_steps_exceeded_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """End an exhausted run with an explicit, inspectable failure."""
    del config
    return {
        "status": "failed",
        "pending_trace_events": [
            _trace_event(state, "error", {"code": "max_steps_exceeded"}),
            _trace_event(
                state,
                "run_end",
                {
                    "step_id": _run_end_step_id(state),
                    "step_type": "run_end",
                    "previous_step_id": f"model-{state['step_count']:04d}",
                    "status": "failed",
                    "reason": "max_steps_exceeded",
                },
            ),
        ],
    }


def route_after_setup(state: MainGraphState) -> Literal["brain_step"]:
    return "brain_step"


def await_interaction_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """Pause after checkpointing an interaction, then apply the user's response."""
    deps = deps_from_config(config)
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
                    _trace_event(
                        state,
                        "run_end",
                        {
                            "step_id": _run_end_step_id(state),
                            "step_type": "run_end",
                            "previous_step_id": str(pending["interaction_id"]),
                            "status": "cancelled",
                        },
                    ),
                ],
            }
        if decision != "submitted":
            raise ValueError(f"unsupported user-input decision: {decision}")
        value = payload.get("value")
        error = validate_user_input_response(pending, value)
        if error is not None:
            raise ValueError(error)
        effective_value = value
        default = pending["payload"].get("default")
        if (
            pending["payload"].get("input_type") == "confirm"
            and isinstance(value, str)
            and default is not None
            and is_affirmative_input(value)
        ):
            effective_value = default
        elif value == "" and default is not None:
            effective_value = default
        choices = pending["payload"].get("choices") or []
        if choices and isinstance(effective_value, str):
            effective_value = normalize_choice_input(effective_value, choices)
        result = {
            "field": pending["payload"].get("field"),
            "value": effective_value,
        }
        field = str(result["field"] or "input")
        human_context = _update_human_context(
            state,
            input_item=(field, effective_value),
        )
        intent_update = _intent_update_from_user_input(
            state,
            deps=deps,
            field=field,
            value=effective_value,
            interaction_id=pending["interaction_id"],
            step_id=pending["payload"].get("step_id"),
        )
        record = {
            "tool_call_id": pending.get("tool_call_id") or "",
            "tool_name": "request_user_input",
        }
        messages = [
            _human_message(
                interaction_id=pending["interaction_id"],
                version=human_context["version"],
                content=_human_input_content(field, effective_value),
            )
        ]
        if pending.get("tool_call_id"):
            messages.insert(0, _tool_msg(record, json.dumps(result, ensure_ascii=False)))
        update = {
            "pending_interaction": None,
            "status": "running",
            "human_context": human_context,
            "messages": messages,
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
        _merge_tool_update(update, intent_update)
        return update
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
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "run_end",
                {
                    "step_id": _run_end_step_id(state),
                    "step_type": "run_end",
                    "previous_step_id": str(pending["interaction_id"]),
                    "status": "cancelled",
                },
            )
        )
    else:
        raise ValueError(f"unsupported interaction decision: {decision}")
    return update


def await_judgment_node(state: MainGraphState, config: RunnableConfig) -> dict[str, Any]:
    """Pause after a Brain handoff, then apply the human judgment."""
    deps = deps_from_config(config)
    pending = state.get("pending_judgment")
    if pending is None:
        return {}
    payload = interrupt(dict(pending))
    if payload.get("judgment_id") != pending["judgment_id"]:
        raise ValueError("judgment response does not match the pending judgment")
    kind, rationale, intent_updates = _normalize_judgment_payload(payload)
    allowed_kinds = pending.get("allowed_kinds") or []
    if _is_bare_failure_recovery_approval(pending, kind, intent_updates):
        interaction = PendingInteraction(
            interaction_id=new_ulid(),
            kind="user_input",
            prompt=(
                "Slow Brain still needs a correction before it can continue. "
                "Please describe how to revise or redirect the run, or type /cancel."
            ),
            payload={
                "input_type": "multiline",
                "required": True,
                "field": "clarification",
                "allowed_kinds": ["clarify", "revise", "redirect", "cancel"],
                "reason": "failure_recovery approval without a state change cannot retry safely",
                "judgment_id": pending["judgment_id"],
            },
            tool_call_id=None,
        )
        return {
            "pending_judgment": None,
            "pending_approval": None,
            "pending_interaction": interaction,
            "status": "interrupted",
            "pending_trace_events": [
                _trace_event(
                    state,
                    "judgment_resolved",
                    {
                        "judgment_id": pending["judgment_id"],
                        "kind": kind,
                        "rationale": rationale,
                        "intent_version": state.get("intent_version"),
                        "target_action_id": pending.get("target_action_id"),
                        "target_stage_id": pending.get("target_stage_id"),
                        "recovery": "clarification_required",
                    },
                ),
                _trace_event(state, "interaction_requested", dict(interaction)),
            ],
        }
    if allowed_kinds and kind not in allowed_kinds:
        raise ValueError(
            f"judgment kind {kind!r} is not allowed for this pause; "
            f"expected one of: {', '.join(allowed_kinds)}"
        )
    update: dict[str, Any] = {
        "pending_judgment": None,
        "pending_approval": None,
        "status": "running",
        "pending_trace_events": [
            _trace_event(
                state,
                "judgment_resolved",
                {
                    "judgment_id": pending["judgment_id"],
                    "kind": kind,
                    "rationale": rationale,
                    "intent_version": state.get("intent_version"),
                    "target_action_id": pending.get("target_action_id"),
                    "target_stage_id": pending.get("target_stage_id"),
                },
            )
        ],
    }
    if kind == "cancel":
        update["status"] = "cancelled"
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "run_end",
                {
                    "step_id": _run_end_step_id(state),
                    "step_type": "run_end",
                    "previous_step_id": str(pending["judgment_id"]),
                    "status": "cancelled",
                },
            )
        )
        return update

    intent = state.get("human_intent")
    correcting = kind in ("revise", "redirect", "constrain", "clarify")
    if intent is not None and (intent_updates or correcting):
        from ..intent.types import HumanJudgment
        from ..intent.updater import apply_judgment, recompute_autonomy

        judgment = HumanJudgment(
            id=pending["judgment_id"],
            kind=kind,  # type: ignore[typeddict-item]
            target_action_id=pending.get("target_action_id"),
            target_stage_id=pending.get("target_stage_id"),
            rationale=rationale,
            intent_updates=intent_updates,  # type: ignore[typeddict-item]
            created_at=now_iso(),
        )
        new_intent = apply_judgment(intent, judgment)
        clarity, scope = recompute_autonomy(
            new_intent,
            estimator=getattr(deps, "clarity_estimator", None),
            task=state["task"],
        )
        update["human_intent"] = new_intent
        update["intent_version"] = new_intent["version"]
        update["stage_id"] = new_intent["current_stage"]["id"]
        update["intent_clarity"] = clarity
        update["autonomy_scope"] = scope
        update["pending_trace_events"].append(
            _trace_event(
                state,
                "intent_updated",
                {
                    "judgment_id": pending["judgment_id"],
                    "kind": kind,
                    "intent_version": new_intent["version"],
                    "clarity_level": clarity["level"],
                    "autonomy_mode": scope["mode"],
                },
            )
        )

    loop = state.get("loop_state")
    if loop is not None:
        next_loop = LoopState(**loop)
        next_loop["status"] = "active"
        next_loop["continuation"] = "continue"
        next_loop["intent_version"] = update.get(
            "intent_version", state.get("intent_version", loop["intent_version"])
        )
        next_loop["stage_id"] = update.get(
            "stage_id", state.get("stage_id", loop["stage_id"])
        )
        next_loop["last_event_id"] = str(pending["judgment_id"])
        next_loop["pending_step_id"] = None
        update["loop_state"] = next_loop
    return update


def _is_bare_failure_recovery_approval(
    pending: dict[str, Any],
    kind: str,
    intent_updates: dict[str, Any],
) -> bool:
    if kind != "approve" or intent_updates:
        return False
    if pending.get("trigger") == "failure_recovery":
        return True
    summary = str(pending.get("summary") or "")
    prompt = str(pending.get("prompt") or "")
    return "slow Brain planner failed" in summary or "Slow Brain could not produce" in prompt


def route_after_interaction(
    state: MainGraphState,
) -> Literal["brain_step", "await_interaction", "__end__"]:
    if state.get("pending_interaction") is not None:
        return "await_interaction"
    return "__end__" if state["status"] == "cancelled" else "brain_step"


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


def _tool_result_trace_payload(
    record: dict[str, Any],
    dispatch: Any,
    *,
    state: MainGraphState,
    proposal: ToolCallProposal,
) -> dict[str, Any]:
    metadata = proposal.get("metadata") if isinstance(proposal, dict) else None
    parent_step_id = None
    if isinstance(metadata, dict):
        raw_parent = metadata.get("parent_step_id")
        if isinstance(raw_parent, str) and raw_parent:
            parent_step_id = raw_parent
    attempts = list(getattr(dispatch, "attempts", []) or [])
    last_attempt = attempts[-1] if attempts else {}
    payload: dict[str, Any] = {
        "step_id": _tool_step_id(state, proposal),
        "step_type": "tool",
        "parent_step_id": parent_step_id,
        "tool_call_id": record["tool_call_id"],
        "tool_name": record["tool_name"],
        "decision": record["decision"],
        "outcome": dispatch.outcome,
        "attempt": len(attempts) or 1,
        "attempts": attempts,
        "elapsed_ms": _iso_elapsed_ms(record.get("started_at"), record.get("finished_at")),
        "timeout": bool(last_attempt.get("timeout")),
        "error_code": last_attempt.get("error_code") or _tool_error_code(record, dispatch),
        "idempotency_cache_hit": bool(getattr(dispatch, "idempotency_cache_hit", False)),
    }
    result = record.get("result")
    if result is not None:
        payload["result_fingerprint"] = compute_fingerprint(result)
        if isinstance(result, dict):
            payload["result_keys"] = sorted(str(key) for key in result.keys())[:40]
    return payload


def _iso_elapsed_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((finish - start).total_seconds() * 1000))


def _tool_error_code(record: dict[str, Any], dispatch: Any) -> str | None:
    if dispatch.outcome == "executed":
        return None
    if getattr(dispatch, "error", None) is not None:
        name = dispatch.error.__class__.__name__
        if name == "ToolSchemaError":
            return "schema_validation_failed"
        if name == "ToolUnknownError":
            return "unknown_tool"
        return "tool_error"
    if record.get("error"):
        return "tool_error"
    return str(dispatch.outcome)


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
