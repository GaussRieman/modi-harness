"""Brain-Agent Loop runtime contracts.

The first executable slice keeps these records JSON-serializable so they can
live inside LangGraph state and checkpoints. The Loop owns lifecycle state;
Brain produces ``StepDecision``; Step records explain semantic progress; runtime
operations remain below Step and are executed by the existing action path.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from ..intent.types import IntentBoundary

LoopStatus = Literal["active", "waiting", "completed", "failed", "cancelled"]

LoopContinuation = Literal[
    "continue",
    "wait_for_user",
    "wait_for_judgment",
    "complete",
    "fail",
    "cancel",
]

StepKind = Literal[
    "clarify",
    "plan",
    "observe",
    "act",
    "verify",
    "handoff",
    "finish",
]

ReasoningMode = Literal["fast", "slow"]
RuntimeOperationKind = Literal["tool", "output_finalize", "stage_transition", "memory_write"]


class LoopState(TypedDict):
    """Durable lifecycle state for one intent run."""

    loop_id: str
    run_id: str
    agent_name: str
    status: LoopStatus
    intent_version: int
    stage_id: str
    step_index: int
    max_auto_steps: int
    continuation: LoopContinuation
    last_event_id: str | None
    pending_step_id: str | None


class BrainIntentPatch(TypedDict, total=False):
    """Brain-authored intent updates.

    This is intentionally narrower than ``IntentPatch``: Brain cannot mutate
    stage directly. Stage changes must be runtime operations.
    """

    goal: str
    desired_outcome: str | None
    add_boundaries: list[IntentBoundary]
    remove_boundary_ids: list[str]
    add_non_goals: list[str]
    add_success_criteria: list[str]
    confirmed_inputs: dict[str, Any]
    tradeoffs: dict[str, str]


class RuntimeOperationProposal(TypedDict):
    """Step-level consequential operation proposed by Brain."""

    kind: RuntimeOperationKind
    summary: str
    target: str
    arguments: dict[str, Any]
    expected_outcome: str | None


HumanJudgmentTrigger = Literal[
    "none",
    "missing_input",
    "boundary",
    "stage_gate",
    "autonomy_scope",
    "operation_risk",
    "failure_recovery",
]


class HumanJudgmentAssessment(TypedDict):
    """Brain's explicit assessment of whether judgment is needed now."""

    required: bool
    reason: str
    trigger: HumanJudgmentTrigger


ContinuationBasisSource = Literal[
    "fast_rule",
    "stage_exit_criteria",
    "postcheck_result",
    "autonomy_budget",
    "slow_plan",
]


class ContinuationBasis(TypedDict):
    """Semantic basis for a Brain-requested automatic continuation."""

    source: ContinuationBasisSource
    reference: str | None
    reason: str


class StepPostcheck(TypedDict, total=False):
    """Minimal postcheck declaration for first-slice traceability."""

    conditions: list[str]
    reason: str


class StepPostcheckResult(TypedDict):
    passed: bool
    reason: str


class AskRequest(TypedDict):
    prompt: str
    reason: str
    allowed_kinds: list[str]


StepContinuationRequest = Literal["continue", "wait", "stop"]


class StepDecision(TypedDict):
    """Brain output consumed by Loop."""

    id: str
    step_kind: StepKind
    reasoning_mode: ReasoningMode
    reason: str
    rule_ref: str | None
    intent_patch: BrainIntentPatch | None
    ask: AskRequest | None
    operation: RuntimeOperationProposal | None
    expected_state_change: dict[str, Any] | None
    postcheck: StepPostcheck | None
    continuation: StepContinuationRequest
    human_judgment: HumanJudgmentAssessment
    continuation_basis: ContinuationBasis | None


class LoopContinuationDecision(TypedDict):
    """Loop's final continuation verdict after a step."""

    outcome: LoopContinuation
    step_id: str
    requested: StepContinuationRequest
    basis: ContinuationBasis | None
    blockers: list[str]
    reason: str


StepRecordStatus = Literal["planned", "running", "waiting", "completed", "failed"]


class StepRecord(TypedDict):
    """Durable audit record for one semantic step."""

    step_id: str
    loop_id: str
    run_id: str
    index: int
    step_kind: StepKind
    status: StepRecordStatus
    intent_version: int
    stage_id: str
    input_event_id: str | None
    decision: StepDecision
    operation_ref: str | None
    operation_result_ref: str | None
    state_delta: dict[str, Any]
    postcheck_result: StepPostcheckResult | None
    started_at: str
    finished_at: str | None


class StepContext(TypedDict, total=False):
    """Compact Brain input used for planning the next semantic step."""

    loop: LoopState
    event: dict[str, Any] | None
    intent: dict[str, Any]
    intent_clarity: dict[str, Any]
    autonomy_scope: dict[str, Any]
    stage: dict[str, Any]
    agent_state: dict[str, Any]
    recent_steps: list[StepRecord]
    available_capabilities: dict[str, Any]
    brain_spec: dict[str, Any] | None


class StepValidationError(ValueError):
    """A Brain-produced step decision violated the Loop contract."""


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


__all__ = [
    "AskRequest",
    "BrainIntentPatch",
    "BrainIntentPatchValidationError",
    "ContinuationBasis",
    "ContinuationBasisSource",
    "HumanJudgmentAssessment",
    "HumanJudgmentTrigger",
    "LoopContinuation",
    "LoopContinuationDecision",
    "LoopState",
    "LoopStateUpdate",
    "LoopStatus",
    "ReasoningMode",
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

