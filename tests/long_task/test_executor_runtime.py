"""Runtime integration tests for parent-inline and human Task executors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import (
    CompletionContract,
    CriterionCoverage,
    ExecutorBinding,
    ExecutorPolicy,
    GraphLimits,
    IntentCriterion,
    IntentVersion,
    LongTaskState,
    TaskGraphRun,
    TaskRun,
    long_task_state_from_snapshot,
)
from modi_harness.long_task.runtime import OperationTaskGraphRuntime, TaskGraphPending
from modi_harness.workflow import (
    ExecutionContract,
    OperationAdapterRegistry,
    PinnedComponent,
    PinnedComponentRegistry,
    TaskGraphLimits,
    TaskGraphNodeConfig,
)
from modi_harness.workspace import TaskArtifactStore


def _component(
    component_id: str,
    kind: str,
    implementation: Any,
    *,
    configuration: dict[str, Any] | None = None,
    outcomes: tuple[str, ...] = ("passed",),
) -> PinnedComponent:
    return PinnedComponent(
        id=component_id,
        version="1",
        kind=kind,  # type: ignore[arg-type]
        implementation_digest=f"sha256:{component_id}",
        protocol_version="v1",
        input_schema_id=f"{component_id}-input",
        output_schema_id=f"{component_id}-output",
        supported_outcomes=outcomes,  # type: ignore[arg-type]
        configuration=configuration or {},
        implementation=implementation,
    )


def _human_component() -> PinnedComponent:
    return _component(
        "human-review-v1",
        "human_contract",
        None,
        configuration={
            "prompt_schema": {
                "type": "object",
                "required": ["title"],
                "properties": {"title": {"type": "string"}},
                "additionalProperties": False,
            },
            "response_schema": {
                "type": "object",
                "required": ["decision", "comment"],
                "properties": {
                    "decision": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "decision_class": "judgment",
            "allowed_decisions": ["approve", "reject"],
            "authority_requirement": {"role": "reviewer"},
            "timeout_behavior": "keep_waiting",
            "resume_policy": "exactly_once",
            "prompt": {"title": "Review the Task result"},
        },
    )


def _intent() -> IntentVersion:
    return IntentVersion(
        intent_id="intent-1",
        version=1,
        status="confirmed",
        goal="Complete one executor Task",
        desired_outcome="A verified result",
        success_criteria=(
            IntentCriterion(
                "criterion-1",
                "The result is verified",
                True,
                "verifier",
                "criterion-v1",
            ),
        ),
        confirmation_proof_id="proof-1",
    )


def _task(
    intent: IntentVersion,
    binding: ExecutorBinding,
    *,
    task_id: str = "executor-task",
) -> TaskRun:
    return TaskRun(
        task_id=task_id,
        task_revision=1,
        graph_id="graph-1",
        intent_version=intent.version,
        intent_binding_hash=compute_fingerprint(
            {
                "intent_id": intent.intent_id,
                "version": intent.version,
                "status": intent.status,
                "goal": intent.goal,
                "desired_outcome": intent.desired_outcome,
                "success_criteria": [
                    {
                        "id": item.id,
                        "description": item.description,
                        "required": item.required,
                        "verification_mode": item.verification_mode,
                        "validator_id": item.validator_id,
                    }
                    for item in intent.success_criteria
                ],
                "constraints": list(intent.constraints),
                "non_goals": list(intent.non_goals),
                "assumptions": list(intent.assumptions),
                "authority_hash": intent.authority_hash,
                "confirmation_proof_id": intent.confirmation_proof_id,
            }
        ),
        intent_binding_state="current",
        goal=f"Run {task_id}",
        supports=("criterion-1",),
        depends_on=(),
        priority=50,
        required=True,
        kind="executable",
        completion_contract=CompletionContract("result-v1", ("task-v1",)),
        executor_policy=ExecutorPolicy((binding,), binding),
    )


def _runtime_fixture(
    tmp_path: Path,
    *,
    mode: str,
    binding_id: str | None = None,
    parent_inline_calls: list[str] | None = None,
) -> tuple[OperationTaskGraphRuntime, LongTaskState, PinnedComponent]:
    intent = _intent()
    inline_calls = parent_inline_calls if parent_inline_calls is not None else []

    def inline(inputs: dict[str, Any], *, idempotency_key: str) -> dict[str, str]:
        del inputs
        inline_calls.append(idempotency_key)
        return {"answer": "parent-inline-ok"}

    inline_component = _component("inline-v1", "parent_inline", inline)
    human_component = _human_component()
    task_verifier = _component(
        "task-v1",
        "task_verifier",
        lambda _inputs, *, idempotency_key: {
            "outcome": "passed",
            "evidence_refs": [idempotency_key],
        },
    )
    criterion_verifier = _component(
        "criterion-v1",
        "criterion_verifier",
        lambda _inputs, *, idempotency_key: {
            "outcome": "passed",
            "evidence_refs": [idempotency_key],
        },
    )
    goal_verifier = _component(
        "goal-v1",
        "goal_verifier",
        lambda _inputs, *, idempotency_key: {
            "outcome": "passed",
            "evidence_refs": [idempotency_key],
        },
    )
    components = PinnedComponentRegistry()
    for component in (
        inline_component,
        human_component,
        task_verifier,
        criterion_verifier,
        goal_verifier,
    ):
        components.register(component)
    selected = (
        inline_component
        if mode == "parent_inline"
        else human_component
    )
    selected_id = binding_id or selected.id
    binding = ExecutorBinding(
        mode=mode,  # type: ignore[arg-type]
        id=selected_id,
        component_fingerprint=selected.fingerprint,
    )
    task = _task(intent, binding)
    graph = TaskGraphRun(
        graph_id="graph-1",
        intent_id=intent.intent_id,
        intent_version=intent.version,
        revision=1,
        status="active",
        limits=GraphLimits(10, 6, 4, 2, 2),
        required_criteria=("criterion-1",),
        tasks=(task,),
        active_task_refs=(task.ref,),
    )
    state = LongTaskState(
        root_run_id="root-1",
        revision=1,
        intents=(intent,),
        graph=graph,
        criterion_coverage=(CriterionCoverage("criterion-1", "unsatisfied"),),
    )
    config = TaskGraphNodeConfig(
        planner="planner-v1",
        graph_policy="policy-v1",
        context_builder="context-v1",
        task_validators=(task_verifier.id,),
        group_validators=(),
        criterion_validators=(criterion_verifier.id,),
        goal_verifier=goal_verifier.id,
        operation_adapters=(),
        parent_inline_components=(inline_component.id,),
        human_task_contracts=(human_component.id,),
        child_templates=(),
        limits=TaskGraphLimits(10, 6, 4, 2, 2),
    )
    contract = ExecutionContract(
        snapshot={
            "task_graph": {
                "nodes": [
                    {
                        "node_id": "execute",
                        "bindings": {
                            "parent_inline_components": [inline_component.snapshot()],
                            "human_task_contracts": [human_component.snapshot()],
                            "task_validators": [task_verifier.snapshot()],
                            "criterion_validators": [criterion_verifier.snapshot()],
                            "goal_verifier": goal_verifier.snapshot(),
                        },
                    }
                ]
            }
        },
        fingerprint="sha256:executor-contract",
    )
    runtime = OperationTaskGraphRuntime(
        root_run_id="root-1",
        node_id="execute",
        config=config,
        contract=contract,
        components=components,
        adapters=OperationAdapterRegistry(),
        dispatcher=object(),  # type: ignore[arg-type]
        artifacts=TaskArtifactStore(tmp_path / "artifacts"),
        state=state,
    )
    return runtime, state, selected


def _advance(runtime: OperationTaskGraphRuntime):
    state = runtime.current_state
    assert state is not None
    return runtime.advance(inputs={}, root_revision=state.revision + 1)


def test_parent_inline_prepares_durably_restarts_and_passes_task_verifier(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    runtime, _state, _component_value = _runtime_fixture(
        tmp_path,
        mode="parent_inline",
        parent_inline_calls=calls,
    )

    _advance(runtime)  # Attempt prepared.
    _advance(runtime)  # Lease acquired.
    _advance(runtime)  # Parent-inline invocation prepared.
    prepared = runtime.current_state
    assert prepared is not None
    assert len(prepared.component_invocations) == 1
    assert prepared.component_invocations[0].status == "prepared"
    assert calls == []

    restored, _, _ = _runtime_fixture(
        tmp_path,
        mode="parent_inline",
        parent_inline_calls=calls,
    )
    restored.current_state = long_task_state_from_snapshot(prepared.snapshot())
    _advance(restored)  # Execute the prepared invocation exactly once.
    assert len(calls) == 1
    assert restored.current_state is not None
    assert restored.current_state.component_invocations[0].status == "completed"

    for _ in range(24):
        current = restored.current_state
        assert current is not None and current.graph is not None
        if current.graph.tasks[0].status == "completed":
            break
        _advance(restored)
    final = restored.current_state
    assert final is not None and final.graph is not None
    assert final.graph.tasks[0].status == "completed"
    assert len(calls) == 1
    assert any(
        item.kind == "task" and item.outcome == "passed"
        for item in final.verification_records
    )


def test_human_task_waits_restores_exact_pending_and_verifies_response(
    tmp_path: Path,
) -> None:
    runtime, _state, _component_value = _runtime_fixture(tmp_path, mode="human")

    _advance(runtime)
    _advance(runtime)
    waiting_step = _advance(runtime)
    assert waiting_step.outcome == "waiting"
    pending_state = runtime.current_state
    assert pending_state is not None and pending_state.graph is not None
    assert pending_state.graph.status == "waiting"
    assert len(pending_state.pending_task_decisions) == 1
    pending = pending_state.pending_task_decisions[0]
    assert pending.status == "pending"
    assert waiting_step.pending is not None
    assert waiting_step.pending.request_id == pending.request_id

    restored_state = long_task_state_from_snapshot(
        json.loads(json.dumps(pending_state.snapshot()))
    )
    restored, _, _ = _runtime_fixture(tmp_path, mode="human")
    restored.current_state = restored_state
    assert restored.current_state.pending_task_decisions[0].request_id == pending.request_id

    invalid = restored.resume(
        pending=TaskGraphPending(kind="task", request_id=pending.request_id),
        payload={
            "decision": "approve",
            "response": {"decision": "approve"},
        },
        root_revision=restored_state.revision + 1,
    )
    assert invalid.outcome == "waiting"
    assert restored.current_state is not None
    assert restored.current_state.pending_task_decisions[0].status == "pending"
    assert restored.current_state.graph is not None
    assert restored.current_state.graph.status == "waiting"

    response = {
        "decision": "approve",
        "response": {"decision": "approve", "comment": "reviewed"},
    }
    resumed = restored.resume(
        pending=TaskGraphPending(kind="task", request_id=pending.request_id),
        payload=response,
        root_revision=restored.current_state.revision + 1,
    )
    assert resumed.outcome == "running"
    consumed = restored.current_state
    assert consumed is not None and consumed.graph is not None
    assert consumed.pending_task_decisions[0].status == "consumed"
    assert consumed.graph.status == "active"
    assert consumed.graph.tasks[0].status == "verifying"

    duplicate = restored.resume(
        pending=TaskGraphPending(kind="task", request_id=pending.request_id),
        payload=response,
        root_revision=consumed.revision + 1,
    )
    assert duplicate.outcome == "running"
    assert restored.current_state is consumed

    conflict = restored.resume(
        pending=TaskGraphPending(kind="task", request_id=pending.request_id),
        payload={
            "decision": "reject",
            "response": {"decision": "reject", "comment": "changed"},
        },
        root_revision=consumed.revision + 1,
    )
    assert conflict.error is not None
    assert restored.current_state is consumed

    for _ in range(8):
        current = restored.current_state
        assert current is not None and current.graph is not None
        if current.graph.tasks[0].status == "completed":
            break
        _advance(restored)
    final = restored.current_state
    assert final is not None and final.graph is not None
    assert final.graph.tasks[0].status == "completed"
    assert any(item.kind == "task" and item.outcome == "passed" for item in final.verification_records)


def test_unpinned_executor_binding_is_rejected(tmp_path: Path) -> None:
    runtime, _state, _component_value = _runtime_fixture(
        tmp_path,
        mode="parent_inline",
        binding_id="not-pinned",
    )

    failed = _advance(runtime)

    assert failed.outcome == "failed"
    assert failed.error is not None
    assert "component" in failed.error or "not-pinned" in failed.error
