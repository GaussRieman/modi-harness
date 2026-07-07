"""Small helpers for the first Brain-Agent Loop runtime slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .._utils import new_ulid, now_iso
from .types import (
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
_STAGE_PATCH_KEYS = frozenset({"set_stage", "stage", "current_stage", "stage_id"})


@dataclass(frozen=True)
class AgentLoop:
    """Lifecycle controller for one intent run.

    The first object slice keeps graph-specific trace and model execution
    outside the Loop. The Loop owns the semantic control boundaries:
    build context, ask Brain for a decision, create a StepRecord, complete it,
    decide continuation, and advance LoopState.
    """

    state: LoopState
    brain: Brain

    def prepare_step(
        self,
        *,
        step_id: str,
        event: dict[str, Any] | None,
        intent: Mapping[str, Any] | None,
        intent_clarity: Mapping[str, Any] | None,
        autonomy_scope: Mapping[str, Any] | None,
        agent_profile: Mapping[str, Any],
        recent_steps: list[StepRecord],
        available_capabilities: dict[str, Any],
        brain_spec: dict[str, Any] | None = None,
        input_event_id: str | None = None,
    ) -> PreparedStep:
        context = build_step_context(
            step_id=step_id,
            loop=self.state,
            event=event,
            intent=intent,
            intent_clarity=intent_clarity,
            autonomy_scope=autonomy_scope,
            agent_profile=agent_profile,
            recent_steps=recent_steps,
            available_capabilities=available_capabilities,
            brain_spec=brain_spec,
        )
        decision = self.brain.plan_step(context)
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
        completed = complete_step_record(
            record,
            status=status,
            state_delta=state_delta,
        )
        continuation = decide_loop_continuation(loop=self.state, step=completed)
        loop = advance_loop_state(
            loop=self.state,
            step=completed,
            continuation=continuation,
        )
        return CompletedStep(
            record=completed,
            continuation=continuation,
            loop=loop,
        )


def initialize_loop_state(
    *,
    run_id: str,
    agent_name: str,
    intent_version: int,
    stage_id: str,
    max_auto_steps: int,
) -> LoopState:
    """Create the first durable loop state for a run."""
    return LoopState(
        loop_id=new_ulid(),
        run_id=run_id,
        agent_name=agent_name,
        status="active",
        intent_version=intent_version,
        stage_id=stage_id,
        step_index=0,
        max_auto_steps=max_auto_steps,
        continuation="continue",
        last_event_id=None,
        pending_step_id=None,
    )


def slow_model_step_decision(
    *,
    step_id: str,
    reason: str = "existing model turn wrapped as slow Brain behavior",
) -> StepDecision:
    """Build the first-slice StepDecision for the existing model_turn path."""
    decision = StepDecision(
        id=step_id,
        step_kind="plan",
        reasoning_mode="slow",
        reason=reason,
        rule_ref=None,
        intent_patch=None,
        ask=None,
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="continue",
        human_judgment=HumanJudgmentAssessment(
            required=False,
            reason="model planning stays inside the current autonomy scope",
            trigger="none",
        ),
        continuation_basis=ContinuationBasis(
            source="slow_plan",
            reference=None,
            reason="continue after obtaining the model's next planning result",
        ),
    )
    validate_step_decision(decision)
    return decision


def build_step_context(
    *,
    step_id: str,
    loop: LoopState,
    event: dict[str, Any] | None,
    intent: Mapping[str, Any] | None,
    intent_clarity: Mapping[str, Any] | None,
    autonomy_scope: Mapping[str, Any] | None,
    agent_profile: Mapping[str, Any],
    recent_steps: list[StepRecord],
    available_capabilities: dict[str, Any],
    brain_spec: dict[str, Any] | None = None,
) -> StepContext:
    """Construct the compact Brain planning input for the next step."""
    stage: dict[str, Any] = {}
    if intent is not None:
        maybe_stage = intent.get("current_stage")
        if isinstance(maybe_stage, dict):
            stage = dict(maybe_stage)

    agent_state = {
        "agent_name": agent_profile.get("name", loop["agent_name"]),
        "description": agent_profile.get("description", ""),
        "default_tools": list(agent_profile.get("default_tools") or []),
        "default_skills": list(agent_profile.get("default_skills") or []),
        "permission_profile": agent_profile.get("permission_profile"),
        "output_contract": agent_profile.get("output_contract"),
        "metadata": dict(agent_profile.get("metadata") or {}),
    }

    return StepContext(
        step_id=step_id,
        loop=loop,
        event=event,
        intent=dict(intent or {}),
        intent_clarity=dict(intent_clarity or {}),
        autonomy_scope=dict(autonomy_scope or {}),
        stage=stage,
        agent_state=agent_state,
        recent_steps=list(recent_steps),
        available_capabilities=dict(available_capabilities),
        brain_spec=brain_spec,
    )


def validate_brain_intent_patch(patch: BrainIntentPatch | None) -> None:
    """Reject stage or unknown keys in Brain-authored intent patches."""
    if not patch:
        return
    keys = set(patch.keys())
    stage_keys = keys & _STAGE_PATCH_KEYS
    if stage_keys:
        joined = ", ".join(sorted(stage_keys))
        raise BrainIntentPatchValidationError(
            f"BrainIntentPatch cannot mutate stage fields: {joined}"
        )
    unknown = keys - _ALLOWED_BRAIN_PATCH_KEYS
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise BrainIntentPatchValidationError(
            f"BrainIntentPatch contains unsupported keys: {joined}"
        )


def validate_step_decision(decision: StepDecision) -> None:
    """Validate first-slice StepDecision invariants."""
    validate_brain_intent_patch(decision.get("intent_patch"))

    if decision["ask"] is not None and decision["operation"] is not None:
        raise StepValidationError("StepDecision cannot carry both ask and operation")

    if decision["step_kind"] == "finish" and (
        decision["ask"] is not None or decision["operation"] is not None
    ):
        raise StepValidationError("finish StepDecision cannot carry ask or operation")

    human = decision["human_judgment"]
    if human["required"] and decision["operation"] is not None:
        raise StepValidationError(
            "StepDecision requiring human judgment cannot carry operation"
        )
    if human["required"] and decision["ask"] is None and decision["continuation"] != "wait":
        raise StepValidationError(
            "StepDecision requiring human judgment must ask or wait"
        )
    if not human["required"] and not human["reason"].strip():
        raise StepValidationError(
            "StepDecision without required judgment must explain why"
        )

    if decision["continuation"] == "continue" and decision["continuation_basis"] is None:
        raise StepValidationError(
            "StepDecision requesting continue must include continuation_basis"
        )


def begin_step_record(
    *,
    loop: LoopState,
    decision: StepDecision,
    input_event_id: str | None = None,
) -> StepRecord:
    """Create a planned StepRecord for ``decision``."""
    validate_step_decision(decision)
    return StepRecord(
        step_id=decision["id"],
        loop_id=loop["loop_id"],
        run_id=loop["run_id"],
        index=loop["step_index"] + 1,
        step_kind=decision["step_kind"],
        status="planned",
        intent_version=loop["intent_version"],
        stage_id=loop["stage_id"],
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
    """Return a completed copy of ``record``."""
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
    """Compute the Loop's final continuation verdict for a completed step."""
    decision = step["decision"]
    blockers: list[str] = []

    if decision["human_judgment"]["required"]:
        blockers.append("human_judgment_required")
    if decision["ask"] is not None:
        blockers.append("ask_pending")
    if loop["step_index"] + 1 >= loop["max_auto_steps"]:
        blockers.append("max_auto_steps_reached")
    if step["status"] == "failed":
        blockers.append("step_failed")

    requested = decision["continuation"]
    if requested == "stop":
        outcome: LoopContinuation = "complete"
        reason = "Brain requested stop"
    elif blockers:
        if "human_judgment_required" in blockers:
            outcome = "wait_for_judgment"
        elif "ask_pending" in blockers:
            outcome = "wait_for_user"
        elif "step_failed" in blockers:
            outcome = "fail"
        else:
            outcome = "wait_for_user"
        reason = "; ".join(blockers)
    else:
        outcome = "continue" if requested == "continue" else "wait_for_user"
        reason = decision["continuation_basis"]["reason"] if decision["continuation_basis"] else "Brain requested wait"

    return LoopContinuationDecision(
        outcome=outcome,
        step_id=step["step_id"],
        requested=requested,
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
    """Advance LoopState after a step boundary."""
    status = loop["status"]
    if continuation["outcome"] in ("wait_for_user", "wait_for_judgment"):
        status = "waiting"
    elif continuation["outcome"] == "complete":
        status = "completed"
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
    updated["stage_id"] = step["stage_id"]
    return updated


__all__ = [
    "AgentLoop",
    "advance_loop_state",
    "begin_step_record",
    "build_step_context",
    "complete_step_record",
    "decide_loop_continuation",
    "initialize_loop_state",
    "slow_model_step_decision",
    "validate_brain_intent_patch",
    "validate_step_decision",
]
