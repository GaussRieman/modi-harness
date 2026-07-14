"""Closed contracts for AgentLoop embedded in one autonomous Workflow Node."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

LoopStatus = Literal["active", "waiting", "failed", "cancelled"]
LoopContinuation = Literal[
    "continue",
    "wait_for_user",
    "wait_for_judgment",
    "node_completion_proposed",
    "fail",
    "cancel",
]
StepKind = Literal["clarify", "plan", "observe", "act", "verify", "handoff"]
RuntimeOperationKind = Literal["tool", "memory_write", "workflow_control"]


class LoopState(TypedDict):
    loop_id: str
    workflow_run_id: str
    workflow_id: str
    node_id: str
    node_attempt: int
    agent_name: str
    status: LoopStatus
    intent_version: int
    step_index: int
    max_auto_steps: int
    continuation: LoopContinuation
    last_event_id: str | None
    pending_step_id: str | None


class BrainIntentPatch(TypedDict, total=False):
    goal: str
    desired_outcome: str | None
    add_boundaries: list[dict[str, Any]]
    remove_boundary_ids: list[str]
    add_non_goals: list[str]
    add_success_criteria: list[str]
    confirmed_inputs: dict[str, Any]
    tradeoffs: dict[str, str]


class RuntimeOperationProposal(TypedDict):
    kind: RuntimeOperationKind
    summary: str
    target: str
    arguments: dict[str, Any]
    expected_outcome: str | None


HumanJudgmentTrigger = Literal["none", "boundary", "autonomy_scope", "operation_risk"]


class HumanJudgmentAssessment(TypedDict):
    required: bool
    reason: str
    trigger: HumanJudgmentTrigger


ContinuationBasisSource = Literal[
    "task_plan",
    "postcheck_result",
    "autonomy_budget",
    "planner",
]


class ContinuationBasis(TypedDict):
    source: ContinuationBasisSource
    reference: str | None
    reason: str


class StepPostcheck(TypedDict, total=False):
    conditions: list[str]
    reason: str


class StepPostcheckResult(TypedDict):
    passed: bool
    reason: str


InputType = Literal["text", "multiline", "url_list", "confirm"]


class AskRequest(TypedDict, total=False):
    prompt: str
    reason: str
    allowed_kinds: list[str]
    field: str
    input_type: InputType
    required: bool
    default: Any
    choices: list[str]


StepContinuationRequest = Literal["continue", "wait"]


class StepDecision(TypedDict):
    id: str
    step_kind: StepKind
    reason: str
    intent_patch: BrainIntentPatch | None
    ask: AskRequest | None
    operation: RuntimeOperationProposal | None
    expected_state_change: dict[str, Any] | None
    postcheck: StepPostcheck | None
    continuation: StepContinuationRequest
    human_judgment: HumanJudgmentAssessment
    continuation_basis: ContinuationBasis | None


class LoopContinuationDecision(TypedDict):
    outcome: LoopContinuation
    step_id: str
    requested: StepContinuationRequest
    basis: ContinuationBasis | None
    blockers: list[str]
    reason: str


StepRecordStatus = Literal["planned", "running", "waiting", "completed", "failed"]


class StepRecord(TypedDict):
    step_id: str
    loop_id: str
    workflow_run_id: str
    workflow_id: str
    node_id: str
    node_attempt: int
    index: int
    step_kind: StepKind
    status: StepRecordStatus
    intent_version: int
    input_event_id: str | None
    decision: StepDecision
    operation_ref: str | None
    operation_result_ref: str | None
    state_delta: dict[str, Any]
    postcheck_result: StepPostcheckResult | None
    started_at: str
    finished_at: str | None


class AutonomousNodeContext(TypedDict):
    goal: str
    inputs: dict[str, Any]
    completion: dict[str, Any]


class StepContext(TypedDict, total=False):
    step_id: str
    loop: LoopState
    node: AutonomousNodeContext
    event: dict[str, Any] | None
    intent: dict[str, Any]
    intent_clarity: dict[str, Any]
    autonomy_scope: dict[str, Any]
    agent_state: dict[str, Any]
    recent_steps: list[StepRecord]
    available_capabilities: dict[str, Any]
    task_plan: dict[str, Any] | None


class StepValidationError(ValueError):
    """A Brain-produced StepDecision violated the embedded Loop contract."""


class BrainIntentPatchValidationError(ValueError):
    """A Brain intent patch attempted an unsupported mutation."""


class LoopStateUpdate(TypedDict, total=False):
    loop_state: LoopState
    step_records: list[StepRecord]
    current_step: StepRecord | None
    last_continuation_decision: LoopContinuationDecision | None
    pending_trace_events: list[dict[str, Any]]
    pending_brain_intent_patch: BrainIntentPatch | None
    _extra: NotRequired[dict[str, Any]]


class PreparedStep(TypedDict):
    context: StepContext
    decision: StepDecision
    record: StepRecord


class CompletedStep(TypedDict):
    record: StepRecord
    continuation: LoopContinuationDecision
    loop: LoopState


__all__ = [
    "AskRequest",
    "AutonomousNodeContext",
    "BrainIntentPatch",
    "BrainIntentPatchValidationError",
    "CompletedStep",
    "ContinuationBasis",
    "ContinuationBasisSource",
    "HumanJudgmentAssessment",
    "HumanJudgmentTrigger",
    "LoopContinuation",
    "LoopContinuationDecision",
    "LoopState",
    "LoopStateUpdate",
    "LoopStatus",
    "PreparedStep",
    "RuntimeOperationKind",
    "RuntimeOperationProposal",
    "StepContext",
    "StepContinuationRequest",
    "StepDecision",
    "StepKind",
    "StepPostcheck",
    "StepPostcheckResult",
    "StepRecord",
    "StepRecordStatus",
    "StepValidationError",
]
