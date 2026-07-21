"""Focused OperationTaskGraphRuntime Intent rebase integration tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task.intent import (
    IntentConfirmation,
    IntentPatch,
    IntentPatchChange,
    intent_fingerprint,
)
from modi_harness.long_task.runtime import (
    OperationTaskGraphRuntime,
    TaskGraphPending,
    TaskGraphRuntimeError,
)
from modi_harness.long_task.types import (
    CompletionContract,
    CriterionCoverage,
    ExecutorBinding,
    ExecutorPolicy,
    GraphLimits,
    IntentCriterion,
    IntentVersion,
    LeaseRecord,
    LongTaskState,
    ResourceLock,
    TaskAttempt,
    TaskGraphRun,
    TaskRun,
)
from modi_harness.workflow import (
    CompletionValidatorRegistry,
    Node,
    OperationAdapter,
    OperationAdapterRegistry,
    PinnedComponent,
    PinnedComponentRegistry,
    TaskGraphLimits,
    TaskGraphNodeConfig,
    Workflow,
    build_execution_contract,
)
from modi_harness.workspace import TaskArtifactStore


class _UnusedBridge:
    def dispatch_task_operation(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("rebase tests must not dispatch Task work")

    def resume_task_operation(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("rebase tests must not resume Task work")


def _component(
    component_id: str,
    kind: str,
    implementation: Callable[[dict[str, Any]], Mapping[str, Any]],
) -> PinnedComponent:
    def keyed(inputs: dict[str, Any], *, idempotency_key: str) -> Mapping[str, Any]:
        assert idempotency_key
        return implementation(inputs)

    return PinnedComponent(
        id=component_id,
        version="1",
        kind=kind,  # type: ignore[arg-type]
        implementation_digest=f"sha256:{component_id}",
        protocol_version="v1",
        input_schema_id=f"{component_id}-input",
        output_schema_id=f"{component_id}-output",
        supported_outcomes=("passed",),
        configuration={},
        implementation=keyed,
    )


def _intent(
    version: int,
    *,
    goal: str,
    authority_hash: str = "sha256:authority",
    confirmation_proof_id: str | None = None,
) -> IntentVersion:
    return IntentVersion(
        intent_id="intent-1",
        version=version,
        status="confirmed",
        goal=goal,
        desired_outcome="A verified result",
        success_criteria=(
            IntentCriterion(
                "criterion-1",
                "The result works",
                True,
                "validator",
                "criterion-v1",
            ),
        ),
        authority_hash=authority_hash,
        confirmation_proof_id=confirmation_proof_id,
    )


def _patch(*, authority_effect: str = "none") -> IntentPatch:
    change = (
        IntentPatchChange(
            "change_authority",
            "authority",
            {"network": True},
            authority_effect=authority_effect,  # type: ignore[arg-type]
        )
        if authority_effect != "none"
        else IntentPatchChange("set_goal", "goal", "Build V2")
    )
    return IntentPatch(
        base_version=1,
        reason="The confirmed goal changed",
        changes=(change,),
        patch_id="request-1",
    )


def _task(
    adapter: OperationAdapter,
    intent: IntentVersion,
    *,
    status: str,
    active_attempt_id: str | None = None,
    output_refs: tuple[str, ...] = (),
    resource_keys: tuple[str, ...] = (),
) -> TaskRun:
    binding = ExecutorBinding(
        "operation",
        adapter.id,
        compute_fingerprint(adapter.snapshot()),
    )
    return TaskRun(
        task_id="work",
        task_revision=1,
        graph_id="graph-1",
        intent_version=intent.version,
        intent_binding_hash=intent_fingerprint(intent),
        intent_binding_state="current",
        goal="Do the work",
        supports=("criterion-1",),
        depends_on=(),
        priority=50,
        required=True,
        kind="executable",
        completion_contract=CompletionContract("result-v1", ("task-v1",)),
        executor_policy=ExecutorPolicy((binding,), binding),
        resource_keys=resource_keys,
        status=status,  # type: ignore[arg-type]
        active_attempt_id=active_attempt_id,
        output_refs=output_refs,
    )


def _state(
    task: TaskRun,
    intent: IntentVersion,
    *,
    attempt: TaskAttempt | None = None,
    lock: ResourceLock | None = None,
) -> LongTaskState:
    return LongTaskState(
        root_run_id="root-1",
        revision=7,
        intents=(intent,),
        graph=TaskGraphRun(
            graph_id="graph-1",
            intent_id=intent.intent_id,
            intent_version=intent.version,
            revision=4,
            status="waiting",
            limits=GraphLimits(8, 4, 4, 2, 2),
            required_criteria=("criterion-1",),
            tasks=(task,),
            active_task_refs=(task.ref,),
        ),
        attempts=() if attempt is None else (attempt,),
        criterion_coverage=(CriterionCoverage("criterion-1", "satisfied"),),
        resource_locks=() if lock is None else (lock,),
    )


def _fixture(
    tmp_path: Path,
    *,
    status: str,
    reusable: bool,
    active: bool = False,
    policy_output: Mapping[str, Any] | None = None,
) -> tuple[OperationTaskGraphRuntime, list[dict[str, Any]], TaskRun]:
    old_intent = _intent(1, goal="Build V1", confirmation_proof_id="proof-v1")
    adapters = OperationAdapterRegistry()
    adapter = OperationAdapter(
        id="work-op",
        version="1",
        kind="tool",
        target="work-op",
        node_selectable=True,
        required_capabilities=(),
        side_effect=False,
        recovery_mode="pure",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    adapters.register(adapter)
    attempt_id = "attempt-1" if active else None
    task = _task(
        adapter,
        old_intent,
        status=status,
        active_attempt_id=attempt_id,
        output_refs=("artifact://verified",) if status == "completed" else (),
        resource_keys=("workspace://result",) if active else (),
    )
    policy_calls: list[dict[str, Any]] = []

    def policy(inputs: dict[str, Any]) -> Mapping[str, Any]:
        policy_calls.append(inputs)
        if policy_output is not None:
            return policy_output
        return {
            "outcome": "passed",
            "reuse_decisions": [
                {
                    "target_ref": item["target_ref"],
                    "reusable": reusable,
                }
                for item in inputs["candidates"]
            ],
        }

    def passed(_inputs: dict[str, Any]) -> Mapping[str, Any]:
        return {"outcome": "passed"}

    components = PinnedComponentRegistry()
    for component in (
        _component("planner-v1", "planner", passed),
        _component("policy-v1", "graph_policy", policy),
        _component("context-v1", "context_builder", passed),
        _component("task-v1", "task_verifier", passed),
        _component("criterion-v1", "criterion_verifier", passed),
        _component("goal-v1", "goal_verifier", passed),
    ):
        components.register(component)
    config = TaskGraphNodeConfig(
        planner="planner-v1",
        graph_policy="policy-v1",
        context_builder="context-v1",
        task_validators=("task-v1",),
        group_validators=(),
        criterion_validators=("criterion-v1",),
        goal_verifier="goal-v1",
        operation_adapters=("work-op",),
        parent_inline_components=(),
        human_task_contracts=(),
        child_templates=(),
        limits=TaskGraphLimits(8, 4, 4, 2, 2),
    )
    workflow = Workflow(
        id="rebase-runtime",
        description="Intent rebase runtime fixture.",
        input_schema={"type": "object"},
        start_node="execute",
        nodes=(
            Node(
                id="execute",
                execution="task_graph",
                inputs={},
                completion_output_schema={"type": "object"},
                completion_validator=None,
                completion_required=(),
                completion_review="none",
                transitions={
                    "completed": "$complete",
                    "failed": "$fail",
                    "waiting": "$wait",
                },
                task_graph=config,
            ),
        ),
        definition_fingerprint="rebase-runtime-v1",
    )
    contract = build_execution_contract(
        workflow=workflow,
        adapters=adapters,
        validators=CompletionValidatorRegistry(),
        output_contract={"free_form": True},
        capability_ceiling=(),
        limits={"max_transitions": 4, "max_steps": 20},
        protocol_version="workflow-v1",
        task_graph_components=components,
    )
    attempt = None
    lock = None
    if active:
        attempt = TaskAttempt(
            attempt_id="attempt-1",
            task_ref=task.ref,
            status="running",
            executor_binding=task.executor_policy.preferred_binding,
            context_manifest_ref="context://attempt-1",
            completion_contract_hash=compute_fingerprint(
                {
                    "output_schema_id": task.completion_contract.output_schema_id,
                    "validator_ids": list(task.completion_contract.validator_ids),
                    "required_artifact_types": list(
                        task.completion_contract.required_artifact_types
                    ),
                    "required_evidence": list(
                        task.completion_contract.required_evidence
                    ),
                }
            ),
            dispatch_key="dispatch-1",
            lease=LeaseRecord(
                "scheduler-1",
                3,
                "lease-token-3",
                "2026-07-18T10:00:00Z",
                resource_keys=("lock://db",),
            ),
            parent_execution_contract_fingerprint=contract.fingerprint,
        )
        lock = ResourceLock(
            "workspace://result",
            "attempt-1",
            "lease-token-3",
        )
    runtime = OperationTaskGraphRuntime(
        root_run_id="root-1",
        node_id="execute",
        config=config,
        contract=contract,
        components=components,
        adapters=adapters,
        dispatcher=_UnusedBridge(),
        artifacts=TaskArtifactStore(tmp_path / "artifacts"),
        state=_state(task, old_intent, attempt=attempt, lock=lock),
    )
    return runtime, policy_calls, task


def _new_intent(*, authority_hash: str = "sha256:authority") -> IntentVersion:
    return _intent(
        2,
        goal="Build V2",
        authority_hash=authority_hash,
        confirmation_proof_id="request-1",
    )


def _confirmation(intent: IntentVersion) -> IntentConfirmation:
    return IntentConfirmation(
        intent.intent_id,
        intent.version,
        intent_fingerprint(intent),
        confirmed_by="human:revise",
    )


def _request_through_goal_resume(
    runtime: OperationTaskGraphRuntime,
    *,
    intent: IntentVersion | None = None,
    patch: IntentPatch | None = None,
):
    selected_intent = intent or _new_intent()
    selected_patch = patch or _patch()
    return runtime.resume(
        pending=TaskGraphPending("goal", "request-1"),
        payload={
            "kind": "revise",
            "intent_updates": {
                "new_intent": selected_intent,
                "patch": selected_patch,
            },
        },
        root_revision=8,
    )


def _complete_rebase(runtime: OperationTaskGraphRuntime) -> None:
    prepared = runtime.advance(inputs={}, root_revision=9)
    assert prepared.outcome == "running"
    state = runtime.current_state
    assert state is not None
    assert state.intents[0].version == 1
    assert state.graph is not None and state.graph.status == "waiting"
    assert len(state.component_invocations) == 1
    assert state.component_invocations[0].status == "prepared"

    applied = runtime.advance(inputs={}, root_revision=10)
    assert applied.outcome == "running"


def test_request_prepare_and_atomic_apply_are_three_durable_steps(tmp_path: Path) -> None:
    runtime, policy_calls, _task_value = _fixture(
        tmp_path,
        status="pending",
        reusable=False,
    )

    requested = _request_through_goal_resume(runtime)

    assert requested.outcome == "running"
    requested_state = runtime.current_state
    assert requested_state is not None and requested_state.revision == 8
    assert requested_state.graph is not None
    assert requested_state.graph.status == "waiting"
    assert requested_state.graph.intent_version == 1
    assert [item.event_type for item in requested_state.events] == [
        "intent_rebase_requested"
    ]
    assert requested_state.component_invocations == ()
    assert policy_calls == []

    _complete_rebase(runtime)

    applied_state = runtime.current_state
    assert applied_state is not None and applied_state.revision == 10
    assert applied_state.graph is not None
    assert applied_state.graph.status == "active"
    assert applied_state.graph.intent_version == 2
    assert [item.status for item in applied_state.intents] == [
        "superseded",
        "confirmed",
    ]
    assert applied_state.events[-1].event_type == "intent_rebased"
    assert len(policy_calls) == 1
    pending_planning = runtime._pending_planning(applied_state)
    assert pending_planning is not None
    assert pending_planning[0].kind == "user_change"


def test_completed_task_with_exact_runtime_proof_is_retained(tmp_path: Path) -> None:
    runtime, _policy_calls, original = _fixture(
        tmp_path,
        status="completed",
        reusable=True,
    )

    _request_through_goal_resume(runtime)
    _complete_rebase(runtime)

    state = runtime.current_state
    assert state is not None and state.graph is not None
    retained = next(item for item in state.graph.tasks if item.ref == original.ref)
    assert state.graph.active_task_refs == (original.ref,)
    assert retained.status == "completed"
    assert retained.intent_version == 1
    assert retained.intent_binding_state == "retained"
    assert retained.output_refs == ("artifact://verified",)
    assert any(
        item.kind == "rebase"
        and item.target_ref == "task:work:1"
        and item.status == "passed"
        for item in state.verification_records
    )


def test_pending_task_is_replaced_even_when_verifier_calls_it_reusable(
    tmp_path: Path,
) -> None:
    runtime, policy_calls, original = _fixture(
        tmp_path,
        status="pending",
        reusable=True,
    )

    _request_through_goal_resume(runtime)
    _complete_rebase(runtime)

    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert policy_calls[0]["candidates"][0]["status"] == "pending"
    old = next(item for item in state.graph.tasks if item.ref == original.ref)
    replacement = next(item for item in state.graph.tasks if item.task_revision == 2)
    assert old.status == "cancelled"
    assert old.intent_binding_state == "invalidated"
    assert replacement.status == "pending"
    assert replacement.intent_version == 2
    assert replacement.intent_binding_state == "current"
    assert state.graph.active_task_refs == (replacement.ref,)
    assert not any(item.kind == "rebase" for item in state.verification_records)


def test_invalidated_active_attempt_lock_and_cancellation_are_fenced(
    tmp_path: Path,
) -> None:
    runtime, _policy_calls, original = _fixture(
        tmp_path,
        status="running",
        reusable=False,
        active=True,
    )

    _request_through_goal_resume(runtime)
    _complete_rebase(runtime)

    state = runtime.current_state
    assert state is not None and state.graph is not None
    old = next(item for item in state.graph.tasks if item.ref == original.ref)
    replacement = next(item for item in state.graph.tasks if item.task_revision == 2)
    assert old.status == "cancelled"
    assert old.active_attempt_id is None
    assert old.intent_binding_state == "invalidated"
    assert replacement.status == "pending"
    attempt = state.attempts[0]
    assert attempt.status == "cancelled"
    assert attempt.lease.epoch == 3
    assert attempt.lease.token == "lease-token-3"
    assert attempt.lease.retiring
    assert state.resource_locks[0].retiring
    cancellation = state.cancellation_requests[0]
    assert cancellation.attempt_id == "attempt-1"
    assert cancellation.lease_epoch == 3
    assert cancellation.lease_token == "lease-token-3"


def test_authority_expansion_is_rejected_before_verifier_and_keeps_waiting(
    tmp_path: Path,
) -> None:
    runtime, policy_calls, _task_value = _fixture(
        tmp_path,
        status="pending",
        reusable=False,
    )
    before = runtime.current_state
    expanded = _new_intent(authority_hash="sha256:wider")

    step = _request_through_goal_resume(
        runtime,
        intent=expanded,
        patch=_patch(authority_effect="expand"),
    )

    assert step.outcome == "waiting"
    assert step.pending is not None
    assert "expand execution authority" in str(step.pending.reason)
    assert runtime.current_state == before
    assert runtime.current_state is not None
    assert runtime.current_state.graph is not None
    assert runtime.current_state.graph.status == "waiting"
    assert runtime.current_state.component_invocations == ()
    assert policy_calls == []


def test_stale_root_revision_does_not_partially_switch_intent_or_graph(
    tmp_path: Path,
) -> None:
    runtime, policy_calls, _task_value = _fixture(
        tmp_path,
        status="pending",
        reusable=False,
    )
    before = runtime.current_state
    new_intent = _new_intent()

    with pytest.raises(TaskGraphRuntimeError, match="advance monotonically"):
        runtime.request_intent_rebase(
            new_intent=new_intent,
            patch=_patch(),
            confirmation=_confirmation(new_intent),
            request_id="request-1",
            root_revision=7,
        )

    assert runtime.current_state == before
    assert runtime.current_state is not None
    assert runtime.current_state.graph is not None
    assert runtime.current_state.graph.intent_version == 1
    assert runtime.current_state.graph.revision == 4
    assert len(runtime.current_state.intents) == 1
    assert policy_calls == []


@pytest.mark.parametrize(
    "policy_output",
    [
        {
            "outcome": "passed",
            "reuse_decisions": [
                {
                    "target_ref": {"kind": "task", "id": "unknown", "revision": 1},
                    "reusable": True,
                }
            ],
        },
        {
            "outcome": "passed",
            "reuse_decisions": [
                {
                    "target_ref": {"kind": "task", "id": "work", "revision": 1},
                    "reusable": True,
                },
                {
                    "target_ref": {"kind": "task", "id": "work", "revision": 1},
                    "reusable": True,
                },
            ],
        },
    ],
)
def test_untrusted_rebase_verifier_cannot_forge_or_duplicate_reuse_proofs(
    tmp_path: Path,
    policy_output: Mapping[str, Any],
) -> None:
    runtime, _policy_calls, _task_value = _fixture(
        tmp_path,
        status="completed",
        reusable=True,
        policy_output=policy_output,
    )
    _request_through_goal_resume(runtime)
    runtime.advance(inputs={}, root_revision=9)

    failed = runtime.advance(inputs={}, root_revision=10)

    assert failed.outcome == "failed"
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert state.graph.intent_version == 1
    assert len(state.intents) == 1
    assert not any(item.kind == "rebase" for item in state.verification_records)
