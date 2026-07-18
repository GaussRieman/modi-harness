"""Pure Intent clarification, patch classification, and rebase planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, cast

from .._utils import compute_fingerprint
from .types import (
    DependencyRef,
    GroupChildRef,
    GroupRun,
    IntentVersion,
    LongTaskState,
    TaskRun,
)

IntentImpact = Literal["low", "high"]
PatchImpact = Literal["local", "material"]
AuthorityEffect = Literal["none", "narrow", "expand"]
ReuseProofStatus = Literal["passed", "failed"]
BindingDecision = Literal["retained", "invalidated"]
JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = (
    JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]
)

_MATERIAL_OPS = frozenset(
    {
        "add_required_criterion",
        "add_success_criterion",
        "change_authority",
        "change_budget",
        "change_constraints",
        "change_desired_outcome",
        "change_direction",
        "change_goal",
        "change_high_impact_assumption",
        "change_risk",
        "remove_required_criterion",
        "remove_success_criterion",
        "replace_required_criterion",
        "replace_success_criterion",
        "set_authority",
        "set_budget",
        "set_desired_outcome",
        "set_goal",
    }
)
_LOCAL_OPS = frozenset(
    {
        "change_implementation_strategy",
        "equivalent_executor_switch",
        "local_graph_repair",
        "replace_dependencies",
        "retry_strategy",
        "set_executor_policy",
        "set_priority",
    }
)
_ACTIVE_ATTEMPT_STATUSES = frozenset(
    {"created", "leased", "running", "waiting", "submitted"}
)


class IntentValidationError(ValueError):
    """An Intent clarification, confirmation, or patch is invalid."""


class IntentRebaseError(IntentValidationError):
    """A pure Intent rebase plan cannot be constructed safely."""


@dataclass(frozen=True, slots=True)
class IntentQuestion:
    """One clarification question ordered by execution impact."""

    id: str
    question: str
    impact: IntentImpact
    answer: JSONValue = None

    def __post_init__(self) -> None:
        _non_empty(self.id, "question id")
        _non_empty(self.question, "question")
        _validate_impact(self.impact)
        object.__setattr__(self, "answer", _freeze_json(self.answer))

    @property
    def resolved(self) -> bool:
        return self.answer is not None

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class IntentAssumption:
    """An explicit assumption that may remain unconfirmed only when low impact."""

    id: str
    value: JSONValue
    impact: IntentImpact
    confirmed: bool = False

    def __post_init__(self) -> None:
        _non_empty(self.id, "assumption id")
        _validate_impact(self.impact)
        object.__setattr__(self, "value", _freeze_json(self.value))

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class ClarificationAssessment:
    """Deterministic gate result for Intent confirmation."""

    can_confirm: bool
    next_question: IntentQuestion | None
    blocking_question_ids: tuple[str, ...]
    blocking_assumption_ids: tuple[str, ...]
    retained_assumptions: tuple[IntentAssumption, ...]

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class IntentConfirmation:
    """Human confirmation bound to one exact immutable Intent fingerprint."""

    intent_id: str
    intent_version: int
    intent_fingerprint: str
    confirmed_by: str = "human"

    def __post_init__(self) -> None:
        _non_empty(self.intent_id, "confirmation intent_id")
        _positive(self.intent_version, "confirmation intent_version")
        _non_empty(self.intent_fingerprint, "confirmation intent_fingerprint")
        _non_empty(self.confirmed_by, "confirmation confirmed_by")

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class IntentPatchChange:
    """One typed change in an immutable IntentPatch."""

    op: str
    target: str
    value: JSONValue = None
    impact: PatchImpact = "material"
    authority_effect: AuthorityEffect = "none"

    def __post_init__(self) -> None:
        _non_empty(self.op, "IntentPatch change op")
        _non_empty(self.target, "IntentPatch change target")
        if self.impact not in {"local", "material"}:
            raise IntentValidationError(f"unsupported patch impact {self.impact!r}")
        if self.authority_effect not in {"none", "narrow", "expand"}:
            raise IntentValidationError(
                f"unsupported authority effect {self.authority_effect!r}"
            )
        object.__setattr__(self, "value", _freeze_json(self.value))

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class IntentPatch:
    """A version-bound set of typed Intent changes."""

    base_version: int
    reason: str
    changes: tuple[IntentPatchChange, ...]
    patch_id: str = ""

    def __post_init__(self) -> None:
        _positive(self.base_version, "IntentPatch base_version")
        _non_empty(self.reason, "IntentPatch reason")
        if not self.changes:
            raise IntentValidationError("IntentPatch must contain at least one change")
        object.__setattr__(self, "changes", tuple(self.changes))

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class IntentPatchClassification:
    """Materiality and authority result derived from a proposed Intent version."""

    impact: PatchImpact
    requires_confirmation: bool
    authority_effect: AuthorityEffect
    reasons: tuple[str, ...]

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class RebaseReuseProof:
    """Pinned validator result bound to the exact old object and new Intent."""

    record_id: str
    target_ref: DependencyRef
    prior_intent_version: int
    new_intent_version: int
    intent_binding_hash: str
    dependency_refs: tuple[DependencyRef, ...]
    completion_contract_hash: str
    reusable: bool
    status: ReuseProofStatus = "passed"
    validator_fingerprint: str = ""
    new_intent_fingerprint: str = ""

    def __post_init__(self) -> None:
        _non_empty(self.record_id, "reuse proof record_id")
        _positive(self.prior_intent_version, "reuse proof prior_intent_version")
        _positive(self.new_intent_version, "reuse proof new_intent_version")
        _non_empty(self.intent_binding_hash, "reuse proof intent_binding_hash")
        _non_empty(
            self.completion_contract_hash,
            "reuse proof completion_contract_hash",
        )
        if self.status not in {"passed", "failed"}:
            raise IntentValidationError(f"unsupported reuse proof status {self.status!r}")
        object.__setattr__(self, "dependency_refs", tuple(self.dependency_refs))

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class RebaseBindingDecision:
    """How one old active object is bound after a rebase."""

    target_ref: DependencyRef
    decision: BindingDecision
    replacement_ref: DependencyRef | None
    reason: str
    proof_record_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class RebaseCancellationPlan:
    """The exact lease and resource fence required by the future root CAS."""

    attempt_id: str
    task_ref: DependencyRef
    lease_epoch: int
    lease_token: str
    resource_keys: tuple[str, ...]
    reason: str

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


@dataclass(frozen=True, slots=True)
class IntentRebasePlan:
    """A complete immutable description of a future atomic root-state CAS."""

    expected_root_revision: int
    expected_graph_revision: int
    next_graph_revision: int
    prior_intent: IntentVersion
    superseded_intent: IntentVersion
    new_intent: IntentVersion
    patch: IntentPatch
    classification: IntentPatchClassification
    task_revisions_to_append: tuple[TaskRun, ...]
    group_revisions_to_append: tuple[GroupRun, ...]
    active_task_refs: tuple[DependencyRef, ...]
    active_group_refs: tuple[DependencyRef, ...]
    binding_decisions: tuple[RebaseBindingDecision, ...]
    cancellations: tuple[RebaseCancellationPlan, ...]
    reset_criterion_ids: tuple[str, ...]

    @property
    def append_tasks(self) -> tuple[TaskRun, ...]:
        return self.task_revisions_to_append

    @property
    def append_groups(self) -> tuple[GroupRun, ...]:
        return self.group_revisions_to_append

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


def assess_intent_clarification(
    questions: Sequence[IntentQuestion],
    assumptions: Sequence[IntentAssumption],
) -> ClarificationAssessment:
    """Allow confirmation only when no high-impact uncertainty is unresolved."""

    unresolved = tuple(
        item for item in questions if item.impact == "high" and not item.resolved
    )
    unconfirmed = tuple(
        item
        for item in assumptions
        if item.impact == "high" and not item.confirmed
    )
    return ClarificationAssessment(
        can_confirm=not unresolved and not unconfirmed,
        next_question=unresolved[0] if unresolved else None,
        blocking_question_ids=tuple(item.id for item in unresolved),
        blocking_assumption_ids=tuple(item.id for item in unconfirmed),
        retained_assumptions=tuple(assumptions),
    )


def intent_fingerprint(intent: IntentVersion) -> str:
    """Return the canonical fingerprint used by confirmation and rebasing."""

    return compute_fingerprint(_plain(intent))


def validate_intent_confirmation(
    intent: IntentVersion,
    confirmation: IntentConfirmation,
) -> None:
    """Reject a confirmation not bound to the exact confirmed Intent version."""

    if intent.status != "confirmed":
        raise IntentValidationError("Intent confirmation requires confirmed status")
    if confirmation.intent_id != intent.intent_id:
        raise IntentValidationError("confirmation Intent id does not match")
    if confirmation.intent_version != intent.version:
        raise IntentValidationError("confirmation Intent version does not match")
    if confirmation.intent_fingerprint != intent_fingerprint(intent):
        raise IntentValidationError("confirmation Intent fingerprint does not match")


def classify_intent_patch(
    current_intent: IntentVersion,
    proposed_intent: IntentVersion,
    patch: IntentPatch,
) -> IntentPatchClassification:
    """Classify the semantic change and enforce the authority ceiling."""

    _validate_patch_versions(current_intent, proposed_intent, patch)
    if any(change.authority_effect == "expand" for change in patch.changes):
        raise IntentValidationError("IntentPatch cannot expand execution authority")

    reasons: list[str] = []
    if current_intent.goal != proposed_intent.goal:
        reasons.append("goal changed")
    if current_intent.desired_outcome != proposed_intent.desired_outcome:
        reasons.append("desired outcome changed")
    if _required_criteria(current_intent) != _required_criteria(proposed_intent):
        reasons.append("required success criteria changed")
    if current_intent.constraints != proposed_intent.constraints:
        reasons.append("constraints changed")
    if current_intent.non_goals != proposed_intent.non_goals:
        reasons.append("non-goals changed")
    if current_intent.assumptions != proposed_intent.assumptions:
        reasons.append("assumptions changed")

    authority_targets = tuple(
        change for change in patch.changes if _authority_target(change)
    )
    authority_changes = tuple(
        change
        for change in authority_targets
        if change.authority_effect == "narrow"
    )
    if current_intent.authority_hash != proposed_intent.authority_hash:
        if not authority_changes or any(
            change.authority_effect != "narrow" for change in authority_targets
        ):
            raise IntentValidationError(
                "authority hash changed without an explicit narrowing change"
            )
        reasons.append("authority narrowed")
    elif authority_targets:
        if not authority_changes:
            raise IntentValidationError(
                "authority changes must explicitly declare a narrowing effect"
            )

    for change in patch.changes:
        if change.impact == "material" or change.op in _MATERIAL_OPS:
            reasons.append(f"material operation {change.op}")
        elif change.op not in _LOCAL_OPS:
            reasons.append(f"unrecognized operation {change.op}")

    normalized_reasons = tuple(dict.fromkeys(reasons))
    impact: PatchImpact = "material" if normalized_reasons else "local"
    authority_effect: AuthorityEffect = "narrow" if authority_changes else "none"
    return IntentPatchClassification(
        impact=impact,
        requires_confirmation=impact == "material",
        authority_effect=authority_effect,
        reasons=normalized_reasons,
    )


def plan_intent_rebase(
    state: LongTaskState,
    *,
    new_intent: IntentVersion,
    patch: IntentPatch,
    confirmation: IntentConfirmation,
    reuse_proofs: tuple[RebaseReuseProof, ...] = (),
) -> IntentRebasePlan:
    """Plan, but never apply, one atomic Intent and Task Graph rebase."""

    graph = state.graph
    if graph is None:
        raise IntentRebaseError("Intent rebase requires a Task Graph")
    prior_intent = _confirmed_graph_intent(state, graph.intent_id, graph.intent_version)
    if any(
        item.intent_id == new_intent.intent_id and item.version == new_intent.version
        for item in state.intents
    ):
        raise IntentRebaseError("proposed Intent version already exists")
    classification = classify_intent_patch(prior_intent, new_intent, patch)
    if not classification.requires_confirmation:
        raise IntentRebaseError("local changes must use a GraphPatch, not IntentRebase")
    validate_intent_confirmation(new_intent, confirmation)

    active_tasks = _active_tasks(graph.tasks, graph.active_task_refs)
    active_groups = _active_groups(graph.groups, graph.active_group_refs)
    proof_by_ref = _proof_index(reuse_proofs)
    replacements = _initial_replacements(
        active_tasks,
        active_groups,
        proof_by_ref,
        new_intent,
    )
    _close_replacements(active_tasks, active_groups, replacements)

    replacement_refs = _replacement_refs(graph.tasks, graph.groups, replacements)
    new_binding_hash = intent_fingerprint(new_intent)
    appended_tasks = tuple(
        _replacement_task(item, replacement_refs, new_intent, new_binding_hash)
        for item in active_tasks
        if item.ref.key in replacements
    )
    appended_groups = tuple(
        _replacement_group(item, replacement_refs, new_intent, new_binding_hash)
        for item in active_groups
        if item.ref.key in replacements
    )
    replacement_by_old = {
        old_key: replacement_refs[old_key] for old_key in sorted(replacements)
    }
    decisions = tuple(
        _binding_decision(item, replacement_by_old, proof_by_ref)
        for item in active_tasks
    ) + tuple(
        _binding_decision(item, replacement_by_old, proof_by_ref)
        for item in active_groups
    )
    cancellations = tuple(
        plan
        for item in active_tasks
        if item.ref.key in replacements
        if (plan := _cancellation_plan(state, item)) is not None
    )

    return IntentRebasePlan(
        expected_root_revision=state.revision,
        expected_graph_revision=graph.revision,
        next_graph_revision=graph.revision + 1,
        prior_intent=prior_intent,
        superseded_intent=replace(prior_intent, status="superseded"),
        new_intent=new_intent,
        patch=patch,
        classification=classification,
        task_revisions_to_append=appended_tasks,
        group_revisions_to_append=appended_groups,
        active_task_refs=tuple(
            replacement_refs.get(item.ref.key, item.ref) for item in active_tasks
        ),
        active_group_refs=tuple(
            replacement_refs.get(item.ref.key, item.ref) for item in active_groups
        ),
        binding_decisions=decisions,
        cancellations=cancellations,
        reset_criterion_ids=tuple(
            sorted(item.id for item in new_intent.success_criteria if item.required)
        ),
    )


def _validate_patch_versions(
    current_intent: IntentVersion,
    proposed_intent: IntentVersion,
    patch: IntentPatch,
) -> None:
    if patch.base_version != current_intent.version:
        raise IntentValidationError(
            f"stale IntentPatch base version {patch.base_version}; "
            f"current is {current_intent.version}"
        )
    if proposed_intent.intent_id != current_intent.intent_id:
        raise IntentValidationError("IntentPatch cannot change intent_id")
    if proposed_intent.version != current_intent.version + 1:
        raise IntentValidationError("proposed Intent must increment version exactly once")


def _required_criteria(intent: IntentVersion) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            item.id,
            item.description,
            item.verification_mode,
            item.validator_id,
        )
        for item in intent.success_criteria
        if item.required
    )


def _authority_target(change: IntentPatchChange) -> bool:
    return "authority" in change.target.casefold() or "authority" in change.op.casefold()


def _confirmed_graph_intent(
    state: LongTaskState,
    intent_id: str,
    intent_version: int,
) -> IntentVersion:
    matches = tuple(
        item
        for item in state.intents
        if item.intent_id == intent_id and item.status == "confirmed"
    )
    if (
        len(matches) != 1
        or matches[0].intent_id != intent_id
        or matches[0].version != intent_version
    ):
        raise IntentRebaseError(
            "Task Graph must reference exactly one confirmed current Intent"
        )
    return matches[0]


def _proof_index(
    proofs: tuple[RebaseReuseProof, ...],
) -> dict[tuple[str, str, int], RebaseReuseProof]:
    result: dict[tuple[str, str, int], RebaseReuseProof] = {}
    for proof in proofs:
        if proof.target_ref.key in result:
            raise IntentRebaseError(
                f"duplicate reuse proof for {_display_ref(proof.target_ref)}"
            )
        result[proof.target_ref.key] = proof
    return result


def _initial_replacements(
    tasks: tuple[TaskRun, ...],
    groups: tuple[GroupRun, ...],
    proofs: Mapping[tuple[str, str, int], RebaseReuseProof],
    new_intent: IntentVersion,
) -> set[tuple[str, str, int]]:
    replacements: set[tuple[str, str, int]] = set()
    for task in tasks:
        if task.status == "pending" or not _has_exact_proof(
            task, proofs, new_intent
        ):
            replacements.add(task.ref.key)
    for group in groups:
        if group.status == "pending" or not _has_exact_proof(
            group, proofs, new_intent
        ):
            replacements.add(group.ref.key)
    return replacements


def _has_exact_proof(
    item: TaskRun | GroupRun,
    proofs: Mapping[tuple[str, str, int], RebaseReuseProof],
    new_intent: IntentVersion,
) -> bool:
    if item.status in {"failed", "cancelled", "pending"}:
        return False
    proof = proofs.get(item.ref.key)
    if proof is None or proof.status != "passed" or not proof.reusable:
        return False
    return (
        proof.target_ref == item.ref
        and proof.prior_intent_version == item.intent_version
        and proof.new_intent_version == new_intent.version
        and proof.new_intent_fingerprint == intent_fingerprint(new_intent)
        and proof.intent_binding_hash == item.intent_binding_hash
        and proof.dependency_refs == _object_dependencies(item)
        and proof.completion_contract_hash
        == compute_fingerprint(_plain(item.completion_contract))
        and bool(proof.validator_fingerprint.strip())
    )


def _object_dependencies(item: TaskRun | GroupRun) -> tuple[DependencyRef, ...]:
    if isinstance(item, TaskRun):
        return item.depends_on
    return item.depends_on + tuple(child.task_ref for child in item.children)


def _close_replacements(
    tasks: tuple[TaskRun, ...],
    groups: tuple[GroupRun, ...],
    replacements: set[tuple[str, str, int]],
) -> None:
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task.ref.key in replacements:
                continue
            if any(ref.key in replacements for ref in task.depends_on):
                replacements.add(task.ref.key)
                changed = True
        for group in groups:
            if group.ref.key in replacements:
                continue
            if any(ref.key in replacements for ref in _object_dependencies(group)):
                replacements.add(group.ref.key)
                changed = True


def _replacement_refs(
    all_tasks: tuple[TaskRun, ...],
    all_groups: tuple[GroupRun, ...],
    replacements: set[tuple[str, str, int]],
) -> dict[tuple[str, str, int], DependencyRef]:
    task_revisions: dict[str, int] = {}
    group_revisions: dict[str, int] = {}
    for task in all_tasks:
        task_revisions[task.task_id] = max(
            task_revisions.get(task.task_id, 0), task.task_revision
        )
    for group in all_groups:
        group_revisions[group.group_id] = max(
            group_revisions.get(group.group_id, 0), group.group_revision
        )

    result: dict[tuple[str, str, int], DependencyRef] = {}
    for kind, object_id, revision in sorted(replacements):
        if kind == "task":
            result[(kind, object_id, revision)] = DependencyRef(
                "task", object_id, task_revisions[object_id] + 1
            )
        else:
            result[(kind, object_id, revision)] = DependencyRef(
                "group", object_id, group_revisions[object_id] + 1
            )
    return result


def _replacement_task(
    task: TaskRun,
    replacement_refs: Mapping[tuple[str, str, int], DependencyRef],
    new_intent: IntentVersion,
    new_binding_hash: str,
) -> TaskRun:
    new_ref = replacement_refs[task.ref.key]
    return replace(
        task,
        task_revision=new_ref.revision,
        intent_version=new_intent.version,
        intent_binding_hash=new_binding_hash,
        intent_binding_state="current",
        depends_on=_rewrite_refs(task.depends_on, replacement_refs),
        status="pending",
        active_attempt_id=None,
        output_refs=(),
        failure=None,
    )


def _replacement_group(
    group: GroupRun,
    replacement_refs: Mapping[tuple[str, str, int], DependencyRef],
    new_intent: IntentVersion,
    new_binding_hash: str,
) -> GroupRun:
    new_ref = replacement_refs[group.ref.key]
    return replace(
        group,
        group_revision=new_ref.revision,
        intent_version=new_intent.version,
        intent_binding_hash=new_binding_hash,
        intent_binding_state="current",
        depends_on=_rewrite_refs(group.depends_on, replacement_refs),
        children=tuple(
            GroupChildRef(
                replacement_refs.get(child.task_ref.key, child.task_ref),
                child.required,
            )
            for child in group.children
        ),
        status="pending",
        winner_task_ref=None,
        verification_record_ref=None,
    )


def _rewrite_refs(
    refs: tuple[DependencyRef, ...],
    replacements: Mapping[tuple[str, str, int], DependencyRef],
) -> tuple[DependencyRef, ...]:
    return tuple(replacements.get(ref.key, ref) for ref in refs)


def _binding_decision(
    item: TaskRun | GroupRun,
    replacements: Mapping[tuple[str, str, int], DependencyRef],
    proofs: Mapping[tuple[str, str, int], RebaseReuseProof],
) -> RebaseBindingDecision:
    replacement_ref = replacements.get(item.ref.key)
    if replacement_ref is not None:
        return RebaseBindingDecision(
            target_ref=item.ref,
            decision="invalidated",
            replacement_ref=replacement_ref,
            reason=(
                "pending objects are always rebound"
                if item.status == "pending"
                else "reuse was not proven against the exact new Intent binding"
            ),
        )
    proof = proofs[item.ref.key]
    return RebaseBindingDecision(
        target_ref=item.ref,
        decision="retained",
        replacement_ref=None,
        reason="exact pinned rebase proof passed",
        proof_record_id=proof.record_id,
    )


def _cancellation_plan(
    state: LongTaskState,
    task: TaskRun,
) -> RebaseCancellationPlan | None:
    if task.active_attempt_id is None:
        return None
    matches = tuple(
        item
        for item in state.attempts
        if item.attempt_id == task.active_attempt_id and item.task_ref == task.ref
    )
    if len(matches) != 1:
        raise IntentRebaseError(
            f"active Task {_display_ref(task.ref)} has no exact active Attempt"
        )
    attempt = matches[0]
    if attempt.status not in _ACTIVE_ATTEMPT_STATUSES:
        raise IntentRebaseError(
            f"Task {_display_ref(task.ref)} points to terminal Attempt "
            f"{attempt.attempt_id!r}"
        )
    return RebaseCancellationPlan(
        attempt_id=attempt.attempt_id,
        task_ref=task.ref,
        lease_epoch=attempt.lease.epoch,
        lease_token=attempt.lease.token,
        resource_keys=tuple(
            sorted(set(task.resource_keys) | set(attempt.lease.resource_keys))
        ),
        reason=f"Intent rebase invalidated {_display_ref(task.ref)}",
    )


def _active_tasks(
    tasks: tuple[TaskRun, ...],
    refs: tuple[DependencyRef, ...],
) -> tuple[TaskRun, ...]:
    by_key = {item.ref.key: item for item in tasks}
    result: list[TaskRun] = []
    for ref in refs:
        item = by_key.get(ref.key)
        if ref.kind != "task" or item is None:
            raise IntentRebaseError(f"missing active Task {_display_ref(ref)}")
        result.append(item)
    return tuple(result)


def _active_groups(
    groups: tuple[GroupRun, ...],
    refs: tuple[DependencyRef, ...],
) -> tuple[GroupRun, ...]:
    by_key = {item.ref.key: item for item in groups}
    result: list[GroupRun] = []
    for ref in refs:
        item = by_key.get(ref.key)
        if ref.kind != "group" or item is None:
            raise IntentRebaseError(f"missing active Group {_display_ref(ref)}")
        result.append(item)
    return tuple(result)


def _display_ref(ref: DependencyRef) -> str:
    return f"{ref.kind}:{ref.id}:{ref.revision}"


def _validate_impact(value: str) -> None:
    if value not in {"low", "high"}:
        raise IntentValidationError(f"unsupported uncertainty impact {value!r}")


def _non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise IntentValidationError(f"{field_name} must be non-empty")


def _positive(value: int, field_name: str) -> None:
    if isinstance(value, bool) or value <= 0:
        raise IntentValidationError(f"{field_name} must be a positive integer")


def _freeze_json(value: Any) -> JSONValue:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        frozen = {
            str(key): _freeze_json(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
        if len(frozen) != len(value):
            raise IntentValidationError("JSON object keys must be strings")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    raise IntentValidationError(
        f"Intent value must be JSON-compatible, got {type(value).__name__}"
    )


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


__all__ = [
    "AuthorityEffect",
    "BindingDecision",
    "ClarificationAssessment",
    "IntentAssumption",
    "IntentConfirmation",
    "IntentImpact",
    "IntentPatch",
    "IntentPatchChange",
    "IntentPatchClassification",
    "IntentQuestion",
    "IntentRebaseError",
    "IntentRebasePlan",
    "IntentValidationError",
    "JSONValue",
    "PatchImpact",
    "RebaseBindingDecision",
    "RebaseCancellationPlan",
    "RebaseReuseProof",
    "ReuseProofStatus",
    "assess_intent_clarification",
    "classify_intent_patch",
    "intent_fingerprint",
    "plan_intent_rebase",
    "validate_intent_confirmation",
]
