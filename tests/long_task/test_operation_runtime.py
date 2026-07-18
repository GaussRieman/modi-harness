"""Operation-only Task Graph vertical runtime tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import (
    CompletionContract,
    DependencyRef,
    ExecutorBinding,
    ExecutorPolicy,
    GraphPatch,
    GraphPatchOperation,
    TaskRun,
    long_task_state_from_snapshot,
)
from modi_harness.long_task.runtime import OperationTaskGraphRuntime
from modi_harness.workflow import (
    CompletionValidator,
    CompletionValidatorRegistry,
    Node,
    OperationAdapter,
    OperationAdapterRegistry,
    OperationDispatchResult,
    PinnedComponent,
    PinnedComponentRegistry,
    TaskGraphLimits,
    TaskGraphNodeConfig,
    Workflow,
    build_execution_contract,
)
from modi_harness.workspace import TaskArtifactStore


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def dispatch_task_operation(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        dispatch_key: str,
    ) -> OperationDispatchResult:
        assert dispatch_key
        self.calls.append(adapter.id)
        return OperationDispatchResult(
            "completed",
            output={"result": arguments["value"]},
        )

    def resume_task_operation(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        dispatch_key: str,
        proposal: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> OperationDispatchResult:
        del adapter, arguments, dispatch_key, proposal, decision
        raise AssertionError("resume is not expected")


def _component(
    component_id: str,
    kind: str,
    implementation: Any,
    *,
    outcomes: tuple[str, ...] = ("passed",),
) -> PinnedComponent:
    def keyed(inputs: dict[str, Any], *, idempotency_key: str) -> Any:
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
        supported_outcomes=outcomes,  # type: ignore[arg-type]
        configuration={},
        implementation=keyed,
    )


def _fixture(
    tmp_path: Path,
    *,
    goal_outcome: str = "passed",
    seed_first_status: str = "pending",
    side_effect: bool = False,
    task_verifier_raises: bool = False,
) -> tuple[
    OperationTaskGraphRuntime,
    _Bridge,
    dict[str, int],
    Callable[[Any], OperationTaskGraphRuntime],
]:
    calls = {"planner": 0, "context": 0, "task": 0, "criterion": 0, "goal": 0}
    adapters = OperationAdapterRegistry()
    for adapter_id in ("first-op", "second-op"):
        adapters.register(
            OperationAdapter(
                id=adapter_id,
                version="1",
                kind="tool",
                target=adapter_id,
                node_selectable=True,
                required_capabilities=(),
                side_effect=side_effect,
                recovery_mode="provider_idempotent" if side_effect else "pure",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"result": {"type": "string"}},
                    "required": ["result"],
                    "additionalProperties": False,
                },
            )
        )

    def planner(inputs: dict[str, Any]) -> GraphPatch:
        calls["planner"] += 1
        graph_id = inputs["graph"]["graph_id"]
        intent = inputs["intent"]
        fingerprints = {
            item["id"]: item["fingerprint"] for item in inputs["allowed_operation_adapters"]
        }

        def task(
            task_id: str,
            adapter_id: str,
            *,
            depends_on: tuple[DependencyRef, ...] = (),
            status: str = "pending",
        ) -> TaskRun:
            binding = ExecutorBinding(
                "operation",
                adapter_id,
                fingerprints[adapter_id],
            )
            return TaskRun(
                task_id=task_id,
                task_revision=1,
                graph_id=graph_id,
                intent_version=intent["version"],
                intent_binding_hash=compute_fingerprint(intent),
                intent_binding_state="current",
                goal=f"Execute {task_id}",
                supports=("criterion-1",),
                depends_on=depends_on,
                priority=50,
                required=True,
                kind="executable",
                completion_contract=CompletionContract("operation-result", ("task-v1",)),
                executor_policy=ExecutorPolicy((binding,), binding),
                status=status,  # type: ignore[arg-type]
                output_refs=("blob://sha256/fake",) if status == "completed" else (),
            )

        first = task("first", "first-op", status=seed_first_status)
        second = task("second", "second-op", depends_on=(first.ref,))
        return GraphPatch(
            base_revision=0,
            trigger="seed",
            reason="two serial operations",
            operations=(
                GraphPatchOperation("add_task", task=first),
                GraphPatchOperation("add_task", task=second),
            ),
        )

    def context_builder(inputs: dict[str, Any]) -> dict[str, Any]:
        calls["context"] += 1
        return {
            "context_manifest": {"task_id": inputs["task"]["task_id"]},
            "operation_arguments": {"value": inputs["task"]["task_id"]},
        }

    def task_verifier(_inputs: dict[str, Any]) -> dict[str, Any]:
        calls["task"] += 1
        if task_verifier_raises:
            raise RuntimeError("validator crashed")
        return {"outcome": "passed"}

    def criterion_verifier(_inputs: dict[str, Any]) -> dict[str, Any]:
        calls["criterion"] += 1
        return {"outcome": "passed", "evidence_refs": ["evidence://criterion-1"]}

    def goal_verifier(_inputs: dict[str, Any]) -> dict[str, Any]:
        calls["goal"] += 1
        return {"outcome": goal_outcome, "reason": "goal decision"}

    components = PinnedComponentRegistry()
    for component in (
        _component("planner-v1", "planner", planner),
        _component("policy-v1", "graph_policy", lambda value: value),
        _component("context-v1", "context_builder", context_builder),
        _component("task-v1", "task_verifier", task_verifier),
        _component("criterion-v1", "criterion_verifier", criterion_verifier),
        _component(
            "goal-v1",
            "goal_verifier",
            goal_verifier,
            outcomes=("passed", "terminal", "ambiguous"),
        ),
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
        operation_adapters=("first-op", "second-op"),
        parent_inline_components=(),
        human_task_contracts=(),
        child_templates=(),
        limits=TaskGraphLimits(8, 4, 2, 1, 0),
    )
    node = Node(
        id="execute",
        execution="task_graph",
        inputs={"intent": {"$ref": "#/workflow/input/intent"}},
        completion_output_schema={
            "type": "object",
            "properties": {"goal_verified": {"const": True}},
            "required": ["goal_verified"],
        },
        completion_validator="node-result-v1",
        completion_required=("goal_verified",),
        completion_review="none",
        transitions={"completed": "$complete", "failed": "$fail", "waiting": "$wait"},
        task_graph=config,
    )
    workflow = Workflow(
        id="long-task",
        description="Operation-only long task.",
        input_schema={"type": "object"},
        start_node="execute",
        nodes=(node,),
        definition_fingerprint="long-task-fixture",
    )
    validators = CompletionValidatorRegistry()
    validators.register(CompletionValidator("node-result-v1", "1", lambda value: bool(value)))
    contract = build_execution_contract(
        workflow=workflow,
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"first-op", "second-op"},
        limits={"max_transitions": 4, "max_steps": 40},
        protocol_version="workflow-v1",
        task_graph_components=components,
    )
    bridge = _Bridge()
    artifact_root = tmp_path / "artifacts"

    def rebuild(state: Any = None) -> OperationTaskGraphRuntime:
        return OperationTaskGraphRuntime(
            root_run_id="root-1",
            node_id="execute",
            config=config,
            contract=contract,
            components=components,
            adapters=adapters,
            dispatcher=bridge,
            artifacts=TaskArtifactStore(artifact_root),
            state=state,
        )

    return rebuild(), bridge, calls, rebuild


def _intent(*, status: str = "confirmed") -> dict[str, Any]:
    return {
        "intent_id": "intent-1",
        "version": 1,
        "status": status,
        "goal": "Run two operations",
        "desired_outcome": "Both outputs verified",
        "success_criteria": [
            {
                "id": "criterion-1",
                "description": "The operation result is valid",
                "required": True,
                "verification_mode": "verifier",
                "validator_id": "criterion-v1",
            }
        ],
    }


def _run(runtime: OperationTaskGraphRuntime, *, intent: dict[str, Any]) -> Any:
    for revision in range(1, 40):
        step = runtime.advance(inputs={"intent": intent}, root_revision=revision)
        if step.outcome in {"completed", "failed", "waiting"}:
            return step
    raise AssertionError("Task Graph did not terminate")


def test_two_serial_operations_complete_only_after_goal_verification(tmp_path: Path) -> None:
    runtime, bridge, calls, _rebuild = _fixture(tmp_path)

    step = _run(runtime, intent=_intent())

    assert step.outcome == "completed"
    assert step.output is not None and step.output["goal_verified"] is True
    assert bridge.calls == ["first-op", "second-op"]
    assert calls == {"planner": 1, "context": 2, "task": 2, "criterion": 1, "goal": 1}
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert state.graph.status == "completed"
    assert [item.status for item in state.attempts] == ["completed", "completed"]
    assert [item.status for item in state.receipts] == ["accepted", "accepted"]
    assert len({item.dispatch_key for item in state.attempts}) == 2
    assert state.verification_records[-1].kind == "goal"
    assert state.verification_records[-1].status == "passed"
    event_types = [item.event_type for item in state.events]
    assert event_types.index("criterion_verified") > max(
        index for index, item in enumerate(event_types) if item == "task_completed"
    )


def test_completed_tasks_do_not_complete_when_goal_verifier_is_terminal(
    tmp_path: Path,
) -> None:
    runtime, bridge, calls, _rebuild = _fixture(tmp_path, goal_outcome="terminal")

    step = _run(runtime, intent=_intent())

    assert step.outcome == "failed"
    assert bridge.calls == ["first-op", "second-op"]
    assert calls["goal"] == 1
    state = runtime.current_state
    assert state is not None and state.graph is not None
    assert all(item.status == "completed" for item in state.graph.tasks)
    assert state.graph.status == "failed"


def test_unconfirmed_intent_never_calls_planner_or_dispatcher(tmp_path: Path) -> None:
    runtime, bridge, calls, _rebuild = _fixture(tmp_path)

    step = _run(runtime, intent=_intent(status="draft"))

    assert step.outcome == "failed"
    assert "confirmed Intent" in str(step.error)
    assert bridge.calls == []
    assert calls == {"planner": 0, "context": 0, "task": 0, "criterion": 0, "goal": 0}
    assert runtime.current_state is not None
    assert runtime.current_state.graph is None


def test_runtime_restarts_after_every_committed_step_without_duplicate_work(
    tmp_path: Path,
) -> None:
    runtime, bridge, calls, rebuild = _fixture(tmp_path)
    final = None

    for revision in range(1, 40):
        final = runtime.advance(inputs={"intent": _intent()}, root_revision=revision)
        state = runtime.current_state
        assert state is not None
        assert state.revision == revision
        assert state.events[-1].root_revision == revision
        restored = long_task_state_from_snapshot(json.loads(json.dumps(state.snapshot())))
        runtime = rebuild(restored)
        if final.outcome in {"completed", "failed", "waiting"}:
            break

    assert final is not None and final.outcome == "completed"
    assert bridge.calls == ["first-op", "second-op"]
    assert calls == {"planner": 1, "context": 2, "task": 2, "criterion": 1, "goal": 1}
    state = runtime.current_state
    assert state is not None
    assert len(state.attempts) == 2
    assert len(state.receipts) == 2
    assert len({item.attempt_id for item in state.attempts}) == 2
    assert len({item.submission_id for item in state.receipts}) == 2
    assert all(item.status == "completed" for item in state.component_invocations)


def test_ambiguous_goal_approval_fails_closed_instead_of_looping(tmp_path: Path) -> None:
    runtime, bridge, calls, _rebuild = _fixture(tmp_path, goal_outcome="ambiguous")

    waiting = _run(runtime, intent=_intent())

    assert waiting.outcome == "waiting"
    assert waiting.pending is not None and waiting.pending.kind == "goal"
    failed = runtime.resume(
        pending=waiting.pending,
        payload={"kind": "approve"},
        root_revision=runtime.current_state.revision + 1,  # type: ignore[union-attr]
    )
    assert failed.outcome == "failed"
    assert calls["goal"] == 1
    assert bridge.calls == ["first-op", "second-op"]
    assert runtime.current_state is not None
    assert runtime.current_state.graph is not None
    assert runtime.current_state.graph.status == "failed"


def test_ambiguous_goal_retry_is_not_accepted_in_slice_one(tmp_path: Path) -> None:
    runtime, _bridge, calls, _rebuild = _fixture(tmp_path, goal_outcome="ambiguous")
    waiting = _run(runtime, intent=_intent())

    failed = runtime.resume(
        pending=waiting.pending,  # type: ignore[arg-type]
        payload={"kind": "retry"},
        root_revision=runtime.current_state.revision + 1,  # type: ignore[union-attr]
    )

    assert failed.outcome == "failed"
    assert calls["goal"] == 1


def test_seed_cannot_inject_precompleted_task_outputs(tmp_path: Path) -> None:
    runtime, bridge, calls, _rebuild = _fixture(
        tmp_path,
        seed_first_status="completed",
    )

    failed = _run(runtime, intent=_intent())

    assert failed.outcome == "failed"
    assert "clean pending work" in str(failed.error)
    assert bridge.calls == []
    assert calls["task"] == 0


def test_slice_one_rejects_side_effecting_operation_bindings(tmp_path: Path) -> None:
    runtime, bridge, calls, _rebuild = _fixture(tmp_path, side_effect=True)

    failed = _run(runtime, intent=_intent())

    assert failed.outcome == "failed"
    assert "side effects" in str(failed.error)
    assert bridge.calls == []
    assert calls["context"] == 0


def test_component_failure_closes_prepared_invocation(tmp_path: Path) -> None:
    runtime, bridge, calls, _rebuild = _fixture(
        tmp_path,
        task_verifier_raises=True,
    )

    failed = _run(runtime, intent=_intent())

    assert failed.outcome == "failed"
    assert bridge.calls == ["first-op"]
    assert calls["task"] == 1
    assert runtime.current_state is not None
    failed_calls = [
        item for item in runtime.current_state.component_invocations if item.status == "failed"
    ]
    assert len(failed_calls) == 1
    assert "validator crashed" in str(failed_calls[0].error)
