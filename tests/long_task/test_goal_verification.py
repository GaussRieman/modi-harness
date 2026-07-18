"""Task 15 Goal verification outcome and final-criterion semantics."""

from __future__ import annotations

from collections.abc import Mapping
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
    GraphPatch,
    GraphPatchOperation,
    IntentCriterion,
    IntentVersion,
    LongTaskState,
    TaskGraphRun,
    TaskRun,
    VerificationRecord,
)
from modi_harness.long_task.verification import json_value
from modi_harness.workflow import (
    ExecutionContract,
    OperationAdapter,
    OperationAdapterRegistry,
    PinnedComponent,
    PinnedComponentRegistry,
    TaskGraphLimits,
    TaskGraphNodeConfig,
)
from modi_harness.workspace import TaskArtifactStore


class _UnusedDispatcher:
    def dispatch_task_operation(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("Goal tests must not dispatch executable work")

    def resume_task_operation(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise AssertionError("Goal tests must not resume executable work")


def _component(
    component_id: str,
    kind: str,
    implementation: Any,
    *,
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
        configuration={},
        implementation=implementation,
    )


def _fixture(
    tmp_path: Path,
    *,
    criterion_outcome: str = "passed",
    goal_outcome: str = "passed",
) -> tuple[
    OperationTaskGraphRuntime,
    dict[str, int],
    list[Mapping[str, Any]],
    dict[str, TaskRun],
]:
    calls = {"criterion": 0, "goal": 0, "planner": 0}
    planner_inputs: list[Mapping[str, Any]] = []
    holder: dict[str, TaskRun] = {}

    adapter = OperationAdapter(
        id="build-v1",
        version="1",
        kind="tool",
        target="build-v1",
        node_selectable=True,
        required_capabilities=(),
        side_effect=False,
        recovery_mode="pure",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    adapters = OperationAdapterRegistry()
    adapters.register(adapter)

    intent = IntentVersion(
        intent_id="intent-1",
        version=1,
        status="confirmed",
        goal="Deliver a verified result",
        desired_outcome="A verified result",
        success_criteria=(
            IntentCriterion(
                "criterion-1",
                "The result is valid",
                True,
                "validator",
                "criterion-v1",
            ),
        ),
        confirmation_proof_id="proof-v1",
    )

    def make_task(task_id: str, *, status: str = "pending") -> TaskRun:
        binding = ExecutorBinding(
            "operation",
            adapter.id,
            compute_fingerprint(adapter.snapshot()),
        )
        return TaskRun(
            task_id=task_id,
            task_revision=1,
            graph_id="graph-1",
            intent_version=intent.version,
            intent_binding_hash=compute_fingerprint(json_value(intent)),
            intent_binding_state="current",
            goal=f"Do {task_id}",
            supports=("criterion-1",),
            depends_on=(),
            priority=50,
            required=True,
            kind="executable",
            completion_contract=CompletionContract("result-v1", ("task-v1",)),
            executor_policy=ExecutorPolicy((binding,), binding),
            status=status,  # type: ignore[arg-type]
            output_refs=(f"result://{task_id}",) if status == "completed" else (),
        )

    done = make_task("done", status="completed")
    holder["repair"] = make_task("repair")

    def planner(
        inputs: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> GraphPatch:
        assert idempotency_key
        calls["planner"] += 1
        planner_inputs.append(inputs)
        return GraphPatch(
            base_revision=1,
            trigger="goal_gap",
            reason="repair the final Goal gap",
            operations=(
                GraphPatchOperation(
                    "add_repair_task",
                    task=holder["repair"],
                ),
            ),
        )

    def criterion(
        _inputs: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        assert idempotency_key
        calls["criterion"] += 1
        return {
            "outcome": criterion_outcome,
            "reason": "final criterion decision",
            "evidence_refs": ["evidence://final"],
        }

    def goal(
        _inputs: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        assert idempotency_key
        calls["goal"] += 1
        result: dict[str, Any] = {
            "outcome": goal_outcome,
            "reason": (
                "The Goal is impossible under the confirmed constraints"
                if goal_outcome == "impossible"
                else "Goal verifier decision"
            ),
        }
        if goal_outcome == "repairable_gap":
            result["gap"] = {"criterion_id": "criterion-1", "repair": "rebuild"}
        if goal_outcome == "ambiguous":
            result["criterion_gaps"] = [
                {"criterion_id": "criterion-1", "reason": "human judgment required"}
            ]
            result["options"] = [
                {"id": "repair", "label": "Repair"},
                {"id": "rebase", "label": "Change intent"},
            ]
        return result

    def passed(
        _inputs: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        assert idempotency_key
        return {"outcome": "passed"}

    components = PinnedComponentRegistry()
    component_values = (
        _component("planner-v1", "planner", planner),
        _component("policy-v1", "graph_policy", passed),
        _component("context-v1", "context_builder", passed),
        _component("task-v1", "task_verifier", passed),
        _component(
            "criterion-v1",
            "criterion_verifier",
            criterion,
            outcomes=("passed", "terminal"),
        ),
        _component(
            "goal-v1",
            "goal_verifier",
            goal,
            outcomes=("passed", "repairable_gap", "ambiguous", "impossible"),
        ),
    )
    for component in component_values:
        components.register(component)
    bindings = {
        "planner": component_values[0].snapshot(),
        "graph_policy": component_values[1].snapshot(),
        "context_builder": component_values[2].snapshot(),
        "task_validators": [component_values[3].snapshot()],
        "group_validators": [],
        "criterion_validators": [component_values[4].snapshot()],
        "goal_verifier": component_values[5].snapshot(),
    }
    config = TaskGraphNodeConfig(
        planner="planner-v1",
        graph_policy="policy-v1",
        context_builder="context-v1",
        task_validators=("task-v1",),
        group_validators=(),
        criterion_validators=("criterion-v1",),
        goal_verifier="goal-v1",
        operation_adapters=(adapter.id,),
        parent_inline_components=(),
        human_task_contracts=(),
        child_templates=(),
        limits=TaskGraphLimits(8, 4, 4, 1, 0),
    )
    contract = ExecutionContract(
        snapshot={
            "task_graph": {
                "nodes": [
                    {
                        "node_id": "execute",
                        "bindings": bindings,
                    }
                ]
            }
        },
        fingerprint="sha256:contract",
    )
    graph = TaskGraphRun(
        graph_id="graph-1",
        intent_id=intent.intent_id,
        intent_version=intent.version,
        revision=1,
        status="active",
        limits=GraphLimits(8, 4, 4, 1, 0),
        required_criteria=("criterion-1",),
        tasks=(done,),
        active_task_refs=(done.ref,),
    )
    incremental_record = VerificationRecord(
        record_id="criterion-incremental",
        kind="criterion",
        target_ref="criterion:criterion-1",
        component_fingerprint=component_values[4].fingerprint,
        input_hash="sha256:incremental-input",
        status="passed",
        evidence_refs=("evidence://incremental",),
        reason="incremental criterion passed",
        validator_id="criterion-v1",
        validator_version="1",
        outcome="passed",
    )
    state = LongTaskState(
        root_run_id="root-1",
        revision=1,
        intents=(intent,),
        graph=graph,
        verification_records=(incremental_record,),
        criterion_coverage=(
            CriterionCoverage(
                "criterion-1",
                "satisfied",
                evidence_refs=("evidence://incremental",),
                verified_by=incremental_record.record_id,
            ),
        ),
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
    return runtime, calls, planner_inputs, holder


def _advance_until_terminal(
    runtime: OperationTaskGraphRuntime,
    *,
    max_steps: int = 12,
):
    step = None
    for _ in range(max_steps):
        state = runtime.current_state
        assert state is not None
        step = runtime.advance(inputs={}, root_revision=state.revision + 1)
        if step.outcome in {"completed", "failed", "waiting"}:
            return step
    raise AssertionError("Goal verification did not reach a terminal or waiting outcome")


def test_final_goal_phase_rechecks_incrementally_satisfied_required_criterion(
    tmp_path: Path,
) -> None:
    runtime, calls, _planner_inputs, _holder = _fixture(tmp_path)

    step = _advance_until_terminal(runtime)

    assert step.outcome == "completed"
    assert calls["criterion"] == 1
    assert calls["goal"] == 1
    state = runtime.current_state
    assert state is not None
    records = [item for item in state.verification_records if item.kind == "criterion"]
    assert len(records) == 2
    assert records[-1].record_id != "criterion-incremental"
    assert records[-1].evidence_refs == ("evidence://final",)
    assert any(
        item.event_type == "criterion_verified"
        and item.payload.get("final") is True
        for item in state.events
    )


def test_failed_final_required_criterion_never_calls_goal_or_completes(
    tmp_path: Path,
) -> None:
    runtime, calls, _planner_inputs, _holder = _fixture(
        tmp_path,
        criterion_outcome="terminal",
    )

    step = _advance_until_terminal(runtime)

    assert step.outcome == "failed"
    assert calls["criterion"] == 1
    assert calls["goal"] == 0
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert state.graph.status == "failed"
    assert state.criterion_coverage[0].status == "blocked"


def test_repairable_goal_gap_triggers_validated_goal_gap_planning(
    tmp_path: Path,
) -> None:
    runtime, calls, planner_inputs, holder = _fixture(
        tmp_path,
        goal_outcome="repairable_gap",
    )

    for _ in range(10):
        state = runtime.current_state
        assert state is not None
        step = runtime.advance(inputs={}, root_revision=state.revision + 1)
        assert step.error is None
        graph = runtime.current_state.graph  # type: ignore[union-attr]
        if graph is not None and graph.revision == 2:
            break
    else:
        raise AssertionError("Goal-gap Planner patch was not applied")

    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert calls["goal"] == 1
    assert calls["planner"] == 1
    assert planner_inputs[0]["trigger"]["kind"] == "goal_gap"
    assert any(item.event_type == "goal_replan_requested" for item in state.events)
    assert holder["repair"].ref in state.graph.active_task_refs


def test_ambiguous_goal_persists_exact_pending_goal_decision(tmp_path: Path) -> None:
    runtime, calls, _planner_inputs, _holder = _fixture(
        tmp_path,
        goal_outcome="ambiguous",
    )

    step = _advance_until_terminal(runtime)

    assert step.outcome == "waiting"
    assert step.pending is not None and step.pending.kind == "goal"
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert state.graph.status == "waiting"
    assert calls["goal"] == 1
    assert len(state.pending_goal_decisions) == 1
    pending = state.pending_goal_decisions[0]
    assert pending.request_id == step.pending.request_id
    assert pending.graph_revision == state.graph.revision
    assert pending.expected_root_revision == state.revision
    assert pending.status == "pending"
    assert pending.goal_verification_record_id == state.verification_records[-1].record_id
    assert pending.criterion_gaps[0]["criterion_id"] == "criterion-1"
    assert {item["id"] for item in pending.options} == {"repair", "rebase"}


def test_impossible_goal_records_explicit_reason_and_fails(tmp_path: Path) -> None:
    runtime, calls, _planner_inputs, _holder = _fixture(
        tmp_path,
        goal_outcome="impossible",
    )

    step = _advance_until_terminal(runtime)

    assert step.outcome == "failed"
    assert "impossible under the confirmed constraints" in str(step.error)
    assert calls["goal"] == 1
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert state.graph.status == "failed"
    record = next(item for item in reversed(state.verification_records) if item.kind == "goal")
    assert record.outcome == "impossible"
    assert record.status == "terminal"
    assert record.reason == "The Goal is impossible under the confirmed constraints"
