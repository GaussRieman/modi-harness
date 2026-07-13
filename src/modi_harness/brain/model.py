"""Model-backed structured planner for autonomous Workflow Nodes."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

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
        planning_context = cast(StepContext, dict(context))
        capabilities = dict(context.get("available_capabilities", {}))
        capabilities["tools"] = list(allowed)
        capabilities["exhausted_tools"] = list(exhausted)
        planning_context["available_capabilities"] = capabilities
        descriptions = [
            self._description(self._tool_catalog[name])
            for name in allowed
            if name in self._tool_catalog
        ]
        descriptions.append(self._request_user_input_description())
        descriptions.append(self._complete_node_description(planning_context))
        pack = self._context_pack(planning_context, descriptions)
        result = self._model.call(pack)
        calls = list(result.get("tool_calls") or [])
        if calls:
            call = self._select_call(calls, planning_context, allowed)
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

        content = str((result.get("message") or {}).get("content") or "").strip()
        if not content:
            raise ValueError("model produced neither an Operation nor a completion result")
        return self._decision(
            step_kind="verify",
            reason="model returned a completion candidate",
            operation={
                "kind": "workflow_control",
                "summary": "complete the active Node",
                "target": "complete_node",
                "arguments": {"result": self._normalize_result(content, context)},
                "expected_outcome": "Harness validates the completion contract",
            },
        )

    def _context_pack(
        self,
        context: StepContext,
        descriptions: list[ToolDescription],
    ) -> ContextPack:
        payload = json.dumps(context, ensure_ascii=False, default=str)
        message = Message(
            role="user",
            content=(
                "Solve only the active Workflow Node below. If required information "
                "is missing or ambiguous, call request_user_input instead of inventing "
                "it. Otherwise propose one permitted tool call, or call complete_node "
                "with {result: ...} when the completion contract is satisfied.\n\n"
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
                f" This Operation may be called at most {max_calls} times per Node "
                "input round."
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
        allowed: list[str] = []
        exhausted: list[str] = []
        for name in declared:
            spec = self._tool_catalog.get(name, {})
            maximum = spec.get("max_calls_per_node")
            if (
                isinstance(maximum, int)
                and not isinstance(maximum, bool)
                and counts.get(name, 0) >= maximum
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
        permitted = set(allowed) | {"request_user_input", "complete_node"}
        candidates = [
            call for call in calls if str(call.get("tool_name") or "") in permitted
        ]
        if not candidates:
            raise ValueError("model proposed only unavailable or exhausted Operations")
        for call in candidates:
            if _operation_fingerprint(call) not in prior:
                return call
        return candidates[0]

    @staticmethod
    def _complete_node_description(context: StepContext) -> ToolDescription:
        schema = dict(context["node"]["completion"].get("output_schema") or {})
        return ToolDescription(
            name="complete_node",
            description="Propose completion of the active Node. The Harness validates it.",
            input_schema={
                "type": "object",
                "properties": {"result": schema},
                "required": ["result"],
                "additionalProperties": False,
            },
            risk_level="L0",
            side_effect=False,
        )

    @staticmethod
    def _request_user_input_description() -> ToolDescription:
        return ToolDescription(
            name="request_user_input",
            description=(
                "Pause the active Node and ask one concise question required to "
                "continue. Do not add a preamble, repeat a plan, or request confirmation "
                "that is not required by the Node goal."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 1},
                    "field": {"type": "string", "minLength": 1},
                    "input_type": {
                        "type": "string",
                        "enum": ["text", "multiline", "url_list", "confirm"],
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
                "the active Node needs user information before it can continue"
                + reason_suffix
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


__all__ = ["ModelStructuredPlanner"]


def _steps_since_human_input(context: StepContext) -> list[Mapping[str, Any]]:
    steps = list(context.get("recent_steps", ()))
    last_human_index = max(
        (
            int(step.get("index") or 0)
            for step in steps
            if isinstance(step.get("state_delta"), Mapping)
            and "human_input" in step["state_delta"]
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
