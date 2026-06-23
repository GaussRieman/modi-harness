"""Intent-aligned runtime type contracts.

These types define the human *intent field* — the durable description of what
the agent is trying to serve and how much freedom it has while serving it. They
supersede the governance-first ``HumanContext`` as the primary human-facing
runtime state (see
``docs/superpowers/specs/2026-06-23-intent-aligned-runtime-redesign.md``).

All types are ``TypedDict`` internal records, consistent with the rest of
``modi_harness.types``: they live inside the LangGraph ``AgentState`` and must
stay JSON-serializable for checkpoint/resume.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------

IntentClarityLevel = Literal["thin", "partial", "operational", "stable"]
"""How operational the current intent is. Drives autonomy derivation."""

IntentBoundaryKind = Literal[
    "scope",
    "risk",
    "data",
    "tool",
    "external_commitment",
    "quality",
    "cost",
]

BoundarySeverity = Literal["soft", "hard"]
"""``soft`` boundaries may trigger clarification/review; ``hard`` may deny."""

BoundaryEscalation = Literal["continue", "ask", "deny"]

IntentStageKind = Literal[
    "clarify",
    "explore",
    "plan",
    "execute",
    "verify",
    "deliver",
]

HumanJudgmentKind = Literal[
    "clarify",
    "approve",
    "reject",
    "revise",
    "redirect",
    "constrain",
    "cancel",
]


# ---------------------------------------------------------------------------
# Leaf records
# ---------------------------------------------------------------------------


class IntentClarity(TypedDict):
    """The runtime's estimate of how operational the current intent is.

    Model-estimated, deterministically floored (spec D2). ``unknowns`` and
    ``assumptions`` are first-class so the runtime can proceed safely on a thin
    intent without pretending it is clearer than it is.
    """

    level: IntentClarityLevel
    unknowns: list[str]
    assumptions: list[str]
    confidence: float


class IntentBoundary(TypedDict):
    """A declared edge of the intent field."""

    id: str
    kind: IntentBoundaryKind
    statement: str
    severity: BoundarySeverity
    escalation: BoundaryEscalation


class IntentStage(TypedDict):
    """The current phase of work — not a micro-task.

    Humans align stages more often than individual steps; ``TaskPlan`` sits
    below stages as agent-owned execution structure.
    """

    id: str
    kind: IntentStageKind
    goal: str
    exit_criteria: list[str]
    judgment_required_before_exit: bool


class ResponsibilityContext(TypedDict):
    """Who bears responsibility for the run's consequential actions."""

    owner: str | None
    on_behalf_of: str | None
    irreversible_requires_judgment: bool
    notes: str | None


class EscalationPreference(TypedDict):
    """How the human wants to be involved when the runtime nears a boundary."""

    default_action: BoundaryEscalation
    escalate_on: list[str]
    quiet: bool


class IntentCorrection(TypedDict):
    """A record of a human correcting drift from the intended field."""

    id: str
    created_at: str
    summary: str
    detail: str | None


class IntentPatch(TypedDict, total=False):
    """A mutation applied to ``HumanIntentContext`` by a ``HumanJudgment``.

    All keys optional. The updater (plan N6) applies present keys and bumps
    ``HumanIntentContext.version``.
    """

    goal: str
    desired_outcome: str | None
    add_boundaries: list[IntentBoundary]
    remove_boundary_ids: list[str]
    add_non_goals: list[str]
    add_success_criteria: list[str]
    set_stage: IntentStage
    confirmed_inputs: dict[str, Any]
    tradeoffs: dict[str, str]


class HumanJudgment(TypedDict):
    """Human judgment — the broad human-interaction primitive.

    Approval is one judgment ``kind``, not the whole human-interaction model.
    """

    id: str
    kind: HumanJudgmentKind
    target_action_id: str | None
    target_stage_id: str | None
    rationale: str | None
    intent_updates: IntentPatch
    created_at: str


# ---------------------------------------------------------------------------
# The intent field
# ---------------------------------------------------------------------------


class HumanIntentContext(TypedDict):
    """The durable runtime field that defines what the agent is trying to serve.

    This replaces ``HumanContext`` as the primary human-facing state. It may
    begin incomplete: a thin intent is valid state, not failure. ``confirmed_inputs``,
    ``decisions``, and ``corrections`` together subsume the old
    input/decision/feedback history.
    """

    version: int
    goal: str
    desired_outcome: str | None
    boundaries: list[IntentBoundary]
    non_goals: list[str]
    success_criteria: list[str]
    current_stage: IntentStage
    responsibility: ResponsibilityContext
    escalation: EscalationPreference
    tradeoffs: dict[str, str]
    confirmed_inputs: dict[str, Any]
    decisions: list[HumanJudgment]
    corrections: list[IntentCorrection]


__all__ = [
    "BoundaryEscalation",
    "BoundarySeverity",
    "EscalationPreference",
    "HumanIntentContext",
    "HumanJudgment",
    "HumanJudgmentKind",
    "IntentBoundary",
    "IntentBoundaryKind",
    "IntentClarity",
    "IntentClarityLevel",
    "IntentCorrection",
    "IntentPatch",
    "IntentStage",
    "IntentStageKind",
    "ResponsibilityContext",
]
