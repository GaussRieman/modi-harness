"""AgentLoop embedded inside one autonomous Workflow Node attempt."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .._utils import compute_fingerprint, new_ulid, now_iso
from .types import (
    AutonomousNodeContext,
    BrainIntentPatch,
    BrainIntentPatchValidationError,
    CompletedStep,
    ContinuationBasis,
    HumanJudgmentAssessment,
    LoopContinuation,
    LoopContinuationDecision,
    LoopState,
    PreparedStep,
    StepContext,
    StepDecision,
    StepRecord,
    StepValidationError,
)

if TYPE_CHECKING:
    from ..brain import Brain

_DECISION_FIELDS = frozenset(
    {
        "id",
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
    }
)
_STEP_KINDS = frozenset({"clarify", "plan", "observe", "act", "verify", "handoff"})
_BRAIN_HISTORY_STRING_LIMIT = 1_200
_BRAIN_HISTORY_STEP_LIMIT = 16_000
_BRAIN_HISTORY_TOTAL_LIMIT = 48_000
_OPERATION_KINDS = frozenset({"tool", "memory_write", "workflow_control"})
_JUDGMENT_TRIGGERS = frozenset({"none", "boundary", "autonomy_scope", "operation_risk"})
_CONTINUATION_SOURCES = frozenset({"task_plan", "postcheck_result", "autonomy_budget", "planner"})
_ALLOWED_BRAIN_PATCH_KEYS = frozenset(
    {
        "goal",
        "desired_outcome",
        "add_boundaries",
        "remove_boundary_ids",
        "add_non_goals",
        "add_success_criteria",
        "confirmed_inputs",
        "tradeoffs",
    }
)


@dataclass(frozen=True)
class AgentLoop:
    """Semantic step controller scoped to one autonomous Node attempt."""

    state: LoopState
    brain: Brain

    def __post_init__(self) -> None:
        for field in ("workflow_run_id", "workflow_id", "node_id"):
            if not str(self.state.get(field) or "").strip():
                raise ValueError(f"AgentLoop requires non-empty {field}")
        if self.state["node_attempt"] < 1:
            raise ValueError("AgentLoop node_attempt must be positive")

    def prepare_step(
        self,
        *,
        step_id: str,
        node: AutonomousNodeContext,
        event: dict[str, Any] | None,
        intent: Mapping[str, Any] | None,
        intent_clarity: Mapping[str, Any] | None,
        autonomy_scope: Mapping[str, Any] | None,
        agent_profile: Mapping[str, Any],
        recent_steps: list[StepRecord],
        available_capabilities: dict[str, Any],
        task_plan: Mapping[str, Any] | None = None,
        input_event_id: str | None = None,
    ) -> PreparedStep:
        context = build_step_context(
            step_id=step_id,
            loop=self.state,
            node=node,
            event=event,
            intent=intent,
            intent_clarity=intent_clarity,
            autonomy_scope=autonomy_scope,
            agent_profile=agent_profile,
            recent_steps=recent_steps,
            available_capabilities=available_capabilities,
            task_plan=task_plan,
        )
        decision = self.brain.plan_step(context)
        validate_step_decision(decision)
        record = begin_step_record(
            loop=self.state,
            decision=decision,
            input_event_id=input_event_id,
        )
        return PreparedStep(context=context, decision=decision, record=record)

    def complete_step(
        self,
        record: StepRecord,
        *,
        status: str = "completed",
        state_delta: dict[str, Any] | None = None,
    ) -> CompletedStep:
        completed = complete_step_record(record, status=status, state_delta=state_delta)
        continuation = decide_loop_continuation(loop=self.state, step=completed)
        loop = advance_loop_state(loop=self.state, step=completed, continuation=continuation)
        return CompletedStep(record=completed, continuation=continuation, loop=loop)


def initialize_loop_state(
    *,
    workflow_run_id: str,
    workflow_id: str,
    node_id: str,
    node_attempt: int,
    agent_name: str,
    intent_version: int,
    max_auto_steps: int,
) -> LoopState:
    """Create Loop state only when complete autonomous Node scope is supplied."""

    for field, value in {
        "workflow_run_id": workflow_run_id,
        "workflow_id": workflow_id,
        "node_id": node_id,
        "agent_name": agent_name,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be non-empty")
    if node_attempt < 1:
        raise ValueError("node_attempt must be positive")
    if max_auto_steps < 1:
        raise ValueError("max_auto_steps must be positive")
    return LoopState(
        loop_id=new_ulid(),
        workflow_run_id=workflow_run_id,
        workflow_id=workflow_id,
        node_id=node_id,
        node_attempt=node_attempt,
        agent_name=agent_name,
        status="active",
        intent_version=intent_version,
        step_index=0,
        max_auto_steps=max_auto_steps,
        continuation="continue",
        last_event_id=None,
        pending_step_id=None,
    )


def build_step_context(
    *,
    step_id: str,
    loop: LoopState,
    node: AutonomousNodeContext,
    event: dict[str, Any] | None,
    intent: Mapping[str, Any] | None,
    intent_clarity: Mapping[str, Any] | None,
    autonomy_scope: Mapping[str, Any] | None,
    agent_profile: Mapping[str, Any],
    recent_steps: list[StepRecord],
    available_capabilities: dict[str, Any],
    task_plan: Mapping[str, Any] | None = None,
) -> StepContext:
    """Construct the compact one-Brain input for the active Node."""

    return StepContext(
        step_id=step_id,
        loop=loop,
        node=AutonomousNodeContext(
            goal=str(node["goal"]),
            inputs=dict(node["inputs"]),
            completion=dict(node["completion"]),
        ),
        event=dict(event) if event is not None else None,
        intent=dict(intent or {}),
        intent_clarity=dict(intent_clarity or {}),
        autonomy_scope=dict(autonomy_scope or {}),
        agent_state={
            "agent_name": agent_profile.get("name", loop["agent_name"]),
            "description": agent_profile.get("description", ""),
            "instruction": agent_profile.get("instruction", ""),
            "output_contract": agent_profile.get("output_contract"),
        },
        recent_steps=list(recent_steps),
        available_capabilities=dict(available_capabilities),
        task_plan=dict(task_plan) if task_plan is not None else None,
    )


def project_recent_steps_for_brain(recent_steps: list[StepRecord]) -> list[StepRecord]:
    """Bound operation outputs without discarding Brain control metadata.

    Checkpoints retain the authoritative records. The Brain projection keeps
    decision arguments, indices, and human-input markers exact, while output
    strings are bounded newest-first. URL/search identifiers remain short
    scalar values and therefore survive the generic projection unchanged.
    """

    remaining = _BRAIN_HISTORY_TOTAL_LIMIT
    projected: list[StepRecord] = []
    for original in reversed(recent_steps):
        record = dict(original)
        state_delta = dict(original.get("state_delta") or {})
        if "operation_output" in state_delta:
            budget = min(_BRAIN_HISTORY_STEP_LIMIT, max(0, remaining))
            output = state_delta.get("operation_output")
            if budget:
                compacted, used = _compact_brain_value(output, budget)
                state_delta["operation_output"] = compacted
                remaining -= used
            else:
                state_delta["operation_output"] = _brain_truncation_marker(output)
        record["state_delta"] = state_delta
        projected.append(record)  # type: ignore[arg-type]
    projected.reverse()
    return projected


def _compact_brain_value(value: Any, budget: int) -> tuple[Any, int]:
    if budget <= 0:
        return _brain_truncation_marker(value), 0
    if isinstance(value, str):
        if len(value) <= _BRAIN_HISTORY_STRING_LIMIT and len(value) <= budget:
            return value, len(value)
        limit = min(_BRAIN_HISTORY_STRING_LIMIT, max(0, budget - 96))
        marker = value[:limit]
        string_result = {
            "__truncated__": marker,
            "original_fingerprint": compute_fingerprint(value),
        }
        return string_result, min(budget, len(marker) + 96)
    if isinstance(value, Mapping):
        mapping_result: dict[str, Any] = {}
        used = 2
        for key, item in value.items():
            if used >= budget:
                mapping_result[str(key)] = _preserve_brain_identity(item, key=str(key))
                continue
            compacted, consumed = _compact_brain_value(item, budget - used)
            mapping_result[str(key)] = compacted
            used += consumed + len(str(key))
        return mapping_result, min(used, budget)
    if isinstance(value, tuple | list):
        list_result: list[Any] = []
        used = 2
        for item in value:
            if used >= budget:
                list_result.append(_preserve_brain_identity(item))
                continue
            compacted, consumed = _compact_brain_value(item, budget - used)
            list_result.append(compacted)
            used += consumed + 1
        return list_result, min(used, budget)
    if isinstance(value, int | float | bool) or value is None:
        return value, min(budget, 16)
    return _compact_brain_value(str(value), budget)


def _brain_truncation_marker(value: Any) -> dict[str, Any]:
    return {
        "__truncated__": True,
        "original_fingerprint": compute_fingerprint(value),
    }


def _preserve_brain_identity(value: Any, *, key: str = "") -> Any:
    """Keep identifiers and URLs when an output body reaches its budget."""

    lowered = key.lower()
    if isinstance(value, str):
        if (
            lowered.endswith(("_id", "_ids", "_url", "_urls"))
            or lowered in {"id", "url", "source_url", "task_id"}
        ):
            return value
        return _brain_truncation_marker(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): _preserve_brain_identity(item, key=str(item_key))
            for item_key, item in value.items()
            if (
                str(item_key).lower().endswith(("_id", "_ids", "_url", "_urls"))
                or str(item_key).lower()
                in {"id", "url", "source_url", "task_id", "title"}
            )
        }
    if isinstance(value, (tuple, list)):
        return [_preserve_brain_identity(item, key=key) for item in value]
    return value if isinstance(value, (int, float, bool)) or value is None else _brain_truncation_marker(value)


def validate_brain_intent_patch(patch: BrainIntentPatch | None) -> None:
    if not patch:
        return
    unknown = set(patch) - _ALLOWED_BRAIN_PATCH_KEYS
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise BrainIntentPatchValidationError(
            f"BrainIntentPatch contains unsupported keys: {joined}"
        )


def validate_step_decision(decision: StepDecision) -> None:
    """Enforce the closed single-Brain decision protocol."""

    fields = set(decision)
    missing = _DECISION_FIELDS - fields
    unknown = fields - _DECISION_FIELDS
    if missing:
        raise StepValidationError(f"StepDecision is missing field(s): {', '.join(sorted(missing))}")
    if unknown:
        raise StepValidationError(
            f"StepDecision contains unsupported field(s): {', '.join(sorted(unknown))}"
        )
    if decision["step_kind"] not in _STEP_KINDS:
        raise StepValidationError(f"unsupported Step kind {decision['step_kind']!r}")
    if decision["continuation"] not in {"continue", "wait"}:
        raise StepValidationError(f"unsupported Step continuation {decision['continuation']!r}")
    validate_brain_intent_patch(decision["intent_patch"])
    if decision["ask"] is not None and decision["operation"] is not None:
        raise StepValidationError("StepDecision cannot carry both ask and operation")

    operation = decision["operation"]
    if operation is not None:
        if operation.get("kind") not in _OPERATION_KINDS:
            raise StepValidationError(
                f"unsupported RuntimeOperation kind {operation.get('kind')!r}"
            )
        if operation["kind"] == "workflow_control" and operation["target"] != "complete_node":
            raise StepValidationError("complete_node is the only Workflow control Operation")

    human = decision["human_judgment"]
    if human.get("trigger") not in _JUDGMENT_TRIGGERS:
        raise StepValidationError(f"unsupported human judgment trigger {human.get('trigger')!r}")
    if human["required"] and operation is not None:
        raise StepValidationError("StepDecision requiring human judgment cannot carry operation")
    if human["required"] and decision["ask"] is None and decision["continuation"] != "wait":
        raise StepValidationError("required human judgment must ask or wait")
    if not human["reason"].strip():
        raise StepValidationError("human judgment assessment must include a reason")

    basis = decision["continuation_basis"]
    if decision["continuation"] == "continue" and basis is None:
        raise StepValidationError("continue requires continuation_basis")
    if basis is not None and basis.get("source") not in _CONTINUATION_SOURCES:
        raise StepValidationError(f"unsupported continuation basis {basis.get('source')!r}")


def begin_step_record(
    *,
    loop: LoopState,
    decision: StepDecision,
    input_event_id: str | None = None,
) -> StepRecord:
    validate_step_decision(decision)
    return StepRecord(
        step_id=decision["id"],
        loop_id=loop["loop_id"],
        workflow_run_id=loop["workflow_run_id"],
        workflow_id=loop["workflow_id"],
        node_id=loop["node_id"],
        node_attempt=loop["node_attempt"],
        index=loop["step_index"] + 1,
        step_kind=decision["step_kind"],
        status="planned",
        intent_version=loop["intent_version"],
        input_event_id=input_event_id,
        decision=decision,
        operation_ref=None,
        operation_result_ref=None,
        state_delta={},
        postcheck_result=None,
        started_at=now_iso(),
        finished_at=None,
    )


def complete_step_record(
    record: StepRecord,
    *,
    status: str = "completed",
    state_delta: dict[str, Any] | None = None,
) -> StepRecord:
    if status not in {"waiting", "completed", "failed"}:
        raise ValueError(f"unsupported StepRecord terminal status {status!r}")
    updated = StepRecord(**record)
    updated["status"] = status  # type: ignore[typeddict-item]
    updated["state_delta"] = dict(state_delta or {})
    updated["finished_at"] = now_iso()
    return updated


def decide_loop_continuation(
    *,
    loop: LoopState,
    step: StepRecord,
) -> LoopContinuationDecision:
    decision = step["decision"]
    blockers: list[str] = []
    operation = decision["operation"]
    if step["status"] == "failed":
        blockers.append("step_failed")
    if decision["human_judgment"]["required"]:
        blockers.append("human_judgment_required")
    if decision["ask"] is not None:
        blockers.append("ask_pending")
    if loop["step_index"] + 1 >= loop["max_auto_steps"]:
        blockers.append("max_auto_steps_reached")

    if "max_auto_steps_reached" in blockers:
        outcome: LoopContinuation = "fail"
    elif "human_judgment_required" in blockers:
        outcome = "wait_for_judgment"
    elif "ask_pending" in blockers:
        outcome = "wait_for_user"
    elif (
        operation is not None
        and operation["kind"] == "workflow_control"
        and operation["target"] == "complete_node"
    ):
        outcome = "node_completion_proposed"
    elif "step_failed" in blockers and decision["continuation"] != "continue":
        outcome = "fail"
    elif decision["continuation"] == "continue":
        outcome = "continue"
    else:
        outcome = "wait_for_user"

    reason = "; ".join(blockers)
    if not reason:
        basis = decision["continuation_basis"]
        reason = basis["reason"] if basis is not None else "Brain requested wait"
    return LoopContinuationDecision(
        outcome=outcome,
        step_id=step["step_id"],
        requested=decision["continuation"],
        basis=decision["continuation_basis"],
        blockers=blockers,
        reason=reason,
    )


def advance_loop_state(
    *,
    loop: LoopState,
    step: StepRecord,
    continuation: LoopContinuationDecision,
) -> LoopState:
    status = loop["status"]
    if continuation["outcome"] in {"wait_for_user", "wait_for_judgment"}:
        status = "waiting"
    elif continuation["outcome"] == "fail":
        status = "failed"
    elif continuation["outcome"] == "cancel":
        status = "cancelled"
    else:
        status = "active"
    updated = LoopState(**loop)
    updated["status"] = status
    updated["step_index"] = step["index"]
    updated["continuation"] = continuation["outcome"]
    updated["pending_step_id"] = None
    updated["intent_version"] = step["intent_version"]
    return updated


def planner_step_decision(
    *,
    step_id: str,
    reason: str = "planner selected the next semantic step",
) -> StepDecision:
    """Build a minimal valid decision for tests and simple adapters."""

    decision = StepDecision(
        id=step_id,
        step_kind="plan",
        reason=reason,
        intent_patch=None,
        ask=None,
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="continue",
        human_judgment=HumanJudgmentAssessment(
            required=False,
            reason="planning remains inside the active autonomous Node",
            trigger="none",
        ),
        continuation_basis=ContinuationBasis(
            source="planner",
            reference=None,
            reason="continue with the current Node plan",
        ),
    )
    validate_step_decision(decision)
    return decision


__all__ = [
    "AgentLoop",
    "advance_loop_state",
    "begin_step_record",
    "build_step_context",
    "complete_step_record",
    "decide_loop_continuation",
    "initialize_loop_state",
    "planner_step_decision",
    "validate_brain_intent_patch",
    "validate_step_decision",
]
