"""Stages as the runtime alignment layer above task plans (plan N7).

A **stage** is the phase of work the agent is in — ``clarify``, ``explore``,
``plan``, ``execute``, ``verify``, ``deliver`` — not a micro-task. Humans align
on stages far more often than on individual tool calls, so the runtime treats a
stage transition as a consequential, alignment-relevant action. ``TaskPlan``
stays beneath stages as agent-owned execution structure.

Two responsibilities live here:

1. **``assess_transition``** — the deterministic *floor* the ``AlignmentKernel``
   consults for a ``stage_transition`` proposal. Consistent with the model-first
   rule, it never lowers the model's verdict: it only returns escalations that
   *raise* severity when a transition crosses a structural line —
   - the target is unknown or outside the current autonomy scope → ``ask_judgment``;
   - the current stage declared ``judgment_required_before_exit`` → ``ask_judgment``;
   - entering a *committing* stage (``deliver``) with no declared success
     criteria → ``ask_judgment`` (deliver should not happen until the human's
     coverage bar exists to judge against).
   The model judge remains primary; the floor only proves these red lines.

2. **Explainers** — ``explain_action_stage`` / ``explain_transition`` answer the
   N7 exit-gate questions in plain language: which stage an action belongs to,
   and why a stage transition was allowed or interrupted.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from .._utils import new_ulid
from .types import HumanIntentContext, IntentStage, IntentStageKind

# Canonical forward order of stages. Used to reason about direction and to keep
# the set of known kinds in one place.
STAGE_ORDER: tuple[IntentStageKind, ...] = (
    "clarify",
    "explore",
    "plan",
    "execute",
    "verify",
    "deliver",
)

# Stages that *commit* to the human-visible world. Entering one is where the run
# stops being internal work and starts producing the delivered result, so the
# runtime wants the human's success bar to exist before crossing in.
_COMMITTING_STAGES: frozenset[IntentStageKind] = frozenset({"deliver"})

_DEFAULT_GOALS: dict[IntentStageKind, str] = {
    "clarify": "establish what the user wants and gather missing inputs",
    "explore": "act on the provided materials toward the goal",
    "plan": "decide the approach before doing consequential work",
    "execute": "carry out the planned work",
    "verify": "check the work against the success criteria",
    "deliver": "finalize and hand back the result",
}

_DEFAULT_EXIT_CRITERIA: dict[IntentStageKind, list[str]] = {
    "clarify": ["the goal is clear enough to act on"],
    "explore": ["enough material has been gathered to plan or act"],
    "plan": ["an approach the human would recognize is chosen"],
    "execute": ["the planned work is done"],
    "verify": ["the work has been checked against the success criteria"],
    # deliver is terminal — nothing follows it, so it has no forward exit gate.
    "deliver": [],
}

# Argument keys an agent might use to name the stage it wants to move to.
_TARGET_KEYS = ("to", "to_stage", "stage", "target", "kind")


# ---------------------------------------------------------------------------
# stage model
# ---------------------------------------------------------------------------


def default_exit_criteria(kind: IntentStageKind) -> list[str]:
    """Per-kind default exit criteria (a fresh list each call)."""
    return list(_DEFAULT_EXIT_CRITERIA.get(kind, []))


def build_stage(kind: IntentStageKind, **over: Any) -> IntentStage:
    """Build an ``IntentStage`` for ``kind`` with sensible per-kind defaults.

    Override any field by keyword (``goal``, ``exit_criteria``, ``id``,
    ``judgment_required_before_exit``).
    """
    stage = IntentStage(
        id=over.get("id", f"stg-{new_ulid()}"),
        kind=kind,
        goal=over.get("goal", _DEFAULT_GOALS.get(kind, "")),
        exit_criteria=list(over["exit_criteria"])
        if "exit_criteria" in over
        else default_exit_criteria(kind),
        judgment_required_before_exit=bool(over.get("judgment_required_before_exit", False)),
    )
    return stage


def target_stage_kind(proposal: Mapping[str, Any]) -> IntentStageKind | None:
    """Read the stage a transition proposal wants to move to, or ``None``.

    Tolerant of the several argument shapes an agent may emit; an unrecognized
    or missing target is reported as ``None`` rather than guessed.
    """
    args = proposal.get("arguments") or {}
    for key in _TARGET_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value in STAGE_ORDER:
            return value
    return None


# ---------------------------------------------------------------------------
# the deterministic floor
# ---------------------------------------------------------------------------


class StageEscalation(TypedDict):
    """A single floor escalation: raise the verdict to ``verdict`` because ``reason``."""

    verdict: str
    reason: str


def assess_transition(
    *,
    proposal: Mapping[str, Any],
    intent: HumanIntentContext,
    scope: Mapping[str, Any],
) -> list[StageEscalation]:
    """Structural floor for a ``stage_transition`` proposal (escalate-only).

    Returns the escalations the kernel must apply on top of the model verdict.
    An empty list means the floor sees no structural reason to raise severity —
    the model's judgment stands.
    """
    escalations: list[StageEscalation] = []
    target = target_stage_kind(proposal)

    # 1 — the target must be a stage the runtime understands.
    if target is None:
        escalations.append(
            StageEscalation(
                verdict="ask_judgment",
                reason="stage transition target is unknown or unspecified",
            )
        )
        # Without a known target the rest cannot be reasoned about.
        return escalations

    # 2 — the target must be inside the current autonomy scope.
    allowed = list(scope.get("allowed_stages") or [])
    if allowed and target not in allowed:
        mode = scope.get("mode", "current")
        escalations.append(
            StageEscalation(
                verdict="ask_judgment",
                reason=(
                    f"stage {target!r} is outside the {mode} autonomy scope "
                    f"(allowed: {', '.join(allowed)})"
                ),
            )
        )

    # 3 — the stage being left may require judgment before exit.
    current = intent["current_stage"]
    if current.get("judgment_required_before_exit") and current["kind"] != target:
        escalations.append(
            StageEscalation(
                verdict="ask_judgment",
                reason=f"stage {current['kind']!r} requires human judgment before exit",
            )
        )

    # 4 — entering a committing stage needs the human's success bar to exist.
    if target in _COMMITTING_STAGES and not intent["success_criteria"]:
        escalations.append(
            StageEscalation(
                verdict="ask_judgment",
                reason=(
                    f"entering {target!r} requires declared success criteria to "
                    "judge coverage against"
                ),
            )
        )

    return escalations


# ---------------------------------------------------------------------------
# explainers (the N7 exit gate)
# ---------------------------------------------------------------------------


def explain_action_stage(
    *, proposal: Mapping[str, Any], intent: HumanIntentContext
) -> str:
    """Explain which stage an action belongs to (and, for a transition, its target)."""
    stage = intent["current_stage"]
    base = (
        f"action {proposal.get('summary') or proposal.get('tool_name')!r} belongs "
        f"to stage {stage['kind']!r} ({stage['id']})"
    )
    if proposal.get("kind") == "stage_transition":
        target = target_stage_kind(proposal)
        base += f"; it proposes moving to stage {target!r}"
    return base


def explain_transition(
    *,
    proposal: Mapping[str, Any],
    intent: HumanIntentContext,
    scope: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    """Explain why a stage transition was allowed or interrupted.

    Combines the kernel's final ``decision['decision']`` with the structural
    reasons the floor would raise, so a maintainer can read one line and know
    both the verdict and what drove it.
    """
    current = intent["current_stage"]
    target = target_stage_kind(proposal)
    verdict = decision.get("decision", "unknown")
    head = (
        f"transition {current['kind']!r} -> {target!r}: {verdict}"
    )
    floor = assess_transition(proposal=proposal, intent=intent, scope=scope)
    if floor:
        head += " — " + "; ".join(e["reason"] for e in floor)
    elif decision.get("reason"):
        head += f" — {decision['reason']}"
    return head


__all__ = [
    "STAGE_ORDER",
    "StageEscalation",
    "assess_transition",
    "build_stage",
    "default_exit_criteria",
    "explain_action_stage",
    "explain_transition",
    "target_stage_kind",
]
