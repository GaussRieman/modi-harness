"""Runtime integration for durable rolling-wave Planner triggers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import (
    CandidateReceipt,
    CandidateSubmission,
    CompletionContract,
    CriterionCoverage,
    ExecutorBinding,
    ExecutorPolicy,
    GraphLimits,
    GraphPatch,
    GraphPatchOperation,
    GroupChildRef,
    GroupRun,
    IntentCriterion,
    IntentVersion,
    LongTaskState,
    TaskGraphRun,
    TaskRun,
)
from modi_harness.long_task.runtime import OperationTaskGraphRuntime
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
    pass


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


def _fixture(tmp_path, planner, *, goal=None):
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
    planner_component = _component("planner-v1", "planner", planner)
    task_component = _component(
        "task-v1",
        "task_verifier",
        lambda _inputs, *, idempotency_key: {"outcome": "passed"},
    )
    group_component = _component(
        "group-v1",
        "group_verifier",
        lambda _inputs, *, idempotency_key: {"outcome": "passed"},
    )
    components = PinnedComponentRegistry()
    for component in (planner_component, task_component, group_component):
        components.register(component)
    bindings: dict[str, Any] = {
        "planner": planner_component.snapshot(),
        "task_validators": [task_component.snapshot()],
        "group_validators": [group_component.snapshot()],
    }
    goal_ids: tuple[str, ...] = ()
    if goal is not None:
        goal_component = _component(
            "goal-v1",
            "goal_verifier",
            goal,
            outcomes=("passed", "repairable_gap"),
        )
        components.register(goal_component)
        bindings["goal_verifier"] = goal_component.snapshot()
        goal_ids = (goal_component.id,)
    config = TaskGraphNodeConfig(
        planner="planner-v1",
        graph_policy="policy-v1",
        context_builder="context-v1",
        task_validators=(task_component.id,),
        group_validators=(group_component.id,),
        criterion_validators=(),
        goal_verifier=goal_ids[0] if goal_ids else "goal-v1",
        operation_adapters=(adapter.id,),
        parent_inline_components=(),
        human_task_contracts=(),
        child_templates=(),
        limits=TaskGraphLimits(12, 6, 4, 2, 0),
    )
    contract = ExecutionContract(
        snapshot={
            "task_graph": {
                "nodes": [{"node_id": "execute", "bindings": bindings}]
            }
        },
        fingerprint="sha256:contract",
    )
    intent = IntentVersion(
        "intent-1",
        1,
        "confirmed",
        "Deliver a verified result",
        "Verified result",
        (IntentCriterion("criterion-1", "verified", True, "verifier"),),
    )

    def make_task(
        task_id: str,
        *,
        kind: str = "executable",
        status: str = "pending",
        depends_on=(),
        output_refs=(),
    ) -> TaskRun:
        binding = ExecutorBinding(
            "operation",
            adapter.id,
            compute_fingerprint(adapter.snapshot()),
        )
        return TaskRun(
            task_id=task_id,
            task_revision=1,
            graph_id="graph-1",
            intent_version=1,
            intent_binding_hash=compute_fingerprint(json_value(intent)),
            intent_binding_state="current",
            goal=f"Do {task_id}",
            supports=("criterion-1",),
            depends_on=tuple(depends_on),
            priority=50,
            required=True,
            kind=kind,  # type: ignore[arg-type]
            completion_contract=CompletionContract("result-v1", ("task-v1",)),
            executor_policy=ExecutorPolicy((binding,), binding),
            status=status,  # type: ignore[arg-type]
            output_refs=tuple(output_refs),
        )

    def runtime(state: LongTaskState) -> OperationTaskGraphRuntime:
        return OperationTaskGraphRuntime(
            root_run_id="root-1",
            node_id="execute",
            config=config,
            contract=contract,
            components=components,
            adapters=adapters,
            dispatcher=_UnusedDispatcher(),  # type: ignore[arg-type]
            artifacts=TaskArtifactStore(tmp_path / "artifacts"),
            state=state,
        )

    def state(*tasks: TaskRun, status: str = "active") -> LongTaskState:
        graph = TaskGraphRun(
            graph_id="graph-1",
            intent_id=intent.intent_id,
            intent_version=intent.version,
            revision=1,
            status=status,  # type: ignore[arg-type]
            limits=GraphLimits(12, 6, 4, 2, 0),
            required_criteria=("criterion-1",),
            tasks=tasks,
            active_task_refs=tuple(item.ref for item in tasks),
        )
        return LongTaskState(
            root_run_id="root-1",
            revision=1,
            intents=(intent,),
            graph=graph,
            criterion_coverage=(CriterionCoverage("criterion-1", "unsatisfied"),),
        )

    return runtime, state, make_task


def test_runtime_lazily_expands_ready_task_with_durable_planner_invocation(tmp_path) -> None:
    holder: dict[str, TaskRun] = {}

    def planner(inputs, *, idempotency_key):
        assert idempotency_key and inputs["trigger"]["kind"] == "expandable_ready"
        expandable = holder["expandable"]
        child = holder["child"]
        group = GroupRun(
            "phase-group",
            1,
            "graph-1",
            1,
            expandable.intent_binding_hash,
            "current",
            expandable.supports,
            True,
            expandable.depends_on,
            CompletionContract("group-v1", ("group-v1",)),
            (GroupChildRef(child.ref, True),),
            "all_required",
            "fail_group",
        )
        return GraphPatch(
            1,
            "expandable_ready",
            "materialize near-term work",
            (
                GraphPatchOperation(
                    "expand_task",
                    task_id=expandable.task_id,
                    expected_revision=1,
                    group=group,
                    child_tasks=(child,),
                ),
            ),
        )

    runtime_factory, state_factory, make_task = _fixture(tmp_path, planner)
    expandable = make_task("phase", kind="expandable")
    child = make_task("child")
    holder.update(expandable=expandable, child=child)
    runtime = runtime_factory(state_factory(expandable))

    runtime.advance(inputs={}, root_revision=2)
    runtime.advance(inputs={}, root_revision=3)

    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.revision == 2
    assert committed.component_invocations[0].status == "completed"
    assert any(item.task_id == "child" for item in committed.graph.tasks)
    assert committed.events[-1].event_type == "graph_patch_applied"


def test_invalid_planner_patch_gets_bounded_feedback_then_repairs(tmp_path) -> None:
    calls: list[dict[str, Any]] = []
    holder: dict[str, TaskRun] = {}

    def planner(inputs, *, idempotency_key):
        del idempotency_key
        calls.append(inputs)
        if len(calls) == 1:
            return GraphPatch(1, "expandable_ready", "invalid", ())
        expandable = holder["expandable"]
        child = holder["child"]
        group = GroupRun(
            "phase-group",
            1,
            "graph-1",
            1,
            expandable.intent_binding_hash,
            "current",
            expandable.supports,
            True,
            (),
            CompletionContract("group-v1", ("group-v1",)),
            (GroupChildRef(child.ref, True),),
            "all_required",
            "fail_group",
        )
        return GraphPatch(
            1,
            "expandable_ready",
            "fixed patch",
            (
                GraphPatchOperation(
                    "expand_task",
                    task_id="phase",
                    expected_revision=1,
                    group=group,
                    child_tasks=(child,),
                ),
            ),
        )

    runtime_factory, state_factory, make_task = _fixture(tmp_path, planner)
    expandable = make_task("phase", kind="expandable")
    holder.update(expandable=expandable, child=make_task("child"))
    runtime = runtime_factory(state_factory(expandable))

    for revision in range(2, 6):
        runtime.advance(inputs={}, root_revision=revision)

    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.revision == 2
    assert len(calls) == 2
    assert "repair_feedback" in calls[1]["trigger"]["details"]
    assert [event.event_type for event in committed.events[-2:]] == [
        "planner_invocation_prepared",
        "graph_patch_applied",
    ]


def test_unpinned_planner_binding_is_repairable_feedback_not_graph_failure(tmp_path) -> None:
    calls: list[dict[str, Any]] = []
    holder: dict[str, TaskRun] = {}

    def planner(inputs, *, idempotency_key):
        del idempotency_key
        calls.append(inputs)
        expandable = holder["expandable"]
        child = holder["child"]
        if len(calls) == 1:
            forbidden = ExecutorBinding("operation", "forbidden-op", "sha256:forbidden")
            child = replace(
                child,
                executor_policy=ExecutorPolicy((forbidden,), forbidden),
            )
        group = GroupRun(
            "phase-group",
            1,
            "graph-1",
            1,
            expandable.intent_binding_hash,
            "current",
            expandable.supports,
            True,
            (),
            CompletionContract("group-v1", ("group-v1",)),
            (GroupChildRef(child.ref, True),),
            "all_required",
            "fail_group",
        )
        return GraphPatch(
            1,
            "expandable_ready",
            "expand with a pinned worker",
            (
                GraphPatchOperation(
                    "expand_task",
                    task_id=expandable.task_id,
                    expected_revision=1,
                    group=group,
                    child_tasks=(child,),
                ),
            ),
        )

    runtime_factory, state_factory, make_task = _fixture(tmp_path, planner)
    expandable = make_task("phase", kind="expandable")
    holder.update(expandable=expandable, child=make_task("child"))
    runtime = runtime_factory(state_factory(expandable))

    for revision in range(2, 6):
        step = runtime.advance(inputs={}, root_revision=revision)
        assert step.error is None

    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.status == "active"
    assert committed.graph.revision == 2
    rejection = next(
        event for event in committed.events if event.event_type == "planner_patch_rejected"
    )
    assert "unpinned Operation adapter" in str(rejection.payload["feedback"])
    assert "repair_feedback" in calls[1]["trigger"]["details"]


def test_unrelated_legal_patch_cannot_consume_expandable_trigger(tmp_path) -> None:
    calls = 0
    holder: dict[str, TaskRun] = {}

    def planner(_inputs, *, idempotency_key):
        nonlocal calls
        assert idempotency_key
        calls += 1
        if calls == 1:
            return GraphPatch(
                1,
                "expandable_ready",
                "change unrelated priority",
                (
                    GraphPatchOperation(
                        "set_priority",
                        task_id="other",
                        expected_revision=1,
                        priority=90,
                    ),
                ),
            )
        expandable = holder["expandable"]
        child = holder["child"]
        group = GroupRun(
            "phase-group",
            1,
            "graph-1",
            1,
            expandable.intent_binding_hash,
            "current",
            expandable.supports,
            True,
            (),
            CompletionContract("group-v1", ("group-v1",)),
            (GroupChildRef(child.ref, True),),
            "all_required",
            "fail_group",
        )
        return GraphPatch(
            1,
            "expandable_ready",
            "resolve the exact frontier",
            (
                GraphPatchOperation(
                    "expand_task",
                    task_id="phase",
                    expected_revision=1,
                    group=group,
                    child_tasks=(child,),
                ),
            ),
        )

    runtime_factory, state_factory, make_task = _fixture(tmp_path, planner)
    expandable = make_task("phase", kind="expandable")
    other = make_task("other")
    holder.update(expandable=expandable, child=make_task("child"))
    runtime = runtime_factory(state_factory(expandable, other))

    for revision in range(2, 6):
        runtime.advance(inputs={}, root_revision=revision)

    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.revision == 2
    assert calls == 2
    rejection = next(
        event for event in committed.events if event.event_type == "planner_patch_rejected"
    )
    assert "did not resolve exact expandable_ready target" in str(
        rejection.payload["feedback"]
    )


def test_goal_gap_returns_to_active_and_applies_repair_patch(tmp_path) -> None:
    holder: dict[str, TaskRun] = {}

    def planner(inputs, *, idempotency_key):
        assert idempotency_key and inputs["trigger"]["kind"] == "goal_gap"
        return GraphPatch(
            1,
            "goal_gap",
            "add exact repair",
            (GraphPatchOperation("add_repair_task", task=holder["repair"]),),
        )

    def goal(_inputs, *, idempotency_key):
        assert idempotency_key
        return {"outcome": "repairable_gap", "reason": "one gap remains"}

    runtime_factory, state_factory, make_task = _fixture(tmp_path, planner, goal=goal)
    done = make_task("done", status="completed", output_refs=("result://done",))
    holder["repair"] = make_task("repair")
    state = state_factory(done, status="verifying")
    state = replace(
        state,
        criterion_coverage=(CriterionCoverage("criterion-1", "satisfied"),),
    )
    runtime = runtime_factory(state)

    for revision in range(2, 6):
        step = runtime.advance(inputs={}, root_revision=revision)
        assert step.error is None

    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.status == "active"
    assert committed.graph.revision == 2
    assert any(item.task_id == "repair" for item in committed.graph.tasks)
    assert any(
        event.event_type == "goal_replan_requested" for event in committed.events
    )


def test_live_steering_has_durable_received_and_applied_events(tmp_path) -> None:
    holder: dict[str, TaskRun] = {}

    def planner(inputs, *, idempotency_key):
        assert idempotency_key
        assert inputs["trigger"]["kind"] == "user_change"
        assert inputs["trigger"]["details"]["feedback"] == "focus on winter range"
        current = holder["pending"]
        return GraphPatch(
            1,
            "user_change",
            "apply live priority",
            (
                GraphPatchOperation(
                    "set_priority",
                    task_id=current.task_id,
                    expected_revision=current.task_revision,
                    priority=100,
                ),
            ),
        )

    runtime_factory, state_factory, make_task = _fixture(tmp_path, planner)
    holder["pending"] = make_task("pending")
    runtime = runtime_factory(state_factory(holder["pending"]))

    received = runtime.receive_user_steering(
        request_id="steer-1",
        feedback="focus on winter range",
        received_at="2026-07-22T10:00:00Z",
        root_revision=2,
    )
    assert received.outcome == "running"
    assert runtime.current_state is not None
    assert runtime.current_state.events[-1].event_type == "user_steering_received"

    runtime.advance(inputs={}, root_revision=3)
    applied = runtime.advance(inputs={}, root_revision=4)

    assert applied.outcome == "running"
    assert runtime.current_state is not None and runtime.current_state.graph is not None
    assert runtime.current_state.events[-1].event_type == "user_steering_applied"
    active = next(
        item
        for item in runtime.current_state.graph.tasks
        if item.ref in runtime.current_state.graph.active_task_refs
    )
    assert active.priority == 100


def test_accepted_child_discovered_work_is_untrusted_planner_input(tmp_path) -> None:
    seen: list[dict[str, Any]] = []
    holder: dict[str, TaskRun] = {}

    def planner(inputs, *, idempotency_key):
        assert idempotency_key
        seen.append(inputs)
        if len(seen) == 1:
            return GraphPatch(
                1,
                "discovered_work",
                "unrelated priority change",
                (
                    GraphPatchOperation(
                        "set_priority",
                        task_id="other",
                        expected_revision=1,
                        priority=90,
                    ),
                ),
            )
        return GraphPatch(
            1,
            "discovered_work",
            "validate the suggested edge case",
            (GraphPatchOperation("add_task", task=holder["followup"]),),
        )

    runtime_factory, state_factory, make_task = _fixture(tmp_path, planner)
    done = make_task("done", status="completed", output_refs=("result://done",))
    other = make_task("other")
    holder["followup"] = make_task("followup")
    submission = CandidateSubmission(
        submission_id="submission-discovery",
        submission_sequence=1,
        task_ref=done.ref,
        attempt_id="attempt-done",
        child_run_id="child-done",
        lease_epoch=1,
        lease_token="lease-done",
        context_manifest_fingerprint="sha256:manifest",
        completion_contract_hash="sha256:completion",
        parent_execution_contract_fingerprint="sha256:contract",
        outcome="candidate_completed",
        result={"answer": "done"},
        discovered_work=(
            {
                "goal": "Inspect an edge case",
                "rationale": "child observation",
                "suggested_dependencies": ["done"],
                "operations": [{"op": "cancel_pending_task"}],
            },
        ),
    )
    receipt = CandidateReceipt(
        submission_id=submission.submission_id,
        attempt_id=submission.attempt_id,
        submission_sequence=1,
        payload_hash=submission.payload_hash,
        status="accepted",
        task_ref=done.ref,
        submission_snapshot=submission.snapshot(),
        decision="accepted",
    )
    state = replace(state_factory(done, other), receipts=(receipt,))
    runtime = runtime_factory(state)

    for revision in range(2, 6):
        runtime.advance(inputs={}, root_revision=revision)

    committed = runtime.current_state
    assert committed is not None and committed.graph is not None
    assert committed.graph.revision == 2
    discovered = seen[0]["context"]["discovered_work"][0]
    assert discovered["trust_level"] == "untrusted"
    assert "operations" not in discovered
    rejection = next(
        event for event in committed.events if event.event_type == "planner_patch_rejected"
    )
    assert "did not add or expand" in str(rejection.payload["feedback"])
