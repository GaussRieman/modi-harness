"""Pure rolling-wave planning inputs, budgets, and patch validation."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from types import MappingProxyType
from typing import Any, Literal, cast

from .._utils import compute_fingerprint
from ..workflow.components import PinnedComponent
from .graph import GraphValidationError, apply_graph_patch, ready_tasks
from .types import (
    DurableComponentInvocation,
    GraphPatch,
    LongTaskState,
    TaskGraphRun,
)
from .verification import json_value, prepare_component_invocation

PlanningTriggerKind = Literal[
    "seed",
    "expandable_ready",
    "verification_failed",
    "deadlock",
    "discovered_work",
    "goal_gap",
    "user_change",
]
PlannerInvocation = DurableComponentInvocation

ALLOWED_PLANNING_TRIGGERS = frozenset(
    {
        "seed",
        "expandable_ready",
        "verification_failed",
        "deadlock",
        "discovered_work",
        "goal_gap",
        "user_change",
    }
)

_ALLOWED_PATCH_OPERATIONS = frozenset(
    {
        "add_task",
        "add_group",
        "add_repair_task",
        "add_verification_task",
        "replace_pending_task",
        "replace_pending_group",
        "replace_dependencies",
        "set_priority",
        "set_executor_policy",
        "supersede_completed_task",
        "cancel_pending_task",
        "expand_task",
    }
)
_MAX_RECENT_RECORDS = 5
_MAX_DISCOVERED_ITEMS = 20
_MAX_STRING_LENGTH = 512
_MAX_COLLECTION_ITEMS = 20
_DISCOVERED_WORK_FIELDS = frozenset(
    {
        "source_submission_id",
        "trust_level",
        "goal",
        "rationale",
        "suggested_dependencies",
    }
)


class PlanningValidationError(ValueError):
    """A Planner input, budget, or proposed local patch is invalid."""


@dataclass(frozen=True, slots=True)
class PlanningTrigger:
    kind: PlanningTriggerKind
    target_ref: str | None = None
    reason: str | None = None
    details: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.kind not in ALLOWED_PLANNING_TRIGGERS:
            raise PlanningValidationError(f"unsupported planning trigger {self.kind!r}")
        if self.target_ref is not None and not self.target_ref.strip():
            raise PlanningValidationError("planning trigger target_ref cannot be blank")
        if self.reason is not None and not self.reason.strip():
            raise PlanningValidationError("planning trigger reason cannot be blank")
        object.__setattr__(self, "details", _freeze_mapping(_compact(self.details)))

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target_ref": self.target_ref,
            "reason": self.reason,
            "details": json_value(self.details),
        }


@dataclass(frozen=True, slots=True)
class PlanningBudgetDecision:
    allowed: bool
    may_repair: bool
    replans_remaining: int
    repairs_remaining: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PlannerPatchAssessment:
    accepted: bool
    graph: TaskGraphRun | None
    feedback: str | None
    retryable: bool
    needs_fresh_context: bool = False


def build_parent_context_projection(
    state: LongTaskState,
    trigger: PlanningTrigger,
    *,
    discovered_work: Iterable[Mapping[str, Any]] = (),
    recent_patches: Iterable[Mapping[str, Any]] = (),
    human_decisions: Iterable[Mapping[str, Any]] = (),
    authority_boundaries: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Return a bounded parent projection made only from durable summaries."""

    graph = state.graph
    if graph is None:
        raise PlanningValidationError("parent context requires a Task Graph")
    intent = next(
        (
            item
            for item in reversed(state.intents)
            if item.status == "confirmed" and item.version == graph.intent_version
        ),
        None,
    )
    if intent is None:
        raise PlanningValidationError("Task Graph has no matching confirmed Intent")

    active_task_keys = {item.key for item in graph.active_task_refs}
    active_group_keys = {item.key for item in graph.active_group_refs}
    tasks = tuple(item for item in graph.tasks if item.ref.key in active_task_keys)
    groups = tuple(item for item in graph.groups if item.ref.key in active_group_keys)
    try:
        ready = {item.ref.key for item in ready_tasks(graph)}
    except GraphValidationError:
        ready = set()

    coverage = {item.criterion_id: item for item in state.criterion_coverage}
    projection = {
        "intent": {
            "intent_id": intent.intent_id,
            "version": intent.version,
            "binding_hash": compute_fingerprint(json_value(intent)),
            "goal": intent.goal,
            "desired_outcome": intent.desired_outcome,
            "criteria": [
                {
                    "id": item.id,
                    "description": item.description,
                    "required": item.required,
                    "verification_mode": item.verification_mode,
                }
                for item in intent.success_criteria
            ],
            "constraints": list(intent.constraints),
            "non_goals": list(intent.non_goals),
            "assumptions": list(intent.assumptions),
            "authority_hash": intent.authority_hash,
        },
        "graph": {
            "graph_id": graph.graph_id,
            "revision": graph.revision,
            "status": graph.status,
            "replan_count": graph.replan_count,
            "tasks": [
                {
                    "ref": _ref_snapshot(item.ref),
                    "goal": item.goal,
                    "kind": item.kind,
                    "status": item.status,
                    "priority": item.priority,
                    "required": item.required,
                    "supports": list(item.supports),
                    "depends_on": [_ref_snapshot(ref) for ref in item.depends_on],
                    "ready": item.ref.key in ready,
                    "failure": item.failure,
                    "output_refs": list(item.output_refs),
                }
                for item in tasks
            ],
            "groups": [
                {
                    "ref": _ref_snapshot(item.ref),
                    "status": item.status,
                    "required": item.required,
                    "supports": list(item.supports),
                    "join_policy": item.join_policy,
                    "winner_task_ref": (
                        None
                        if item.winner_task_ref is None
                        else _ref_snapshot(item.winner_task_ref)
                    ),
                }
                for item in groups
            ],
        },
        "criterion_gaps": [
            {
                "criterion_id": criterion_id,
                "status": item.status if item is not None else "unsatisfied",
                "evidence_refs": list(item.evidence_refs) if item is not None else [],
            }
            for criterion_id in graph.required_criteria
            if (item := coverage.get(criterion_id)) is None
            or item.status != "satisfied"
        ],
        "committed_results": [
            {
                "task_ref": _ref_snapshot(item.ref),
                "output_refs": list(item.output_refs),
            }
            for item in tasks
            if item.status == "completed"
        ],
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "kind": item.kind,
                "uri": item.uri,
                "content_hash": item.content_hash,
                "artifact_type": item.artifact_type,
                "schema_version": item.schema_version,
                "trust_level": item.trust_level,
            }
            for item in state.artifacts
            if item.committed
        ],
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "criterion_id": item.criterion_id,
                "source_ref": item.source_ref,
                "verification_status": item.verification_status,
                "verifier_id": item.verifier_id,
            }
            for item in state.evidence_records
        ],
        "recent_patches": _recent_summaries(recent_patches),
        "human_decisions": _recent_summaries(human_decisions),
        "budgets": {
            "max_tasks": graph.limits.max_tasks,
            "active_tasks": len(tasks),
            "max_graph_depth": graph.limits.max_graph_depth,
            "max_replans": graph.limits.max_replans,
            "replans_used": graph.replan_count,
            "replans_remaining": max(
                0, graph.limits.max_replans - graph.replan_count
            ),
            "max_child_runs": graph.limits.max_child_runs,
            "child_runs_used": sum(
                1
                for item in state.attempts
                if item.executor_binding.mode == "child_agent"
            ),
            "max_concurrency": graph.limits.max_concurrency,
        },
        "authority_boundaries": _compact(authority_boundaries or {}),
        "trigger": trigger.snapshot(),
        "discovered_work": _discovered_summaries(discovered_work),
    }
    return _freeze_mapping(cast(Mapping[str, Any], _compact(projection, depth=6)))


def normalize_discovered_work(
    suggestions: Iterable[Mapping[str, Any]],
    *,
    source_submission_id: str,
    max_items: int = _MAX_DISCOVERED_ITEMS,
) -> tuple[Mapping[str, Any], ...]:
    """Whitelist child suggestions as untrusted data, never as patch operations."""

    if not source_submission_id.strip():
        raise PlanningValidationError("discovered work requires a submission ID")
    if max_items < 0:
        raise PlanningValidationError("max_items cannot be negative")
    normalized: list[Mapping[str, Any]] = []
    for index, item in enumerate(suggestions):
        if index >= max_items:
            break
        if not isinstance(item, Mapping):
            raise PlanningValidationError("discovered work item must be a mapping")
        goal = item.get("goal")
        rationale = item.get("rationale", "")
        dependencies = item.get("suggested_dependencies", ())
        if not isinstance(goal, str) or not goal.strip():
            raise PlanningValidationError("discovered work goal must be non-empty")
        if not isinstance(rationale, str):
            raise PlanningValidationError("discovered work rationale must be a string")
        if not isinstance(dependencies, tuple | list):
            raise PlanningValidationError(
                "discovered work suggested_dependencies must be an array"
            )
        normalized.append(
            _freeze_mapping(
                {
                    "source_submission_id": source_submission_id,
                    "trust_level": "untrusted",
                    "goal": _compact(goal),
                    "rationale": _compact(rationale),
                    "suggested_dependencies": _compact(dependencies),
                }
            )
        )
    return tuple(normalized)


def decide_planning_budget(
    graph: TaskGraphRun,
    trigger: PlanningTrigger,
    *,
    repair_attempt: int,
    max_repair_attempts: int,
) -> PlanningBudgetDecision:
    """Decide whether this exact Planner call and a later repair are allowed."""

    if repair_attempt < 0 or max_repair_attempts < 0:
        raise PlanningValidationError("Planner repair budgets cannot be negative")
    replans_remaining = max(0, graph.limits.max_replans - graph.replan_count)
    repairs_remaining = max(0, max_repair_attempts - repair_attempt)
    if trigger.kind == "seed":
        replan_allowed = graph.revision == 0
        replan_reason = None if replan_allowed else "seed requires graph revision zero"
    else:
        replan_allowed = graph.replan_count < graph.limits.max_replans
        replan_reason = None if replan_allowed else "replan budget exhausted"
    repair_allowed = repair_attempt <= max_repair_attempts
    reason = replan_reason
    if not repair_allowed:
        reason = "Planner repair budget exhausted"
    return PlanningBudgetDecision(
        allowed=replan_allowed and repair_allowed,
        may_repair=replan_allowed and repair_attempt < max_repair_attempts,
        replans_remaining=replans_remaining,
        repairs_remaining=repairs_remaining,
        reason=reason,
    )


def prepare_planner_invocation(
    planner: PinnedComponent,
    *,
    root_run_id: str,
    graph: TaskGraphRun,
    trigger: PlanningTrigger,
    context: Mapping[str, Any],
    repair_attempt: int = 0,
) -> PlannerInvocation:
    """Prepare the durable record that must be committed before Planner execution."""

    if planner.kind != "planner":
        raise PlanningValidationError("Planner invocation requires a planner component")
    if repair_attempt < 0:
        raise PlanningValidationError("repair_attempt cannot be negative")
    inputs = {"context": json_value(context), "trigger": trigger.snapshot()}
    input_hash = compute_fingerprint(inputs)
    idempotency_key = (
        f"root/{root_run_id}/graph/{graph.revision}/trigger/{trigger.kind}/"
        f"input/{input_hash}/repair/{repair_attempt}"
    )
    return prepare_component_invocation(
        planner,
        kind="planner",
        idempotency_key=idempotency_key,
        inputs=inputs,
    )


def validate_planner_patch(
    graph: TaskGraphRun,
    trigger: PlanningTrigger,
    proposal: object,
) -> TaskGraphRun:
    """Validate and apply one typed incremental proposal without mutating input."""

    if not isinstance(proposal, GraphPatch):
        raise PlanningValidationError(
            "Planner must return an incremental GraphPatch, not a graph snapshot"
        )
    if proposal.trigger != trigger.kind:
        raise PlanningValidationError("GraphPatch trigger does not match Planner invocation")
    if not proposal.reason.strip():
        raise PlanningValidationError("GraphPatch reason cannot be blank")
    if not proposal.operations:
        raise PlanningValidationError("GraphPatch must contain at least one operation")
    unsupported = sorted(
        {item.op for item in proposal.operations} - _ALLOWED_PATCH_OPERATIONS
    )
    if unsupported:
        raise PlanningValidationError(
            f"unsupported GraphPatch operation: {', '.join(unsupported)}"
        )
    try:
        return apply_graph_patch(graph, proposal)
    except GraphValidationError as exc:
        raise PlanningValidationError(str(exc)) from exc


def assess_planner_patch(
    graph: TaskGraphRun,
    trigger: PlanningTrigger,
    proposal: object,
    *,
    repair_attempt: int,
    max_repair_attempts: int,
) -> PlannerPatchAssessment:
    """Return deterministic model feedback without changing the current graph."""

    budget = decide_planning_budget(
        graph,
        trigger,
        repair_attempt=repair_attempt,
        max_repair_attempts=max_repair_attempts,
    )
    if not budget.allowed:
        return PlannerPatchAssessment(False, None, budget.reason, False)
    try:
        updated = validate_planner_patch(graph, trigger, proposal)
    except PlanningValidationError as exc:
        feedback = str(exc)
        stale = feedback.startswith("stale graph revision")
        return PlannerPatchAssessment(
            False,
            None,
            feedback,
            budget.may_repair,
            needs_fresh_context=stale,
        )
    return PlannerPatchAssessment(True, updated, None, False)


def _ref_snapshot(ref: Any) -> dict[str, Any]:
    return {"kind": ref.kind, "id": ref.id, "revision": ref.revision}


def _recent_summaries(items: Iterable[Mapping[str, Any]]) -> list[Any]:
    values = deque(items, maxlen=_MAX_RECENT_RECORDS)
    return [_compact(item) for item in values]


def _discovered_summaries(items: Iterable[Mapping[str, Any]]) -> list[Any]:
    summaries: list[Any] = []
    for item in islice(items, _MAX_DISCOVERED_ITEMS):
        if item.get("trust_level") != "untrusted":
            raise PlanningValidationError(
                "discovered work must be normalized as untrusted input"
            )
        if set(item) - _DISCOVERED_WORK_FIELDS:
            raise PlanningValidationError(
                "discovered work contains non-whitelisted fields"
            )
        summaries.append(_compact(item))
    return summaries


def _compact(value: Any, *, depth: int = 3) -> Any:
    if depth < 0:
        return "<omitted>"
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_LENGTH:
            return value
        return value[:_MAX_STRING_LENGTH] + "…"
    if isinstance(value, Mapping):
        return {
            str(key): _compact(item, depth=depth - 1)
            for key, item in islice(value.items(), _MAX_COLLECTION_ITEMS)
        }
    if isinstance(value, tuple | list):
        return [
            _compact(item, depth=depth - 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, tuple | list):
        return tuple(_freeze(item) for item in value)
    return value


__all__ = [
    "ALLOWED_PLANNING_TRIGGERS",
    "PlannerInvocation",
    "PlannerPatchAssessment",
    "PlanningBudgetDecision",
    "PlanningTrigger",
    "PlanningTriggerKind",
    "PlanningValidationError",
    "assess_planner_patch",
    "build_parent_context_projection",
    "decide_planning_budget",
    "normalize_discovered_work",
    "prepare_planner_invocation",
    "validate_planner_patch",
]
