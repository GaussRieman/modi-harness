"""Parent-owned Group verification and committed join outputs."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from modi_harness.long_task import (
    CompletionContract,
    CriterionCoverage,
    ExecutorBinding,
    ExecutorPolicy,
    GroupChildRef,
    GroupRun,
    IntentCriterion,
    IntentVersion,
    LeaseRecord,
    LongTaskState,
    TaskAttempt,
)
from modi_harness.long_task.runtime import OperationTaskGraphRuntime
from modi_harness.workflow import (
    ExecutionContract,
    OperationAdapterRegistry,
    PinnedComponent,
    PinnedComponentRegistry,
    TaskGraphLimits,
    TaskGraphNodeConfig,
)
from modi_harness.workspace import TaskArtifactStore

from .helpers import graph, task, with_status


class _UnusedDispatcher:
    pass


def _component(component_id: str, kind: str, implementation: Any) -> PinnedComponent:
    return PinnedComponent(
        id=component_id,
        version="1",
        kind=kind,  # type: ignore[arg-type]
        implementation_digest=f"sha256:{component_id}",
        protocol_version="v1",
        input_schema_id=f"{component_id}-input",
        output_schema_id=f"{component_id}-output",
        supported_outcomes=("passed", "terminal"),
        configuration={},
        implementation=implementation,
    )


def _runtime(
    tmp_path,
    state,
    *components: PinnedComponent,
    child_bridge: Any | None = None,
) -> OperationTaskGraphRuntime:
    registry = PinnedComponentRegistry()
    for component in components:
        registry.register(component)
    bindings = {
        "group_validators": [
            component.snapshot()
            for component in components
            if component.kind == "group_verifier"
        ],
        "criterion_validators": [
            component.snapshot()
            for component in components
            if component.kind == "criterion_verifier"
        ],
    }
    return OperationTaskGraphRuntime(
        root_run_id="root-1",
        node_id="execute",
        config=TaskGraphNodeConfig(
            planner="planner",
            graph_policy="policy",
            context_builder="context",
            task_validators=(),
            group_validators=tuple(
                component.id
                for component in components
                if component.kind == "group_verifier"
            ),
            criterion_validators=tuple(
                component.id
                for component in components
                if component.kind == "criterion_verifier"
            ),
            goal_verifier="goal",
            operation_adapters=(),
            parent_inline_components=(),
            human_task_contracts=(),
            child_templates=(),
            limits=TaskGraphLimits(8, 4, 2, 2, 0),
        ),
        contract=ExecutionContract(
            snapshot={
                "task_graph": {
                    "nodes": [{"node_id": "execute", "bindings": bindings}]
                }
            },
            fingerprint="sha256:contract",
        ),
        components=registry,
        adapters=OperationAdapterRegistry(),
        dispatcher=_UnusedDispatcher(),  # type: ignore[arg-type]
        artifacts=TaskArtifactStore(tmp_path / "artifacts"),
        child_bridge=child_bridge,
        state=state,
    )


def _state(group: GroupRun, *tasks) -> LongTaskState:
    graph_value = replace(
        graph(*tasks),
        groups=(group,),
        active_group_refs=(group.ref,),
    )
    return LongTaskState(
        root_run_id="root-1",
        revision=1,
        intents=(
            IntentVersion(
                "intent-1",
                1,
                "confirmed",
                "Choose a valid result",
                "A verified result",
                (IntentCriterion("criterion-1", "valid", True, "verifier"),),
            ),
        ),
        graph=graph_value,
        criterion_coverage=(CriterionCoverage("criterion-1", "unsatisfied"),),
    )


def _group(first, second, *, status: str = "running") -> GroupRun:
    return GroupRun(
        group_id="options",
        group_revision=1,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        supports=("criterion-1",),
        required=True,
        depends_on=(),
        completion_contract=CompletionContract("group-v1", ("group-v1",)),
        children=(GroupChildRef(first.ref, True), GroupChildRef(second.ref, True)),
        join_policy="any_success",
        failure_behavior="cancel_unneeded",
        status=status,  # type: ignore[arg-type]
    )


def test_group_verifier_rejection_tries_next_completed_candidate(tmp_path) -> None:
    alpha = with_status(task("alpha", priority=90), "completed")
    beta = with_status(task("beta", priority=80), "completed")
    group = _group(alpha, beta)
    calls: list[str] = []

    def verify(inputs, *, idempotency_key):
        del idempotency_key
        winner = inputs["winner"]["task_id"]
        calls.append(winner)
        return {
            "outcome": "terminal" if winner == "alpha" else "passed",
            "reason": "candidate decision",
        }

    runtime = _runtime(
        tmp_path,
        _state(group, alpha, beta),
        _component("group-v1", "group_verifier", verify),
    )

    for revision in range(2, 6):
        step = runtime.advance(inputs={}, root_revision=revision)
        assert step.error is None

    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    joined = committed.graph.groups[0]
    assert calls == ["alpha", "beta"]
    assert joined.status == "completed"
    assert joined.winner_task_ref == beta.ref
    assert [record.outcome for record in committed.verification_records] == [
        "terminal",
        "passed",
    ]
    assert committed.verification_records[0].artifact_refs == ("task:alpha:1",)


def test_group_children_are_not_double_counted_by_criterion_verifier(tmp_path) -> None:
    winner = replace(
        with_status(task("winner"), "completed"),
        output_refs=("result://winner",),
    )
    loser = replace(
        with_status(task("loser"), "completed"),
        output_refs=("result://loser",),
    )
    group = replace(
        _group(winner, loser, status="completed"),
        winner_task_ref=winner.ref,
        verification_record_ref="verification://group",
    )
    seen: list[dict[str, Any]] = []

    def verify(inputs, *, idempotency_key):
        del idempotency_key
        seen.append(inputs)
        return {"outcome": "passed"}

    runtime = _runtime(
        tmp_path,
        _state(group, winner, loser),
        _component("criterion-v1", "criterion_verifier", verify),
    )
    runtime.advance(inputs={}, root_revision=2)
    runtime.advance(inputs={}, root_revision=3)

    assert len(seen) == 1
    assert seen[0]["tasks"] == []
    assert [item["group_id"] for item in seen[0]["groups"]] == ["options"]
    downstream = task("downstream", depends_on=(group.ref,))
    assert runtime._dependency_outputs(runtime.current_state, downstream) == [  # type: ignore[arg-type]
        "result://winner"
    ]


def test_one_child_runtime_failure_is_committed_without_failing_group_graph(tmp_path) -> None:
    child_binding = ExecutorBinding("child_agent", "worker", "sha256:worker")
    alpha = replace(
        task("alpha", status="running"),
        executor_policy=ExecutorPolicy((child_binding,), child_binding),
        active_attempt_id="attempt-alpha",
    )
    beta = replace(
        task("beta", status="running"),
        executor_policy=ExecutorPolicy((child_binding,), child_binding),
        active_attempt_id="attempt-beta",
    )
    group = _group(alpha, beta)
    attempts = tuple(
        TaskAttempt(
            attempt_id=f"attempt-{item.task_id}",
            task_ref=item.ref,
            status="running",
            executor_binding=child_binding,
            context_manifest_ref=f"context://{item.task_id}",
            completion_contract_hash="sha256:contract",
            dispatch_key=f"dispatch-{item.task_id}",
            lease=LeaseRecord("root-1", 1, f"token-{item.task_id}", "2099-01-01T00:00:00Z"),
            parent_execution_contract_fingerprint="sha256:contract",
            child_run_id=f"child-{item.task_id}",
        )
        for item in (alpha, beta)
    )

    class _ChildBridge:
        def advance_child(self, attempt):
            if attempt.task_ref == alpha.ref:
                error = RuntimeError("alpha crashed")
                error.observation_revision = 6  # type: ignore[attr-defined]
                error.observation_status = "failed"  # type: ignore[attr-defined]
                raise error
            return None

    state = replace(_state(group, alpha, beta), attempts=attempts)
    runtime = _runtime(tmp_path, state, child_bridge=_ChildBridge())

    step = runtime.advance(inputs={}, root_revision=2)

    assert step.outcome == "running"
    assert step.error is None
    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.status == "active"
    assert next(item for item in committed.graph.tasks if item.ref == alpha.ref).status == "failed"
    assert next(item for item in committed.graph.tasks if item.ref == beta.ref).status == "running"
    failed_attempt = next(item for item in committed.attempts if item.task_ref == alpha.ref)
    assert failed_attempt.child_observation_revision == 6
    assert failed_attempt.child_observation_status == "failed"
    plan = runtime.task_plan()
    assert plan is not None
    failed_item = next(item for item in plan["items"] if item["id"] == "alpha")
    assert failed_item["child"] == {
        "run_id": "child-alpha",
        "status": "failed",
        "revision": 6,
    }


def test_optional_ungrouped_child_failure_does_not_fail_graph(tmp_path) -> None:
    child_binding = ExecutorBinding("child_agent", "worker", "sha256:worker")
    core = with_status(task("core"), "completed")
    followup = replace(
        task("followup", status="running", supports=()),
        required=False,
        executor_policy=ExecutorPolicy((child_binding,), child_binding),
        active_attempt_id="attempt-followup",
    )
    placeholder_group = _group(core, followup)
    state = _state(placeholder_group, core, followup)
    assert state.graph is not None
    state = replace(
        state,
        graph=replace(state.graph, groups=(), active_group_refs=()),
        attempts=(
            TaskAttempt(
                attempt_id="attempt-followup",
                task_ref=followup.ref,
                status="running",
                executor_binding=child_binding,
                context_manifest_ref="context://followup",
                completion_contract_hash="sha256:contract",
                dispatch_key="dispatch-followup",
                lease=LeaseRecord(
                    "root-1",
                    1,
                    "token-followup",
                    "2099-01-01T00:00:00Z",
                ),
                parent_execution_contract_fingerprint="sha256:contract",
                child_run_id="child-followup",
            ),
        ),
        criterion_coverage=(CriterionCoverage("criterion-1", "satisfied"),),
    )

    class _ChildBridge:
        def advance_child(self, attempt):
            raise RuntimeError("follow-up planner response was malformed")

    runtime = _runtime(tmp_path, state, child_bridge=_ChildBridge())

    step = runtime.advance(inputs={}, root_revision=2)

    assert step.outcome == "running"
    assert step.error is None
    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.status == "active"
    failed = next(item for item in committed.graph.tasks if item.ref == followup.ref)
    assert failed.status == "failed"
    assert next(item for item in committed.graph.tasks if item.ref == core.ref).status == "completed"


def test_required_ungrouped_child_failure_still_fails_graph(tmp_path) -> None:
    child_binding = ExecutorBinding("child_agent", "worker", "sha256:worker")
    required = replace(
        task("required", status="running"),
        executor_policy=ExecutorPolicy((child_binding,), child_binding),
        active_attempt_id="attempt-required",
    )
    sibling = task("sibling", status="running")
    placeholder_group = _group(required, sibling)
    state = _state(placeholder_group, required, sibling)
    assert state.graph is not None
    state = replace(
        state,
        graph=replace(state.graph, groups=(), active_group_refs=()),
        attempts=(
            TaskAttempt(
                attempt_id="attempt-required",
                task_ref=required.ref,
                status="running",
                executor_binding=child_binding,
                context_manifest_ref="context://required",
                completion_contract_hash="sha256:contract",
                dispatch_key="dispatch-required",
                lease=LeaseRecord(
                    "root-1",
                    1,
                    "token-required",
                    "2099-01-01T00:00:00Z",
                ),
                parent_execution_contract_fingerprint="sha256:contract",
                child_run_id="child-required",
            ),
        ),
    )

    class _ChildBridge:
        def advance_child(self, attempt):
            raise RuntimeError("required child failed")

    runtime = _runtime(tmp_path, state, child_bridge=_ChildBridge())

    step = runtime.advance(inputs={}, root_revision=2)

    assert step.outcome == "failed"
    assert "required child failed" in str(step.error)
    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.status == "failed"


def test_failed_optional_task_does_not_block_required_criterion(tmp_path) -> None:
    core = replace(
        with_status(task("core"), "completed"),
        goal=json.dumps(
            {
                "schema_version": "research-task-goal-v1",
                "title": "VLA路线核心企业深度调研",
                "question": "哪些企业在推动VLA路线?",
            },
            ensure_ascii=False,
        ),
        required=False,
    )
    optional = replace(
        with_status(task("optional"), "failed"),
        failure="max_auto_steps_reached",
        required=False,
    )
    placeholder_group = _group(core, optional)
    state = _state(placeholder_group, core, optional)
    assert state.graph is not None
    state = replace(
        state,
        graph=replace(state.graph, groups=(), active_group_refs=()),
    )
    runtime = _runtime(
        tmp_path,
        state,
        _component(
            "criterion-v1",
            "criterion_verifier",
            lambda _inputs, *, idempotency_key: {"outcome": "passed"},
        ),
    )

    runtime.advance(inputs={}, root_revision=2)
    step = runtime.advance(inputs={}, root_revision=3)

    assert step.outcome == "running"
    committed = runtime.current_state
    assert committed is not None
    assert committed.criterion_coverage[0].status == "satisfied"
    assert committed.events[-1].event_type == "criterion_verified"
    output = runtime._node_output(committed)
    assert output["task_failures"] == [
        {
            "task_id": "optional",
            "title": "Do optional",
            "reason": "max_auto_steps_reached",
        }
    ]
    plan = runtime.task_plan()
    assert plan is not None
    assert next(item for item in plan["items"] if item["id"] == "core")["title"] == (
        "VLA路线核心企业深度调研"
    )
