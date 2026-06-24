"""Build an initial ``HumanIntentContext`` from task input (plan N1.2).

Extraction is deterministic and never blocks: thin or ambiguous input still
produces a valid intent context with an opening ``clarify`` stage. Estimating
*how clear* that intent is — and unlocking autonomy from it — is the job of
``intent.clarity`` and ``autonomy`` (plan N2); this module only sets the
deterministic starting field.

Per spec D1, an API caller may pass an explicit partial ``HumanIntentContext``
to override or supplement the inferred field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from modi_harness._utils.ids import new_ulid
from modi_harness._utils.task_input import task_input_to_text
from modi_harness.intent.types import (
    EscalationPreference,
    HumanIntentContext,
    IntentBoundary,
    IntentStage,
    IntentStageKind,
    ResponsibilityContext,
)
from modi_harness.types import AgentProfile

# Goal text precedence. ``research_question`` leads because research is the
# first validation agent; the rest mirror the run_task text contract.
_GOAL_KEYS = ("research_question", "goal", "question", "prompt")

# Payload keys that supply working materials. Their presence (with the goal)
# means the agent has something concrete to act on, so the run can open in
# ``explore`` rather than ``clarify``.
_MATERIAL_KEYS = frozenset(
    {"source_urls", "sources", "urls", "documents", "files", "attachments", "data"}
)

# Payload keys that steer the harness rather than describe intent inputs.
_NON_INPUT_KEYS = frozenset({"messages", "tags", "reference_keys"})


def _derive_goal(task_input: Mapping[str, Any]) -> str:
    for key in _GOAL_KEYS:
        value = task_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return task_input_to_text(task_input)


def _collect_confirmed_inputs(task_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in task_input.items()
        if key not in _NON_INPUT_KEYS and value not in (None, "", [], {})
    }


def _has_materials(confirmed_inputs: Mapping[str, Any]) -> bool:
    return any(
        key in _MATERIAL_KEYS and confirmed_inputs.get(key)
        for key in confirmed_inputs
    )


def _opening_stage(*, goal: str, has_materials: bool) -> IntentStage:
    """Open in ``explore`` only when there is a goal *and* materials to act on.

    A goal with no working materials still needs clarification first.
    """
    ready_to_explore = bool(goal.strip()) and has_materials
    kind: IntentStageKind = "explore" if ready_to_explore else "clarify"
    stage_goal = (
        "act on the provided materials toward the goal"
        if ready_to_explore
        else "establish what the user wants and gather missing inputs"
    )
    return IntentStage(
        id=f"stg-{new_ulid()}",
        kind=kind,
        goal=stage_goal,
        exit_criteria=[],
        judgment_required_before_exit=False,
    )


def _boundaries_from_agent(agent: AgentProfile | None) -> list[IntentBoundary]:
    if agent is None:
        return []
    boundaries: list[IntentBoundary] = []
    for constraint in agent.get("safety_constraints", []):
        if not isinstance(constraint, str) or not constraint.strip():
            continue
        boundaries.append(
            IntentBoundary(
                id=f"b-{new_ulid()}",
                kind="risk",
                statement=constraint,
                severity="hard",
                escalation="ask",
            )
        )
    return boundaries


def _default_responsibility() -> ResponsibilityContext:
    return ResponsibilityContext(
        owner=None,
        on_behalf_of=None,
        irreversible_requires_judgment=True,
        notes=None,
    )


def _default_escalation() -> EscalationPreference:
    return EscalationPreference(default_action="ask", escalate_on=[], quiet=False)


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def extract_intent(
    task_input: Mapping[str, Any],
    *,
    agent: AgentProfile | None = None,
    override: Mapping[str, Any] | None = None,
) -> HumanIntentContext:
    """Infer a starting ``HumanIntentContext`` from ``task_input``.

    ``agent`` seeds default boundaries from the profile's safety constraints.
    ``override`` is a partial ``HumanIntentContext`` whose present keys replace
    the inferred values (spec D1).
    """
    goal = _derive_goal(task_input)
    confirmed_inputs = _collect_confirmed_inputs(task_input)
    stage = _opening_stage(goal=goal, has_materials=_has_materials(confirmed_inputs))

    ctx: HumanIntentContext = {
        "version": 1,
        "goal": goal,
        "desired_outcome": task_input.get("desired_outcome"),
        "boundaries": _boundaries_from_agent(agent),
        "non_goals": _as_str_list(task_input.get("non_goals")),
        "success_criteria": _as_str_list(task_input.get("success_criteria")),
        "current_stage": stage,
        "responsibility": _default_responsibility(),
        "escalation": _default_escalation(),
        "tradeoffs": {},
        "confirmed_inputs": confirmed_inputs,
        "decisions": [],
        "corrections": [],
    }

    if override:
        for key, value in override.items():
            if key in ctx:
                ctx[key] = value  # type: ignore[literal-required]

    return ctx


__all__ = ["extract_intent"]
