"""Immutable persisted values for the long-running Task Graph runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

IntentStatus = Literal["draft", "confirmed", "superseded"]
IntentBindingState = Literal["current", "retained", "invalidated"]
RefKind = Literal["task", "group"]
TaskKind = Literal["executable", "expandable"]
TaskStatus = Literal["pending", "running", "verifying", "waiting", "completed", "failed", "cancelled"]
GroupStatus = Literal["pending", "running", "verifying", "completed", "failed", "cancelled"]
GraphStatus = Literal["planning", "active", "waiting", "verifying", "completed", "failed", "cancelled"]
AttemptStatus = Literal["created", "leased", "running", "waiting", "submitted", "completed", "failed", "cancelled"]
AttemptMode = Literal["child_agent", "operation", "parent_inline", "human"]
JoinPolicy = Literal["all_required", "any_success"]
FailureBehavior = Literal["continue", "cancel_unneeded", "fail_group"]
ReceiptStatus = Literal[
    "received",
    "validated",
    "accepted",
    "repairable",
    "rejected",
    "stale",
]
VerificationStatus = Literal["passed", "repairable", "needs_replan", "ambiguous", "terminal"]
ComponentInvocationKind = Literal[
    "planner",
    "context_builder",
    "task_verifier",
    "criterion_verifier",
    "goal_verifier",
    "group_verifier",
]
ComponentInvocationStatus = Literal["prepared", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class IntentCriterion:
    id: str
    description: str
    required: bool
    verification_mode: str
    validator_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntentVersion:
    intent_id: str
    version: int
    status: IntentStatus
    goal: str
    desired_outcome: str
    success_criteria: tuple[IntentCriterion, ...]
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    authority_hash: str = ""


@dataclass(frozen=True, slots=True)
class DependencyRef:
    kind: RefKind
    id: str
    revision: int

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.kind, self.id, self.revision)


@dataclass(frozen=True, slots=True)
class CompletionContract:
    output_schema_id: str
    validator_ids: tuple[str, ...]
    required_artifact_types: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutorBinding:
    mode: AttemptMode
    id: str
    component_fingerprint: str


@dataclass(frozen=True, slots=True)
class ExecutorPolicy:
    allowed_bindings: tuple[ExecutorBinding, ...]
    preferred_binding: ExecutorBinding


@dataclass(frozen=True, slots=True)
class TaskRun:
    task_id: str
    task_revision: int
    graph_id: str
    intent_version: int
    intent_binding_hash: str
    intent_binding_state: IntentBindingState
    goal: str
    supports: tuple[str, ...]
    depends_on: tuple[DependencyRef, ...]
    priority: int
    required: bool
    kind: TaskKind
    completion_contract: CompletionContract
    executor_policy: ExecutorPolicy
    resource_keys: tuple[str, ...] = ()
    status: TaskStatus = "pending"
    active_attempt_id: str | None = None
    output_refs: tuple[str, ...] = ()
    failure: str | None = None

    @property
    def ref(self) -> DependencyRef:
        return DependencyRef("task", self.task_id, self.task_revision)


@dataclass(frozen=True, slots=True)
class GroupChildRef:
    task_ref: DependencyRef
    required: bool


@dataclass(frozen=True, slots=True)
class GroupRun:
    group_id: str
    group_revision: int
    graph_id: str
    intent_version: int
    intent_binding_hash: str
    intent_binding_state: IntentBindingState
    supports: tuple[str, ...]
    required: bool
    depends_on: tuple[DependencyRef, ...]
    completion_contract: CompletionContract
    children: tuple[GroupChildRef, ...]
    join_policy: JoinPolicy
    failure_behavior: FailureBehavior
    status: GroupStatus = "pending"
    winner_task_ref: DependencyRef | None = None
    verification_record_ref: str | None = None

    @property
    def ref(self) -> DependencyRef:
        return DependencyRef("group", self.group_id, self.group_revision)


@dataclass(frozen=True, slots=True)
class GraphLimits:
    max_tasks: int
    max_graph_depth: int
    max_replans: int
    max_concurrency: int
    max_child_runs: int
    template_concurrency_limits: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class LeaseRecord:
    owner_id: str
    epoch: int
    token: str
    expires_at: str
    resource_keys: tuple[str, ...] = ()
    retiring: bool = False


@dataclass(frozen=True, slots=True)
class ResourceLock:
    resource_key: str
    attempt_id: str
    fencing_token: str
    retiring: bool = False


@dataclass(frozen=True, slots=True)
class CancellationRequest:
    cancellation_id: str
    attempt_id: str
    reason: str
    lease_epoch: int
    lease_token: str
    status: Literal["requested", "acknowledged"] = "requested"


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    attempt_id: str
    task_ref: DependencyRef
    status: AttemptStatus
    executor_binding: ExecutorBinding
    context_manifest_ref: str
    completion_contract_hash: str
    dispatch_key: str
    lease: LeaseRecord
    parent_execution_contract_fingerprint: str
    child_run_id: str | None = None
    child_workflow_fingerprint: str | None = None
    child_execution_contract_fingerprint: str | None = None
    child_checkpoint_ns: str | None = None
    parent_node_id: str | None = None
    parent_node_attempt: int | None = None
    context_manifest_fingerprint: str | None = None
    child_template_fingerprint: str | None = None
    child_observation_revision: int | None = None
    child_observation_status: str | None = None
    submission_sequence: int = 0
    output_refs: tuple[str, ...] = ()
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateReceipt:
    submission_id: str
    attempt_id: str
    submission_sequence: int
    payload_hash: str
    status: ReceiptStatus
    task_ref: DependencyRef | None = None
    child_run_id: str | None = None
    lease_epoch: int | None = None
    lease_token_hash: str | None = None
    context_manifest_fingerprint: str | None = None
    completion_contract_hash: str | None = None
    parent_execution_contract_fingerprint: str | None = None
    submission_outcome: str | None = None
    submission_snapshot: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    validator_record_ids: tuple[str, ...] = ()
    result_refs: tuple[str, ...] = ()
    reason: str | None = None
    decision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "submission_snapshot", _freeze_mapping(self.submission_snapshot))


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    kind: Literal["context_manifest", "candidate_output"]
    uri: str
    content_hash: str
    size_bytes: int
    mime_type: str | None
    trust_level: Literal["trusted", "untrusted"]
    producer_attempt_id: str
    task_ref: DependencyRef | None = None
    producer_child_run_id: str | None = None
    artifact_type: str | None = None
    schema_version: str | None = None
    visibility: Literal["task", "graph", "workflow"] = "task"
    committed: bool = True


@dataclass(frozen=True, slots=True)
class DurableComponentInvocation:
    invocation_id: str
    kind: ComponentInvocationKind
    component_id: str
    component_fingerprint: str
    idempotency_key: str
    input_hash: str
    status: ComponentInvocationStatus
    output_hash: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    record_id: str
    kind: Literal["task", "group", "criterion", "goal", "rebase"]
    target_ref: str
    component_fingerprint: str
    input_hash: str
    status: VerificationStatus
    evidence_refs: tuple[str, ...] = ()
    reason: str | None = None
    submission_id: str | None = None
    validator_id: str | None = None
    validator_version: str | None = None
    invocation_id: str | None = None
    output_hash: str | None = None
    outcome: str | None = None
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    criterion_id: str | None
    claim: str
    source_ref: str
    producer_attempt_id: str
    verification_method: str
    verification_status: Literal["verified"]
    verifier_id: str
    verified_at: str
    child_run_id: str | None = None
    visibility: Literal["task", "graph", "workflow"] = "task"


@dataclass(frozen=True, slots=True)
class CriterionCoverage:
    criterion_id: str
    status: Literal["unsatisfied", "partially_satisfied", "satisfied", "blocked"]
    evidence_refs: tuple[str, ...] = ()
    verified_by: str | None = None


@dataclass(frozen=True, slots=True)
class GraphPatchOperation:
    op: str
    expected_revision: int | None = None
    task: TaskRun | None = None
    group: GroupRun | None = None
    task_id: str | None = None
    group_id: str | None = None
    dependencies: tuple[DependencyRef, ...] = ()
    priority: int | None = None
    executor_policy: ExecutorPolicy | None = None
    child_tasks: tuple[TaskRun, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphPatch:
    base_revision: int
    trigger: str
    reason: str
    operations: tuple[GraphPatchOperation, ...]


@dataclass(frozen=True, slots=True)
class TaskGraphRun:
    graph_id: str
    intent_id: str
    intent_version: int
    revision: int
    status: GraphStatus
    limits: GraphLimits
    required_criteria: tuple[str, ...]
    tasks: tuple[TaskRun, ...] = ()
    groups: tuple[GroupRun, ...] = ()
    active_task_refs: tuple[DependencyRef, ...] = ()
    active_group_refs: tuple[DependencyRef, ...] = ()
    replan_count: int = 0


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    event_type: str
    root_revision: int
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class LongTaskState:
    root_run_id: str
    revision: int
    intents: tuple[IntentVersion, ...]
    graph: TaskGraphRun | None
    attempts: tuple[TaskAttempt, ...] = ()
    receipts: tuple[CandidateReceipt, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()
    component_invocations: tuple[DurableComponentInvocation, ...] = ()
    verification_records: tuple[VerificationRecord, ...] = ()
    evidence_records: tuple[EvidenceRecord, ...] = ()
    criterion_coverage: tuple[CriterionCoverage, ...] = ()
    resource_locks: tuple[ResourceLock, ...] = ()
    cancellation_requests: tuple[CancellationRequest, ...] = ()
    events: tuple[AuditEvent, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))


def long_task_state_from_snapshot(raw: Mapping[str, Any]) -> LongTaskState:
    intents = tuple(_intent_from(item) for item in _items(raw, "intents"))
    graph_raw = raw.get("graph")
    return LongTaskState(
        root_run_id=_string(raw, "root_run_id"),
        revision=_int(raw, "revision"),
        intents=intents,
        graph=None if graph_raw is None else _graph_from(_mapping(graph_raw, "graph")),
        attempts=tuple(_attempt_from(item) for item in _items(raw, "attempts")),
        receipts=tuple(_receipt_from(item) for item in _items(raw, "receipts")),
        artifacts=tuple(ArtifactRecord(**item) for item in _items(raw, "artifacts")),
        component_invocations=tuple(
            DurableComponentInvocation(**item)
            for item in _items(raw, "component_invocations")
        ),
        verification_records=tuple(
            VerificationRecord(**_tuple_fields(item, "evidence_refs", "artifact_refs"))
            for item in _items(raw, "verification_records")
        ),
        evidence_records=tuple(
            EvidenceRecord(**item) for item in _items(raw, "evidence_records")
        ),
        criterion_coverage=tuple(
            CriterionCoverage(**_tuple_fields(item, "evidence_refs"))
            for item in _items(raw, "criterion_coverage")
        ),
        resource_locks=tuple(
            ResourceLock(**item) for item in _items(raw, "resource_locks")
        ),
        cancellation_requests=tuple(
            CancellationRequest(**item)
            for item in _items(raw, "cancellation_requests")
        ),
        events=tuple(
            AuditEvent(
                event_id=_string(item, "event_id"),
                event_type=_string(item, "event_type"),
                root_revision=_int(item, "root_revision"),
                payload=_mapping(item.get("payload", {}), "payload"),
            )
            for item in _items(raw, "events")
        ),
    )


def _intent_from(raw: Mapping[str, Any]) -> IntentVersion:
    return IntentVersion(
        intent_id=_string(raw, "intent_id"),
        version=_int(raw, "version"),
        status=cast(IntentStatus, _string(raw, "status")),
        goal=_string(raw, "goal"),
        desired_outcome=_string(raw, "desired_outcome"),
        success_criteria=tuple(IntentCriterion(**item) for item in _items(raw, "success_criteria")),
        constraints=tuple(raw.get("constraints", ())),
        non_goals=tuple(raw.get("non_goals", ())),
        assumptions=tuple(raw.get("assumptions", ())),
        authority_hash=str(raw.get("authority_hash", "")),
    )


def _graph_from(raw: Mapping[str, Any]) -> TaskGraphRun:
    limits_raw = _mapping(raw["limits"], "limits")
    return TaskGraphRun(
        graph_id=_string(raw, "graph_id"),
        intent_id=_string(raw, "intent_id"),
        intent_version=_int(raw, "intent_version"),
        revision=_int(raw, "revision"),
        status=cast(GraphStatus, _string(raw, "status")),
        limits=GraphLimits(
            max_tasks=_int(limits_raw, "max_tasks"),
            max_graph_depth=_int(limits_raw, "max_graph_depth"),
            max_replans=_int(limits_raw, "max_replans"),
            max_concurrency=_int(limits_raw, "max_concurrency"),
            max_child_runs=_int(limits_raw, "max_child_runs"),
            template_concurrency_limits=tuple(
                (str(item[0]), int(item[1]))
                for item in limits_raw.get("template_concurrency_limits", ())
            ),
        ),
        required_criteria=tuple(raw.get("required_criteria", ())),
        tasks=tuple(_task_from(item) for item in _items(raw, "tasks")),
        groups=tuple(_group_from(item) for item in _items(raw, "groups")),
        active_task_refs=tuple(_ref_from(item) for item in _items(raw, "active_task_refs")),
        active_group_refs=tuple(_ref_from(item) for item in _items(raw, "active_group_refs")),
        replan_count=int(raw.get("replan_count", 0)),
    )


def _task_from(raw: Mapping[str, Any]) -> TaskRun:
    return TaskRun(
        task_id=_string(raw, "task_id"),
        task_revision=_int(raw, "task_revision"),
        graph_id=_string(raw, "graph_id"),
        intent_version=_int(raw, "intent_version"),
        intent_binding_hash=_string(raw, "intent_binding_hash"),
        intent_binding_state=cast(IntentBindingState, _string(raw, "intent_binding_state")),
        goal=_string(raw, "goal"),
        supports=tuple(raw.get("supports", ())),
        depends_on=tuple(_ref_from(item) for item in _items(raw, "depends_on")),
        priority=_int(raw, "priority"),
        required=bool(raw["required"]),
        kind=cast(TaskKind, _string(raw, "kind")),
        completion_contract=_completion_from(_mapping(raw["completion_contract"], "completion_contract")),
        executor_policy=_executor_policy_from(_mapping(raw["executor_policy"], "executor_policy")),
        resource_keys=tuple(raw.get("resource_keys", ())),
        status=cast(TaskStatus, _string(raw, "status")),
        active_attempt_id=cast(str | None, raw.get("active_attempt_id")),
        output_refs=tuple(raw.get("output_refs", ())),
        failure=cast(str | None, raw.get("failure")),
    )


def _group_from(raw: Mapping[str, Any]) -> GroupRun:
    return GroupRun(
        group_id=_string(raw, "group_id"),
        group_revision=_int(raw, "group_revision"),
        graph_id=_string(raw, "graph_id"),
        intent_version=_int(raw, "intent_version"),
        intent_binding_hash=_string(raw, "intent_binding_hash"),
        intent_binding_state=cast(IntentBindingState, _string(raw, "intent_binding_state")),
        supports=tuple(raw.get("supports", ())),
        required=bool(raw["required"]),
        depends_on=tuple(_ref_from(item) for item in _items(raw, "depends_on")),
        completion_contract=_completion_from(_mapping(raw["completion_contract"], "completion_contract")),
        children=tuple(
            GroupChildRef(
                task_ref=_ref_from(_mapping(item["task_ref"], "task_ref")),
                required=bool(item["required"]),
            )
            for item in _items(raw, "children")
        ),
        join_policy=cast(JoinPolicy, _string(raw, "join_policy")),
        failure_behavior=cast(FailureBehavior, _string(raw, "failure_behavior")),
        status=cast(GroupStatus, _string(raw, "status")),
        winner_task_ref=(
            None
            if raw.get("winner_task_ref") is None
            else _ref_from(_mapping(raw["winner_task_ref"], "winner_task_ref"))
        ),
        verification_record_ref=cast(str | None, raw.get("verification_record_ref")),
    )


def _attempt_from(raw: Mapping[str, Any]) -> TaskAttempt:
    return TaskAttempt(
        attempt_id=_string(raw, "attempt_id"),
        task_ref=_ref_from(_mapping(raw["task_ref"], "task_ref")),
        status=cast(AttemptStatus, _string(raw, "status")),
        executor_binding=_binding_from(_mapping(raw["executor_binding"], "executor_binding")),
        context_manifest_ref=_string(raw, "context_manifest_ref"),
        completion_contract_hash=_string(raw, "completion_contract_hash"),
        dispatch_key=_string(raw, "dispatch_key"),
        lease=LeaseRecord(**_tuple_fields(_mapping(raw["lease"], "lease"), "resource_keys")),
        parent_execution_contract_fingerprint=_string(
            raw, "parent_execution_contract_fingerprint"
        ),
        child_run_id=cast(str | None, raw.get("child_run_id")),
        child_workflow_fingerprint=cast(str | None, raw.get("child_workflow_fingerprint")),
        child_execution_contract_fingerprint=cast(
            str | None, raw.get("child_execution_contract_fingerprint")
        ),
        child_checkpoint_ns=cast(str | None, raw.get("child_checkpoint_ns")),
        parent_node_id=cast(str | None, raw.get("parent_node_id")),
        parent_node_attempt=cast(int | None, raw.get("parent_node_attempt")),
        context_manifest_fingerprint=cast(
            str | None, raw.get("context_manifest_fingerprint")
        ),
        child_template_fingerprint=cast(
            str | None, raw.get("child_template_fingerprint")
        ),
        child_observation_revision=cast(
            int | None, raw.get("child_observation_revision")
        ),
        child_observation_status=cast(str | None, raw.get("child_observation_status")),
        submission_sequence=int(raw.get("submission_sequence", 0)),
        output_refs=tuple(raw.get("output_refs", ())),
        failure=cast(str | None, raw.get("failure")),
    )


def _receipt_from(raw: Mapping[str, Any]) -> CandidateReceipt:
    task_ref = raw.get("task_ref")
    return CandidateReceipt(
        submission_id=_string(raw, "submission_id"),
        attempt_id=_string(raw, "attempt_id"),
        submission_sequence=_int(raw, "submission_sequence"),
        payload_hash=_string(raw, "payload_hash"),
        status=cast(ReceiptStatus, _string(raw, "status")),
        validator_record_ids=tuple(raw.get("validator_record_ids", ())),
        result_refs=tuple(raw.get("result_refs", ())),
        reason=cast(str | None, raw.get("reason")),
        task_ref=(
            None
            if task_ref is None
            else _ref_from(_mapping(task_ref, "receipt.task_ref"))
        ),
        child_run_id=cast(str | None, raw.get("child_run_id")),
        lease_epoch=cast(int | None, raw.get("lease_epoch")),
        lease_token_hash=cast(str | None, raw.get("lease_token_hash")),
        context_manifest_fingerprint=cast(
            str | None, raw.get("context_manifest_fingerprint")
        ),
        completion_contract_hash=cast(
            str | None, raw.get("completion_contract_hash")
        ),
        parent_execution_contract_fingerprint=cast(
            str | None, raw.get("parent_execution_contract_fingerprint")
        ),
        submission_outcome=cast(str | None, raw.get("submission_outcome")),
        submission_snapshot=_mapping(
            raw.get("submission_snapshot", {}),
            "receipt.submission_snapshot",
        ),
        decision=cast(str | None, raw.get("decision")),
    )


def _completion_from(raw: Mapping[str, Any]) -> CompletionContract:
    return CompletionContract(
        output_schema_id=_string(raw, "output_schema_id"),
        validator_ids=tuple(raw.get("validator_ids", ())),
        required_artifact_types=tuple(raw.get("required_artifact_types", ())),
        required_evidence=tuple(raw.get("required_evidence", ())),
    )


def _executor_policy_from(raw: Mapping[str, Any]) -> ExecutorPolicy:
    bindings = tuple(_binding_from(item) for item in _items(raw, "allowed_bindings"))
    return ExecutorPolicy(
        allowed_bindings=bindings,
        preferred_binding=_binding_from(_mapping(raw["preferred_binding"], "preferred_binding")),
    )


def _binding_from(raw: Mapping[str, Any]) -> ExecutorBinding:
    return ExecutorBinding(
        mode=cast(AttemptMode, _string(raw, "mode")),
        id=_string(raw, "id"),
        component_fingerprint=_string(raw, "component_fingerprint"),
    )


def _ref_from(raw: Mapping[str, Any]) -> DependencyRef:
    return DependencyRef(
        kind=cast(RefKind, _string(raw, "kind")),
        id=_string(raw, "id"),
        revision=_int(raw, "revision"),
    )


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{source} must be a mapping")
    return cast(Mapping[str, Any], value)


def _items(raw: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    value = raw.get(key, ())
    if not isinstance(value, list | tuple):
        raise ValueError(f"{key} must be an array")
    return tuple(_mapping(item, key) for item in value)


def _tuple_fields(raw: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    result = dict(raw)
    for key in keys:
        result[key] = tuple(result.get(key, ()))
    return result


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


__all__ = [
    "ArtifactRecord",
    "AttemptMode",
    "AttemptStatus",
    "AuditEvent",
    "CancellationRequest",
    "CandidateReceipt",
    "CompletionContract",
    "CriterionCoverage",
    "DependencyRef",
    "DurableComponentInvocation",
    "EvidenceRecord",
    "ExecutorBinding",
    "ExecutorPolicy",
    "FailureBehavior",
    "GraphLimits",
    "GraphPatch",
    "GraphPatchOperation",
    "GraphStatus",
    "GroupChildRef",
    "GroupRun",
    "GroupStatus",
    "IntentBindingState",
    "IntentCriterion",
    "IntentStatus",
    "IntentVersion",
    "JoinPolicy",
    "LeaseRecord",
    "LongTaskState",
    "ReceiptStatus",
    "RefKind",
    "ResourceLock",
    "TaskAttempt",
    "TaskGraphRun",
    "TaskKind",
    "TaskRun",
    "TaskStatus",
    "VerificationRecord",
    "VerificationStatus",
    "long_task_state_from_snapshot",
]
