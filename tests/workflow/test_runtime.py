"""Operation-only vertical tests for WorkflowRuntime."""

from __future__ import annotations

import pytest

from modi_harness.brain import DefaultBrain, StaticStructuredPlanner
from modi_harness.loop import planner_step_decision
from modi_harness.workflow import parse_workflow
from modi_harness.workflow.contract import (
    CompletionValidator,
    CompletionValidatorRegistry,
    OperationAdapter,
    OperationAdapterRegistry,
    build_execution_contract,
)
from modi_harness.workflow.runtime import (
    InMemoryWorkflowStore,
    InvocationRecord,
    OperationDispatchResult,
    WorkflowRuntime,
    WorkflowRuntimeError,
)


def _workflow():
    return parse_workflow(
        {
            "id": "answer",
            "input_schema": {
                "type": "object",
                "required": ["question"],
                "properties": {"question": {"type": "string"}},
            },
            "start_node": "lookup",
            "nodes": [
                {
                    "id": "lookup",
                    "execution": "operation",
                    "operation": "lookup",
                    "inputs": {"question": {"$ref": "#/workflow/input/question"}},
                    "completion": {
                        "output_schema": {
                            "type": "object",
                            "required": ["answer"],
                            "properties": {"answer": {"type": "string"}},
                        },
                        "validator": "valid_answer",
                    },
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )


def _dependencies(*, adapter_version: str = "1"):
    adapters = OperationAdapterRegistry()
    adapters.register(
        OperationAdapter(
            id="lookup",
            version=adapter_version,
            kind="tool",
            target="search",
            node_selectable=True,
            required_capabilities=("search",),
            side_effect=False,
            recovery_mode="pure",
            input_schema={"type": "object", "required": ["question"]},
            output_schema={"type": "object", "required": ["answer"]},
        )
    )
    validators = CompletionValidatorRegistry()
    validators.register(
        CompletionValidator(
            id="valid_answer",
            version="1",
            validate=lambda value: bool(value.get("answer")),
        )
    )
    contract = build_execution_contract(
        workflow=_workflow(),
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 4},
        protocol_version="workflow-v1",
    )
    return adapters, validators, contract


def _autonomous_workflow():
    return parse_workflow(
        {
            "id": "investigation",
            "input_schema": {"type": "object"},
            "start_node": "investigate",
            "nodes": [
                {
                    "id": "investigate",
                    "execution": "autonomous",
                    "goal": "Find the root cause",
                    "completion": {
                        "output_schema": {
                            "type": "object",
                            "required": ["root_cause"],
                            "properties": {"root_cause": {"type": "string", "minLength": 1}},
                        },
                        "validator": "valid_investigation",
                    },
                    "limits": {"max_steps": 3},
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )


def _autonomous_dependencies():
    adapters = OperationAdapterRegistry()
    validators = CompletionValidatorRegistry()
    validators.register(
        CompletionValidator(
            id="valid_investigation",
            version="1",
            validate=lambda value: value.get("root_cause") != "unknown",
        )
    )
    workflow = _autonomous_workflow()
    contract = build_execution_contract(
        workflow=workflow,
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling=set(),
        limits={"max_transitions": 4, "max_steps": 3},
        protocol_version="workflow-v1",
    )
    return adapters, validators, workflow, contract


def _complete_decision(result: dict):
    decision = planner_step_decision(step_id="ignored")
    decision["step_kind"] = "verify"
    decision["operation"] = {
        "kind": "workflow_control",
        "summary": "complete the current Node",
        "target": "complete_node",
        "arguments": {"result": result},
        "expected_outcome": "Harness validates completion",
    }
    decision["continuation"] = "wait"
    decision["continuation_basis"] = None
    return decision


class _Dispatcher:
    def __init__(self, result: OperationDispatchResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, adapter: OperationAdapter, arguments: dict) -> OperationDispatchResult:
        self.calls.append((adapter.id, arguments))
        return self.result


def test_operation_node_completes_workflow_once() -> None:
    adapters, validators, contract = _dependencies()
    dispatcher = _Dispatcher(OperationDispatchResult(outcome="completed", output={"answer": "42"}))
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=dispatcher,
        store=InMemoryWorkflowStore(),
    )

    state = runtime.start(
        workflow=_workflow(),
        contract=contract,
        workflow_input={"question": "life?"},
    )
    completed = runtime.advance(state.run_id, workflow=_workflow(), contract=contract)

    assert completed.status == "completed"
    assert completed.output == {"answer": "42"}
    assert dispatcher.calls == [("lookup", {"question": "life?"})]
    assert runtime.store.invocations(state.run_id)[0].status == "terminal"


def test_completion_rejection_follows_failed_without_reexecution() -> None:
    adapters, validators, contract = _dependencies()
    dispatcher = _Dispatcher(OperationDispatchResult(outcome="completed", output={"answer": ""}))
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=dispatcher,
        store=InMemoryWorkflowStore(),
    )
    state = runtime.start(
        workflow=_workflow(),
        contract=contract,
        workflow_input={"question": "life?"},
    )

    failed = runtime.advance(state.run_id, workflow=_workflow(), contract=contract)
    again = runtime.advance(state.run_id, workflow=_workflow(), contract=contract)

    assert failed.status == "failed"
    assert again == failed
    assert len(dispatcher.calls) == 1


def test_start_rejects_invalid_workflow_input() -> None:
    adapters, validators, contract = _dependencies()
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(OperationDispatchResult(outcome="failed", error="unused")),
        store=InMemoryWorkflowStore(),
    )
    with pytest.raises(WorkflowRuntimeError, match="Workflow input"):
        runtime.start(workflow=_workflow(), contract=contract, workflow_input={})


def test_resume_rejects_changed_execution_contract() -> None:
    adapters, validators, contract = _dependencies()
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(
            OperationDispatchResult(outcome="completed", output={"answer": "x"})
        ),
        store=InMemoryWorkflowStore(),
    )
    state = runtime.start(
        workflow=_workflow(),
        contract=contract,
        workflow_input={"question": "q"},
    )
    _, _, changed = _dependencies(adapter_version="2")

    with pytest.raises(WorkflowRuntimeError, match="execution contract changed"):
        runtime.advance(state.run_id, workflow=_workflow(), contract=changed)


def test_prepared_invocation_can_be_cancelled_before_dispatch_claim() -> None:
    store = InMemoryWorkflowStore()
    adapters, validators, contract = _dependencies()
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(
            OperationDispatchResult(outcome="completed", output={"answer": "x"})
        ),
        store=store,
    )
    state = runtime.start(
        workflow=_workflow(),
        contract=contract,
        workflow_input={"question": "q"},
    )
    invocation = InvocationRecord.prepared(
        run_id=state.run_id,
        node_id="lookup",
        node_attempt=1,
        adapter_id="lookup",
        arguments={"question": "q"},
        workflow_revision=state.revision,
    )
    store.prepare_invocation(invocation)

    cancelled = store.cancel(state.run_id, reason="user_cancelled")

    assert cancelled.status == "cancelled"
    assert store.invocations(state.run_id)[0].status == "cancelled"
    with pytest.raises(WorkflowRuntimeError, match="cannot claim"):
        store.claim_dispatch(invocation.id, expected_workflow_revision=state.revision)


def test_dispatching_invocation_prevents_terminal_cancellation() -> None:
    store = InMemoryWorkflowStore()
    adapters, validators, contract = _dependencies()
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(
            OperationDispatchResult(outcome="completed", output={"answer": "x"})
        ),
        store=store,
    )
    state = runtime.start(
        workflow=_workflow(),
        contract=contract,
        workflow_input={"question": "q"},
    )
    invocation = InvocationRecord.prepared(
        run_id=state.run_id,
        node_id="lookup",
        node_attempt=1,
        adapter_id="lookup",
        arguments={"question": "q"},
        workflow_revision=state.revision,
    )
    store.prepare_invocation(invocation)
    store.claim_dispatch(invocation.id, expected_workflow_revision=state.revision)

    cancelling = store.cancel(state.run_id, reason="user_cancelled")

    assert cancelling.status == "running"
    assert cancelling.cancellation_requested is True
    assert store.invocations(state.run_id)[0].status == "dispatching"


def test_uncertain_side_effect_requires_reconciliation() -> None:
    adapters = OperationAdapterRegistry()
    adapters.register(
        OperationAdapter(
            id="lookup",
            version="1",
            kind="tool",
            target="search",
            node_selectable=True,
            required_capabilities=("search",),
            side_effect=True,
            recovery_mode="manual_reconciliation",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )
    validators = CompletionValidatorRegistry()
    validators.register(
        CompletionValidator(id="valid_answer", version="1", validate=lambda _v: True)
    )
    workflow = _workflow()
    contract = build_execution_contract(
        workflow=workflow,
        adapters=adapters,
        validators=validators,
        output_contract={"free_form": True},
        capability_ceiling={"search"},
        limits={"max_transitions": 4},
        protocol_version="workflow-v1",
    )
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(OperationDispatchResult(outcome="uncertain", error="timeout")),
        store=InMemoryWorkflowStore(),
    )
    state = runtime.start(
        workflow=workflow,
        contract=contract,
        workflow_input={"question": "q"},
    )

    result = runtime.advance(state.run_id, workflow=workflow, contract=contract)

    assert result.status == "reconciliation_required"
    assert runtime.store.invocations(state.run_id)[0].status == "reconciliation_required"


def test_autonomous_complete_node_is_validated_and_committed() -> None:
    adapters, validators, workflow, contract = _autonomous_dependencies()
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(OperationDispatchResult(outcome="failed", error="unused")),
        store=InMemoryWorkflowStore(),
        brain=DefaultBrain(
            StaticStructuredPlanner(_complete_decision({"root_cause": "supplier defect"}))
        ),
        agent_profile={"name": "investigator"},
    )
    state = runtime.start(workflow=workflow, contract=contract, workflow_input={})

    completed = runtime.advance(state.run_id, workflow=workflow, contract=contract)

    assert completed.status == "completed"
    assert completed.output == {"root_cause": "supplier defect"}
    assert completed.step_records[0]["node_id"] == "investigate"


def test_autonomous_completion_rejection_returns_feedback_to_same_node() -> None:
    adapters, validators, workflow, contract = _autonomous_dependencies()
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(OperationDispatchResult(outcome="failed", error="unused")),
        store=InMemoryWorkflowStore(),
        brain=DefaultBrain(StaticStructuredPlanner(_complete_decision({"root_cause": "unknown"}))),
        agent_profile={"name": "investigator"},
    )
    state = runtime.start(workflow=workflow, contract=contract, workflow_input={})

    retrying = runtime.advance(state.run_id, workflow=workflow, contract=contract)

    assert retrying.status == "running"
    assert retrying.current_node_id == "investigate"
    assert retrying.step_records[0]["state_delta"]["completion_feedback"]
    assert retrying.transitions == ()


class _BrokenPlanner:
    def plan_structured_step(self, _context):
        raise RuntimeError("model unavailable")


def test_autonomous_planning_failure_emits_node_failed_transition() -> None:
    adapters, validators, workflow, contract = _autonomous_dependencies()
    runtime = WorkflowRuntime(
        adapters=adapters,
        validators=validators,
        dispatcher=_Dispatcher(OperationDispatchResult(outcome="failed", error="unused")),
        store=InMemoryWorkflowStore(),
        brain=DefaultBrain(_BrokenPlanner()),
        agent_profile={"name": "investigator"},
    )
    state = runtime.start(workflow=workflow, contract=contract, workflow_input={})

    failed = runtime.advance(state.run_id, workflow=workflow, contract=contract)

    assert failed.status == "failed"
    assert failed.transitions[0].event == "failed"
    assert "brain_planning_failed" in str(failed.failure)
