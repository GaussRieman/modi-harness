"""Constrained fast Brain rules.

Fast rules decide only the next semantic step. They do not execute operations,
mutate state, or expand into multi-step scripts.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..loop import validate_step_decision
from ..loop.types import (
    AskRequest,
    HumanJudgmentAssessment,
    InputType,
    StepContext,
    StepDecision,
)
from .types import Brain

MISSING_INPUT_RULE_ID = "fast.missing_input.clarify.v1"
STAGE_EXIT_RULE_ID = "fast.stage_exit.transition.v1"
HARD_BOUNDARY_RULE_ID = "fast.hard_boundary.wait.v1"


def _stage_kind(context: StepContext) -> str:
    stage = context.get("stage") or {}
    return str(stage.get("kind") or "")


def _required_inputs(context: StepContext) -> list[str]:
    fast_rules = _fast_rules(context)
    return [
        str(item).strip()
        for item in fast_rules.get("required_inputs", [])
        if str(item).strip()
    ]


def _fast_rules(context: StepContext) -> dict:
    spec = context.get("brain_spec") or {}
    fast_rules = spec.get("fast_rules") if isinstance(spec, dict) else None
    return fast_rules if isinstance(fast_rules, dict) else {}


def _stage_id(context: StepContext) -> str:
    stage = context.get("stage") or {}
    return str(stage.get("id") or "")


def _stage_exit_satisfied(context: StepContext) -> bool:
    event = context.get("event") or {}
    if event.get("stage_exit_criteria_satisfied") is True:
        return True
    agent_state = context.get("agent_state") or {}
    metadata = agent_state.get("metadata") if isinstance(agent_state, dict) else None
    if isinstance(metadata, dict):
        value = metadata.get("stage_exit_criteria_satisfied")
        if value is True:
            return True
    return False


def _stage_exit_transition_target(context: StepContext) -> str | None:
    if not _stage_exit_satisfied(context):
        return None
    current_kind = _stage_kind(context)
    current_id = _stage_id(context)
    transitions = _fast_rules(context).get("stage_exit_transitions") or []
    if not isinstance(transitions, list):
        return None
    for item in transitions:
        if not isinstance(item, dict):
            continue
        if item.get("when") not in (None, "exit_criteria_satisfied"):
            continue
        source = str(item.get("from") or item.get("from_stage") or "").strip()
        if source and source not in (current_kind, current_id):
            continue
        target = str(item.get("to") or item.get("to_stage") or "").strip()
        if target:
            return target
    return None


def _hard_boundary_trigger(context: StepContext) -> dict | None:
    event = context.get("event") or {}
    raw = event.get("hard_boundary_triggered")
    if raw is True:
        return {
            "id": event.get("boundary_id"),
            "reason": event.get("reason"),
        }
    if isinstance(raw, dict):
        return raw
    return None


def _missing_required_inputs(context: StepContext) -> list[str]:
    intent = context.get("intent") or {}
    confirmed = intent.get("confirmed_inputs") or {}
    if not isinstance(confirmed, dict):
        confirmed = {}
    missing: list[str] = []
    for field in _required_inputs(context):
        value = confirmed.get(field)
        if value is None or value == "" or value == []:
            missing.append(field)
    return missing


def _input_type_for_field(field: str) -> InputType:
    if field == "source_urls" or field.endswith("_urls"):
        return "url_list"
    return "text"


def missing_input_decision(context: StepContext) -> StepDecision | None:
    """Ask the human when explicit required inputs are missing."""
    missing = _missing_required_inputs(context)
    if not missing or _stage_kind(context) != "clarify":
        return None

    question = "Please provide: " + ", ".join(missing[:3])
    if len(missing) > 3:
        question += ", and any other required inputs"

    decision = StepDecision(
        id=context["step_id"],
        step_kind="clarify",
        reasoning_mode="fast",
        reason="current clarify stage is missing explicit required input",
        rule_ref=MISSING_INPUT_RULE_ID,
        intent_patch=None,
        ask=AskRequest(
            prompt=question,
            reason="BrainSpec fast rule declared required inputs",
            allowed_kinds=["clarify", "revise", "cancel"],
            field=missing[0],
            input_type=_input_type_for_field(missing[0]),
            required=True,
        ),
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="wait",
        human_judgment=HumanJudgmentAssessment(
            required=False,
            reason="missing input must be supplied by the human",
            trigger="missing_input",
        ),
        continuation_basis=None,
    )
    validate_step_decision(decision)
    return decision


def stage_exit_transition_decision(context: StepContext) -> StepDecision | None:
    """Propose a configured stage transition after explicit exit satisfaction."""
    target = _stage_exit_transition_target(context)
    if not target:
        return None

    decision = StepDecision(
        id=context["step_id"],
        step_kind="act",
        reasoning_mode="fast",
        reason="explicit stage exit criteria were satisfied",
        rule_ref=STAGE_EXIT_RULE_ID,
        intent_patch=None,
        ask=None,
        operation={
            "kind": "stage_transition",
            "summary": f"transition stage to {target}",
            "target": "transition_stage",
            "arguments": {
                "to": target,
                "rationale": "fast rule: stage exit criteria satisfied",
            },
            "expected_outcome": "stage advances after alignment and governance",
        },
        expected_state_change={"stage_id": target},
        postcheck=None,
        continuation="continue",
        human_judgment=HumanJudgmentAssessment(
            required=False,
            reason="configured stage exit rule stays within the runtime transition path",
            trigger="none",
        ),
        continuation_basis={
            "source": "stage_exit_criteria",
            "reference": STAGE_EXIT_RULE_ID,
            "reason": "continue after the configured stage transition operation",
        },
    )
    validate_step_decision(decision)
    return decision


def hard_boundary_decision(context: StepContext) -> StepDecision | None:
    """Wait for judgment when the input event explicitly names a hard boundary."""
    trigger = _hard_boundary_trigger(context)
    if trigger is None:
        return None

    boundary_id = trigger.get("id") if isinstance(trigger, dict) else None
    reason = str((trigger or {}).get("reason") or "explicit hard boundary was triggered")
    prompt = f"Human judgment required: {reason}"

    decision = StepDecision(
        id=context["step_id"],
        step_kind="handoff",
        reasoning_mode="fast",
        reason=reason,
        rule_ref=HARD_BOUNDARY_RULE_ID,
        intent_patch=None,
        ask=AskRequest(
            prompt=prompt,
            reason="BrainSpec fast rule observed an explicit hard-boundary event",
            allowed_kinds=["approve", "reject", "revise", "redirect", "constrain", "clarify", "cancel"],
        ),
        operation=None,
        expected_state_change=None,
        postcheck=None,
        continuation="wait",
        human_judgment=HumanJudgmentAssessment(
            required=True,
            reason=reason,
            trigger="boundary",
        ),
        continuation_basis={
            "source": "fast_rule",
            "reference": str(boundary_id or HARD_BOUNDARY_RULE_ID),
            "reason": "wait because an explicit hard boundary requires judgment",
        },
    )
    validate_step_decision(decision)
    return decision


@dataclass(frozen=True)
class RuleBrain:
    """Fast-rule wrapper with slow fallback."""

    fallback: Brain

    def plan_step(self, context: StepContext) -> StepDecision:
        for rule in (hard_boundary_decision, missing_input_decision, stage_exit_transition_decision):
            decision = rule(context)
            if decision is not None:
                return decision
        return self.fallback.plan_step(context)


__all__ = [
    "HARD_BOUNDARY_RULE_ID",
    "MISSING_INPUT_RULE_ID",
    "STAGE_EXIT_RULE_ID",
    "RuleBrain",
    "hard_boundary_decision",
    "missing_input_decision",
    "stage_exit_transition_decision",
]
