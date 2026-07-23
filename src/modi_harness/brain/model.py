"""Model-backed structured planner for autonomous Workflow Nodes."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from ..loop.types import (
    AskRequest,
    InputType,
    RuntimeOperationKind,
    RuntimeOperationProposal,
    StepContext,
    StepDecision,
    StepKind,
)
from ..models import ModelAdapter
from ..types import ContextPack, Message, ToolDescription


class ModelStructuredPlanner:
    """Translate one provider turn into one closed ``StepDecision``.

    The model sees only the active Node goal, inputs, completion contract and
    capability ceiling. It may propose one permitted Operation or propose
    ``complete_node``. Workflow routing remains entirely outside the model.
    """

    def __init__(
        self,
        *,
        model: ModelAdapter,
        instruction: str,
        tool_catalog: Mapping[str, Mapping[str, Any]],
        skill_instructions: Iterable[str] = (),
    ) -> None:
        self._model = model
        self._instruction = instruction
        self._tool_catalog = {name: dict(spec) for name, spec in tool_catalog.items()}
        self._skill_instructions = tuple(
            item.strip() for item in skill_instructions if item.strip()
        )

    def plan_structured_step(self, context: StepContext) -> Mapping[str, Any]:
        declared = tuple(context.get("available_capabilities", {}).get("tools", ()))
        allowed, exhausted = self._available_tools(context, declared)
        fresh_outputs = _pending_fresh_output_hints(
            context,
            allowed,
            self._tool_catalog,
        )
        hidden_issuers = {
            str(item["issued_by"]) for item in fresh_outputs.values() if item.get("issued_by")
        }
        hidden_issuers.update(
            _spent_prerequisite_issuers(
                context,
                declared=declared,
                exhausted=exhausted,
                tool_catalog=self._tool_catalog,
            )
        )
        temporarily_hidden = tuple(name for name in allowed if name in hidden_issuers)
        if temporarily_hidden:
            allowed = tuple(name for name in allowed if name not in hidden_issuers)
        planning_context = cast(StepContext, dict(context))
        capabilities = dict(context.get("available_capabilities", {}))
        capabilities["tools"] = list(allowed)
        capabilities["exhausted_tools"] = list(exhausted)
        if temporarily_hidden:
            capabilities["temporarily_hidden_tools"] = list(temporarily_hidden)
        if fresh_outputs:
            capabilities["fresh_output_prerequisites"] = fresh_outputs
        planning_context["available_capabilities"] = capabilities
        descriptions = [
            self._description(self._tool_catalog[name])
            for name in allowed
            if name in self._tool_catalog
        ]
        if planning_context.get("task_plan") is None:
            descriptions.append(self._request_user_input_description(planning_context))
        descriptions.append(self._complete_node_description(planning_context))
        base_pack = self._context_pack(planning_context, descriptions)
        pack = base_pack
        result = self._model.call(pack)
        repairs = 0
        call: Mapping[str, Any] | None = None
        completion_result: Any = None
        while True:
            content = str((result.get("message") or {}).get("content") or "").strip()
            calls = list(result.get("tool_calls") or [])
            feedback: str | None = None
            call = None

            if calls:
                try:
                    call = self._select_call(calls, planning_context, allowed)
                except ValueError as exc:
                    feedback = str(exc)
            elif content:
                completion_result = self._normalize_result(content, planning_context)
                if self._content_result_satisfies_schema(
                    completion_result,
                    planning_context,
                ):
                    break
                feedback = (
                    "content-only response did not satisfy the Node completion schema; "
                    "use complete_node with structured arguments"
                )
            else:
                feedback = "model response contained no executable Operation or completion result"

            if call is not None:
                break

            assert feedback is not None
            allow_reasoning_retry = repairs == 1 and _is_reasoning_only_response(result)
            if repairs >= 1 and not allow_reasoning_retry:
                raise ValueError(
                    "model repair produced no permitted Operation or result"
                    + _response_diagnostic_suffix(result, repaired=True)
                )
            repairs += 1
            if allow_reasoning_retry:
                feedback = (
                    "model response contained only hidden reasoning and no executable "
                    "Operation or completion result"
                )
            pack = self._repair_pack(base_pack, feedback, allowed)
            result = self._model.call(pack)

        if call is not None:
            target = str(call.get("tool_name") or "")
            arguments = dict(call.get("arguments") or {})
            reason_suffix = (
                f"; deferred {len(calls) - 1} additional proposal(s) to later Steps"
                if len(calls) > 1
                else ""
            )
            if target == "request_user_input":
                return self._ask_decision(arguments, reason_suffix=reason_suffix)
            if target == "complete_node":
                arguments = self._completion_arguments(
                    arguments,
                    content=content,
                    context=planning_context,
                )
                return self._decision(
                    step_kind="verify",
                    reason="model proposed completion for the active Node" + reason_suffix,
                    operation={
                        "kind": "workflow_control",
                        "summary": "complete the active Node",
                        "target": "complete_node",
                        "arguments": arguments,
                        "expected_outcome": "Harness validates the completion contract",
                    },
                )
            if target not in allowed:
                raise ValueError(f"model proposed unavailable Operation {target!r}")
            kind: RuntimeOperationKind = (
                "memory_write" if target in {"save_memory", "propose_memory"} else "tool"
            )
            return self._decision(
                step_kind="act",
                reason=f"model proposed {target}" + reason_suffix,
                operation={
                    "kind": kind,
                    "summary": f"call {target}",
                    "target": target,
                    "arguments": arguments,
                    "expected_outcome": f"{target} returns a usable result",
                },
            )
        return self._decision(
            step_kind="verify",
            reason="model returned a completion candidate",
            operation={
                "kind": "workflow_control",
                "summary": "complete the active Node",
                "target": "complete_node",
                "arguments": {"result": completion_result},
                "expected_outcome": "Harness validates the completion contract",
            },
        )

    def _context_pack(
        self,
        context: StepContext,
        descriptions: list[ToolDescription],
    ) -> ContextPack:
        payload = json.dumps(
            _compact_planning_context(context),
            ensure_ascii=False,
            default=str,
        )
        message = Message(
            role="user",
            content=(
                "Solve only the active Workflow Node below. If required information "
                "is missing or ambiguous, call request_user_input instead of inventing "
                "it. Otherwise propose one permitted tool call, or call complete_node "
                "with the Node output fields directly when the completion contract is "
                "satisfied. Do not nest the output under a result field. "
                + (
                    "This Node already has Harness review: never ask the user to approve "
                    "or confirm a draft; submit it with complete_node. "
                    if context["node"]["completion"].get("review") == "required"
                    else ""
                )
                + "\n\n"
                + payload
            ),
            tool_call_id=None,
            metadata={},
        )
        return ContextPack(
            system_instruction=(
                "You are the single Agent Brain inside one autonomous Workflow Node. "
                "You cannot change the Node goal, Workflow, capability ceiling, limits, "
                "or completion contract. You must request missing user information and "
                "must not fabricate it. Never claim completion outside complete_node."
            ),
            agent_instruction=self._instruction,
            skill_instructions=list(self._skill_instructions),
            memory_blocks=[],
            references=[],
            state_summary="",
            tool_descriptions=descriptions,
            workspace_index=[],
            recent_messages=[message],
            output_requirement=None,
            trust_annotations=[],
            context_hash="",
        )

    @staticmethod
    def _description(spec: Mapping[str, Any]) -> ToolDescription:
        description = str(spec.get("description") or "")
        max_calls = spec.get("max_calls_per_node")
        if isinstance(max_calls, int) and not isinstance(max_calls, bool):
            description += (
                f" This Operation may be called at most {max_calls} times per Node input round."
            )
        max_calls_per_task = spec.get("max_calls_per_task")
        if isinstance(max_calls_per_task, int) and not isinstance(max_calls_per_task, bool):
            description += (
                f" This Operation may be called at most {max_calls_per_task} times "
                "for one TaskPlan item."
            )
        return ToolDescription(
            name=str(spec["name"]),
            description=description,
            input_schema=dict(spec.get("input_schema") or {"type": "object"}),
            risk_level=str(spec.get("risk_level") or "L0"),
            side_effect=bool(spec.get("side_effect", False)),
        )

    def _available_tools(
        self,
        context: StepContext,
        declared: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        counts = _operation_counts_since_human_input(context)
        task_counts = _operation_counts_by_task(context)
        task_plan = context.get("task_plan")
        active_task_id = (
            str(task_plan.get("current_task_id") or "") if isinstance(task_plan, Mapping) else ""
        )
        if not active_task_id:
            active_task_id = _single_operation_task_id(context, declared)
        allowed: list[str] = []
        exhausted: list[str] = []
        for name in declared:
            spec = self._tool_catalog.get(name, {})
            maximum = spec.get("max_calls_per_node")
            task_maximum = spec.get("max_calls_per_task")
            if (
                isinstance(maximum, int)
                and not isinstance(maximum, bool)
                and counts.get(name, 0) >= maximum
            ) or (
                active_task_id
                and isinstance(task_maximum, int)
                and not isinstance(task_maximum, bool)
                and task_counts.get((name, active_task_id), 0) >= task_maximum
            ):
                exhausted.append(name)
            else:
                allowed.append(name)
        return tuple(allowed), tuple(exhausted)

    @staticmethod
    def _select_call(
        calls: Sequence[Mapping[str, Any]],
        context: StepContext,
        allowed: tuple[str, ...],
    ) -> Mapping[str, Any]:
        prior = _operation_fingerprints_since_human_input(context)
        permitted = set(allowed) | {"complete_node"}
        if context.get("task_plan") is None:
            permitted.add("request_user_input")
        candidates = [call for call in calls if str(call.get("tool_name") or "") in permitted]
        if not candidates:
            raise ValueError("model proposed only unavailable or exhausted Operations")
        for call in candidates:
            if _operation_fingerprint(call) not in prior:
                return call
        return candidates[0]

    @staticmethod
    def _repair_pack(
        pack: ContextPack,
        feedback: str,
        allowed: tuple[str, ...],
    ) -> ContextPack:
        repaired = ContextPack(**pack)
        repaired["recent_messages"] = [
            *pack["recent_messages"],
            Message(
                role="user",
                content=(
                    f"Your previous proposal was rejected: {feedback}. "
                    f"Choose exactly one currently available Operation from "
                    f"{list(allowed)}, or complete_node."
                ),
                tool_call_id=None,
                metadata={},
            ),
        ]
        return repaired

    @staticmethod
    def _complete_node_description(context: StepContext) -> ToolDescription:
        schema = dict(context["node"]["completion"].get("output_schema") or {})
        object_schema = schema.get("type") == "object"
        return ToolDescription(
            name="complete_node",
            description=(
                "Propose completion of the active Node. Pass the Node output fields "
                "directly as this tool's arguments; do not nest them under result. "
                "The Harness validates the output."
                if object_schema
                else "Propose completion of the active Node. The Harness validates it."
            ),
            input_schema=(
                schema
                if object_schema
                else {
                    "type": "object",
                    "properties": {"result": schema},
                    "required": ["result"],
                    "additionalProperties": False,
                }
            ),
            risk_level="L0",
            side_effect=False,
        )

    @staticmethod
    def _request_user_input_description(context: StepContext) -> ToolDescription:
        reviewed = context["node"]["completion"].get("review") == "required"
        return ToolDescription(
            name="request_user_input",
            description=(
                "Pause the active Node and ask one concise question required to "
                "continue. Do not add a preamble, repeat a plan, or request confirmation "
                "that is not required by the Node goal."
                + (
                    " This Node already has Harness review. Use this only for a missing "
                    "fact; never use it to ask the user to approve or confirm a draft."
                    if reviewed
                    else ""
                )
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 1},
                    "field": {"type": "string", "minLength": 1},
                    "input_type": {
                        "type": "string",
                        "enum": (
                            ["text", "multiline", "url_list"]
                            if reviewed
                            else ["text", "multiline", "url_list", "confirm"]
                        ),
                    },
                    "required": {"type": "boolean"},
                    "default": {},
                    "choices": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["prompt", "field", "input_type"],
                "additionalProperties": False,
            },
            risk_level="L0",
            side_effect=False,
        )

    @staticmethod
    def _ask_decision(
        arguments: Mapping[str, Any],
        *,
        reason_suffix: str = "",
    ) -> StepDecision:
        prompt = str(arguments.get("prompt") or "").strip()
        field = str(arguments.get("field") or "").strip()
        input_type = str(arguments.get("input_type") or "").strip()
        if not prompt:
            raise ValueError("request_user_input requires a non-empty prompt")
        if not field:
            raise ValueError("request_user_input requires a non-empty field")
        if input_type not in {"text", "multiline", "url_list", "confirm"}:
            raise ValueError("request_user_input has an unsupported input_type")
        ask = AskRequest(
            prompt=prompt,
            field=field,
            input_type=cast(InputType, input_type),
            required=bool(arguments.get("required", True)),
        )
        if "default" in arguments:
            ask["default"] = arguments["default"]
        if "choices" in arguments:
            choices = arguments["choices"]
            if not isinstance(choices, list) or not all(
                isinstance(item, str) and item.strip() for item in choices
            ):
                raise ValueError("request_user_input choices must be non-empty strings")
            ask["choices"] = choices
        return StepDecision(
            id="assigned-by-brain",
            step_kind="clarify",
            reason=(
                "the active Node needs user information before it can continue" + reason_suffix
            ),
            intent_patch=None,
            ask=ask,
            operation=None,
            expected_state_change=None,
            postcheck=None,
            continuation="wait",
            human_judgment={
                "required": False,
                "reason": "missing information is an input request, not a judgment",
                "trigger": "none",
            },
            continuation_basis=None,
        )

    @staticmethod
    def _decision(
        *,
        step_kind: StepKind,
        reason: str,
        operation: RuntimeOperationProposal,
    ) -> StepDecision:
        return StepDecision(
            id="assigned-by-brain",
            step_kind=step_kind,
            reason=reason,
            intent_patch=None,
            ask=None,
            operation=operation,
            expected_state_change=None,
            postcheck=None,
            continuation="continue",
            human_judgment={
                "required": False,
                "reason": "the proposal stays inside the active Node contract",
                "trigger": "none",
            },
            continuation_basis={
                "source": "planner",
                "reference": operation["target"],
                "reason": "continue within the active Node",
            },
        )

    @staticmethod
    def _normalize_result(content: str, context: StepContext) -> Any:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            schema = context["node"]["completion"].get("output_schema") or {}
            required = list(schema.get("required") or []) if isinstance(schema, Mapping) else []
            if len(required) == 1:
                return {str(required[0]): content}
            return content

    @staticmethod
    def _content_result_satisfies_schema(result: Any, context: StepContext) -> bool:
        schema = context["node"]["completion"].get("output_schema") or {}
        if not schema:
            return True
        return not any(Draft202012Validator(schema).iter_errors(result))

    @classmethod
    def _completion_arguments(
        cls,
        arguments: Mapping[str, Any],
        *,
        content: str,
        context: StepContext,
    ) -> dict[str, Any]:
        schema = context["node"]["completion"].get("output_schema") or {}
        object_schema = isinstance(schema, Mapping) and schema.get("type") == "object"
        if object_schema and arguments:
            return {"result": dict(arguments)}
        if not object_schema and "result" in arguments:
            return dict(arguments)
        if content:
            try:
                candidate = json.loads(content)
            except json.JSONDecodeError:
                return {}
            if object_schema:
                return {"result": dict(candidate)} if isinstance(candidate, Mapping) else {}
            return {"result": candidate}
        return {}


__all__ = ["ModelStructuredPlanner"]


def _compact_planning_context(context: StepContext) -> dict[str, Any]:
    """Remove contracts already carried by trusted model-message fields."""

    compact = dict(context)

    node = dict(context["node"])
    completion = dict(context["node"]["completion"])
    completion.pop("output_schema", None)
    node["completion"] = completion
    compact["node"] = node

    agent_state = context.get("agent_state")
    if isinstance(agent_state, Mapping):
        compact_agent_state = dict(agent_state)
        compact_agent_state.pop("instruction", None)
        compact["agent_state"] = compact_agent_state

    return compact


def _response_diagnostic_suffix(result: Mapping[str, Any], *, repaired: bool) -> str:
    """Format only bounded adapter diagnostics into a persisted failure string."""

    info = result.get("model_info")
    if not isinstance(info, Mapping):
        return f" (repaired={str(repaired).lower()})"
    finish = str(info.get("finish_reason") or "unknown")
    tool_count = info.get("tool_call_count")
    content_types = info.get("content_block_types")
    usage = info.get("usage")
    usage_total = usage.get("total_tokens") if isinstance(usage, Mapping) else None
    return (
        f" (finish_reason={finish!r}; tool_call_count={tool_count!r}; "
        f"content_block_types={content_types!r}; total_tokens={usage_total!r}; "
        f"repaired={str(repaired).lower()})"
    )


def _is_reasoning_only_response(result: Mapping[str, Any]) -> bool:
    info = result.get("model_info")
    if not isinstance(info, Mapping):
        return False
    content_types = info.get("content_block_types")
    if not isinstance(content_types, (list, tuple)) or not content_types:
        return False
    return all(str(item).lower() in {"thinking", "reasoning"} for item in content_types)


def _steps_since_human_input(context: StepContext) -> list[Mapping[str, Any]]:
    steps = list(context.get("recent_steps", ()))
    last_human_index = max(
        (
            int(step.get("index") or 0)
            for step in steps
            if isinstance(step.get("state_delta"), Mapping) and "human_input" in step["state_delta"]
        ),
        default=0,
    )
    return [step for step in steps if int(step.get("index") or 0) > last_human_index]


def _operation_counts_since_human_input(context: StepContext) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in _steps_since_human_input(context):
        decision = step.get("decision")
        operation = decision.get("operation") if isinstance(decision, Mapping) else None
        if not isinstance(operation, Mapping):
            continue
        target = str(operation.get("target") or "")
        if target:
            counts[target] = counts.get(target, 0) + 1
    return counts


def _pending_fresh_output_hints(
    context: StepContext,
    declared: tuple[str, ...],
    tool_catalog: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Expose a just-issued prerequisite value and suppress redundant re-issuance."""

    steps = list(context.get("recent_steps", ()))
    if not steps:
        return {}
    last = steps[-1]
    decision = last.get("decision")
    operation = decision.get("operation") if isinstance(decision, Mapping) else None
    state_delta = last.get("state_delta")
    output = state_delta.get("operation_output") if isinstance(state_delta, Mapping) else None
    if not isinstance(operation, Mapping) or not isinstance(output, Mapping):
        return {}
    issuer = str(operation.get("target") or "")
    if not issuer:
        return {}
    hints: dict[str, dict[str, str]] = {}
    for dependent in declared:
        prerequisite = tool_catalog.get(dependent, {}).get("fresh_output_prerequisite")
        if not isinstance(prerequisite, Mapping):
            continue
        if str(prerequisite.get("issuer_adapter") or "") != issuer:
            continue
        argument = str(prerequisite.get("argument") or "").strip()
        output_field = str(prerequisite.get("issuer_output_field") or "").strip()
        value = str(output.get(output_field) or "").strip()
        if not argument or not value:
            continue
        hints[dependent] = {
            "argument": argument,
            "value": value,
            "issued_by": issuer,
            "instruction": (
                f"Call {dependent} next and pass this exact value as {argument}; "
                f"do not call {issuer} again first."
            ),
        }
    return hints


def _operation_counts_by_task(context: StepContext) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for step in context.get("recent_steps", ()):
        decision = step.get("decision")
        operation = decision.get("operation") if isinstance(decision, Mapping) else None
        if not isinstance(operation, Mapping):
            continue
        target = str(operation.get("target") or "")
        arguments = operation.get("arguments")
        task_id = str(arguments.get("task_id") or "") if isinstance(arguments, Mapping) else ""
        if not target or not task_id:
            continue
        if target == "record_research_finding":
            for key in [key for key in counts if key[1] == task_id]:
                del counts[key]
            continue
        state_delta = step.get("state_delta")
        if isinstance(state_delta, Mapping) and state_delta.get("operation_error"):
            continue
        key = (target, task_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _single_operation_task_id(
    context: StepContext,
    declared: tuple[str, ...],
) -> str:
    """Infer the one bounded Task used by isolated child Workflows."""

    declared_set = set(declared)
    task_ids: set[str] = set()
    for step in context.get("recent_steps", ()):
        decision = step.get("decision")
        operation = decision.get("operation") if isinstance(decision, Mapping) else None
        if not isinstance(operation, Mapping):
            continue
        if str(operation.get("target") or "") not in declared_set:
            continue
        arguments = operation.get("arguments")
        task_id = str(arguments.get("task_id") or "") if isinstance(arguments, Mapping) else ""
        if task_id:
            task_ids.add(task_id)
    return next(iter(task_ids)) if len(task_ids) == 1 else ""


def _spent_prerequisite_issuers(
    context: StepContext,
    *,
    declared: tuple[str, ...],
    exhausted: tuple[str, ...],
    tool_catalog: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Hide prerequisite tools when every operation they can unlock is exhausted."""

    exhausted_set = set(exhausted)
    dependents_by_issuer: dict[str, set[str]] = {}
    for name in declared:
        prerequisite = tool_catalog.get(name, {}).get("fresh_output_prerequisite")
        if not isinstance(prerequisite, Mapping):
            continue
        issuer = str(prerequisite.get("issuer_adapter") or "")
        if issuer:
            dependents_by_issuer.setdefault(issuer, set()).add(name)
    used = {
        str(operation.get("target") or "")
        for step in context.get("recent_steps", ())
        if isinstance((decision := step.get("decision")), Mapping)
        and isinstance((operation := decision.get("operation")), Mapping)
        and isinstance((state_delta := step.get("state_delta")), Mapping)
        and not state_delta.get("operation_error")
    }
    return {
        issuer
        for issuer, dependents in dependents_by_issuer.items()
        if issuer in declared
        and issuer in used
        and dependents
        and dependents.issubset(exhausted_set)
    }


def _operation_fingerprints_since_human_input(context: StepContext) -> set[str]:
    fingerprints: set[str] = set()
    for step in _steps_since_human_input(context):
        decision = step.get("decision")
        operation = decision.get("operation") if isinstance(decision, Mapping) else None
        if isinstance(operation, Mapping):
            fingerprints.add(
                _operation_fingerprint(
                    {
                        "tool_name": operation.get("target"),
                        "arguments": operation.get("arguments"),
                    }
                )
            )
    return fingerprints


def _operation_fingerprint(call: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "target": str(call.get("tool_name") or ""),
            "arguments": _plain_json(call.get("arguments") or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_json(item) for item in value]
    return value
