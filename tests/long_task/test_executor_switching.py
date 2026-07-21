"""Task 15 executor-switching Attempt history and binding-closure tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task.runtime import OperationTaskGraphRuntime
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
    TaskAttempt,
    TaskGraphRun,
    TaskRun,
)
from modi_harness.long_task.verification import json_value
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


class _UnusedDispatcher:
    def dispatch_task_operation(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("switching tests stop before dispatch")

    def resume_task_operation(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("switching tests do not resume Operations")


def _adapter(adapter_id: str) -> OperationAdapter:
    return OperationAdapter(
        id=adapter_id,
        version="1",
        kind="tool",
        target=adapter_id,
        node_selectable=True,
        required_capabilities=(),
        side_effect=False,
        recovery_mode="pure",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def _component(component_id: str, kind: str, implementation: Any) -> PinnedComponent:
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
        implementation=implementation,
    )


def _fixture(
    tmp_path: Path,
    *,
    allowed_ids: tuple[str, ...],
    preferred_id: str,
    contract_adapter_ids: tuple[str, ...] = ("primary-op", "alternate-op"),
    dynamic_adapter_id: str | None = None,
) -> tuple[OperationTaskGraphRuntime, TaskAttempt, TaskRun]:
    adapters = OperationAdapterRegistry()
    adapter_values = {item: _adapter(item) for item in contract_adapter_ids}
    for adapter in adapter_values.values():
        adapters.register(adapter)

    def passed(_inputs: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        assert idempotency_key
        return {"outcome": "passed"}

    def context(
        inputs: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        assert idempotency_key
        return {
            "context_manifest": {"task_id": inputs["task"]["task_id"]},
            "operation_arguments": {},
        }

    components = PinnedComponentRegistry()
    component_values = (
        _component("planner-v1", "planner", passed),
        _component("policy-v1", "graph_policy", passed),
        _component("context-v1", "context_builder", context),
        _component("task-v1", "task_verifier", passed),
        _component("criterion-v1", "criterion_verifier", passed),
        _component("goal-v1", "goal_verifier", passed),
    )
    for component in component_values:
        components.register(component)
    config = TaskGraphNodeConfig(
        planner="planner-v1",
        graph_policy="policy-v1",
        context_builder="context-v1",
        task_validators=("task-v1",),
        group_validators=(),
        criterion_validators=("criterion-v1",),
        goal_verifier="goal-v1",
        operation_adapters=contract_adapter_ids,
        parent_inline_components=(),
        human_task_contracts=(),
        child_templates=(),
        limits=TaskGraphLimits(8, 4, 4, 1, 0),
    )
    workflow = Workflow(
        id="executor-switching",
        description="Executor switching fixture.",
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
        definition_fingerprint="executor-switching-v1",
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
    if dynamic_adapter_id is not None:
        dynamic = _adapter(dynamic_adapter_id)
        adapters.register(dynamic)
        adapter_values[dynamic_adapter_id] = dynamic

    intent = IntentVersion(
        intent_id="intent-1",
        version=1,
        status="confirmed",
        goal="Complete the Task",
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
        confirmation_proof_id="proof-v1",
    )
    bindings = {
        adapter_id: ExecutorBinding(
            "operation",
            adapter_id,
            compute_fingerprint(adapter_values[adapter_id].snapshot()),
        )
        for adapter_id in set((*allowed_ids, preferred_id, "primary-op"))
    }
    task = TaskRun(
        task_id="work",
        task_revision=1,
        graph_id="graph-1",
        intent_version=intent.version,
        intent_binding_hash=compute_fingerprint(json_value(intent)),
        intent_binding_state="current",
        goal="Do the work",
        supports=("criterion-1",),
        depends_on=(),
        priority=50,
        required=True,
        kind="executable",
        completion_contract=CompletionContract("result-v1", ("task-v1",)),
        executor_policy=ExecutorPolicy(
            tuple(bindings[item] for item in allowed_ids),
            bindings[preferred_id],
        ),
        status="pending",
    )
    old_attempt = TaskAttempt(
        attempt_id="attempt-primary",
        task_ref=task.ref,
        status="failed",
        executor_binding=bindings["primary-op"],
        context_manifest_ref="context://attempt-primary",
        completion_contract_hash=compute_fingerprint(
            json_value(task.completion_contract)
        ),
        dispatch_key="dispatch-primary",
        lease=LeaseRecord(
            "root-1",
            1,
            "lease-primary",
            "2026-07-18T10:00:00Z",
        ),
        parent_execution_contract_fingerprint=contract.fingerprint,
        failure="primary executor failed",
    )
    graph = TaskGraphRun(
        graph_id="graph-1",
        intent_id=intent.intent_id,
        intent_version=intent.version,
        revision=1,
        status="active",
        limits=GraphLimits(8, 4, 4, 1, 0),
        required_criteria=("criterion-1",),
        tasks=(task,),
        active_task_refs=(task.ref,),
    )
    state = LongTaskState(
        root_run_id="root-1",
        revision=1,
        intents=(intent,),
        graph=graph,
        attempts=(old_attempt,),
        criterion_coverage=(CriterionCoverage("criterion-1", "unsatisfied"),),
    )
    runtime = OperationTaskGraphRuntime(
        root_run_id="root-1",
        node_id="execute",
        config=config,
        contract=contract,
        components=components,
        adapters=adapters,
        dispatcher=_UnusedDispatcher(),
        artifacts=TaskArtifactStore(tmp_path / "artifacts"),
        state=state,
    )
    return runtime, old_attempt, task


def _prepare_selected_attempt(runtime: OperationTaskGraphRuntime):
    prepared_context = runtime.advance(inputs={}, root_revision=2)
    if prepared_context.outcome == "failed":
        return prepared_context
    return runtime.advance(inputs={}, root_revision=3)


def test_failed_old_attempt_switch_creates_distinct_new_attempt(
    tmp_path: Path,
) -> None:
    runtime, old_attempt, task = _fixture(
        tmp_path,
        allowed_ids=("primary-op", "alternate-op"),
        preferred_id="alternate-op",
    )

    step = _prepare_selected_attempt(runtime)

    assert step.outcome == "running"
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert len(state.attempts) == 2
    new_attempt = state.attempts[-1]
    assert new_attempt.attempt_id != old_attempt.attempt_id
    assert new_attempt.task_ref == task.ref
    assert new_attempt.executor_binding.id == "alternate-op"
    assert new_attempt.status == "created"
    current_task = state.graph.tasks[0]
    assert current_task.active_attempt_id == new_attempt.attempt_id
    assert current_task.status == "running"


def test_executor_switch_preserves_failed_attempt_history(tmp_path: Path) -> None:
    runtime, old_attempt, _task = _fixture(
        tmp_path,
        allowed_ids=("primary-op", "alternate-op"),
        preferred_id="alternate-op",
    )
    before = runtime.current_state
    assert before is not None and before.attempts == (old_attempt,)

    _prepare_selected_attempt(runtime)

    state = runtime.current_state
    assert state is not None
    assert state.attempts[0] == old_attempt
    assert state.attempts[0].status == "failed"
    assert state.attempts[0].failure == "primary executor failed"


def test_alternate_binding_must_be_in_task_allowed_bindings(tmp_path: Path) -> None:
    runtime, old_attempt, _task = _fixture(
        tmp_path,
        allowed_ids=("primary-op",),
        preferred_id="alternate-op",
    )

    step = _prepare_selected_attempt(runtime)

    assert step.outcome == "failed"
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert state.graph.status == "failed"
    assert state.attempts == (old_attempt,)
    assert "preferred executor is not allowed" in str(step.error)


def test_contract_pinned_allowed_alternate_is_accepted(tmp_path: Path) -> None:
    runtime, _old_attempt, _task = _fixture(
        tmp_path,
        allowed_ids=("primary-op", "alternate-op"),
        preferred_id="alternate-op",
    )

    step = _prepare_selected_attempt(runtime)

    assert step.outcome == "running"
    state = runtime.current_state
    assert state is not None
    assert state.attempts[-1].executor_binding.id == "alternate-op"
    assert state.attempts[-1].executor_binding in _task.executor_policy.allowed_bindings


def test_dynamically_registered_but_unpinned_binding_is_rejected(
    tmp_path: Path,
) -> None:
    runtime, old_attempt, _task = _fixture(
        tmp_path,
        allowed_ids=("primary-op", "dynamic-op"),
        preferred_id="dynamic-op",
        contract_adapter_ids=("primary-op",),
        dynamic_adapter_id="dynamic-op",
    )

    step = _prepare_selected_attempt(runtime)

    assert step.outcome == "failed"
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert state.graph.status == "failed"
    assert state.attempts == (old_attempt,)
    assert "unpinned Operation adapter" in str(step.error)
