"""Durable Workflow execution for deterministic Operation Nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import RLock
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from .._utils import compute_fingerprint, new_ulid
from ..brain import Brain, BrainPlanningError
from ..loop import AgentLoop, initialize_loop_state
from ..loop.types import AutonomousNodeContext, LoopState, StepRecord
from .contract import (
    CompletionValidatorRegistry,
    ExecutionContract,
    OperationAdapter,
    OperationAdapterRegistry,
)
from .definition import WorkflowInstanceError, validate_instance
from .types import WORKFLOW_COMPLETE, WORKFLOW_FAIL, Node, Workflow

if TYPE_CHECKING:
    from ..types import TaskItem, TaskPlan

WorkflowStatus = Literal[
    "running",
    "waiting",
    "completed",
    "failed",
    "cancelled",
    "reconciliation_required",
]
InvocationStatus = Literal[
    "prepared",
    "dispatching",
    "waiting",
    "terminal",
    "cancelled",
    "reconciliation_required",
]
DispatchOutcome = Literal["completed", "failed", "waiting", "uncertain"]


class WorkflowRuntimeError(RuntimeError):
    """Workflow state, authority, or execution integrity is invalid."""


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    source_node_id: str
    source_attempt: int
    event: str
    target: str


@dataclass(frozen=True, slots=True)
class IntentConfirmationProof:
    proof_id: str
    source: Literal["user_input", "node_review"]
    run_id: str
    workflow_id: str
    execution_contract_fingerprint: str
    input_ref: str
    reviewed_result_hash: str
    approved_revision: int
    source_node_id: str | None = None
    source_node_attempt: int | None = None
    request_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "source": self.source,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "execution_contract_fingerprint": self.execution_contract_fingerprint,
            "input_ref": self.input_ref,
            "reviewed_result_hash": self.reviewed_result_hash,
            "approved_revision": self.approved_revision,
            "source_node_id": self.source_node_id,
            "source_node_attempt": self.source_node_attempt,
            "request_id": self.request_id,
        }


@dataclass(frozen=True, slots=True)
class WorkflowState:
    run_id: str
    workflow_id: str
    definition_fingerprint: str
    execution_contract_fingerprint: str
    workflow_input: Mapping[str, Any]
    status: WorkflowStatus
    current_node_id: str
    node_attempt: int
    revision: int
    transition_count: int
    node_outputs: Mapping[str, Any]
    transitions: tuple[TransitionRecord, ...]
    output: Any | None = None
    failure: str | None = None
    cancellation_requested: bool = False
    loop_state: LoopState | None = None
    step_records: tuple[StepRecord, ...] = ()
    task_plan: Mapping[str, Any] | None = None
    pending_operation: PendingOperation | None = None
    human_inputs: Mapping[str, Any] = MappingProxyType({})
    intent_confirmation_proofs: tuple[IntentConfirmationProof, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingOperation:
    """Exact durable work item awaiting one external decision or value."""

    id: str
    kind: Literal["judgment", "interaction"]
    source: Literal[
        "operation_node",
        "autonomous_operation",
        "autonomous_ask",
        "node_review",
        "task_graph_operation",
        "task_graph_task",
        "task_graph_goal",
    ]
    node_id: str
    node_attempt: int
    request_id: str
    step_id: str | None
    invocation_id: str | None
    adapter_id: str | None
    arguments: Mapping[str, Any]
    proposal: Mapping[str, Any]
    decision: Mapping[str, Any]
    dispatch_key: str | None = None


@dataclass(frozen=True, slots=True)
class InvocationRecord:
    id: str
    run_id: str
    node_id: str
    node_attempt: int
    adapter_id: str
    arguments: Mapping[str, Any]
    workflow_revision: int
    status: InvocationStatus
    output: Any | None = None
    error: str | None = None

    @classmethod
    def prepared(
        cls,
        *,
        run_id: str,
        node_id: str,
        node_attempt: int,
        adapter_id: str,
        arguments: Mapping[str, Any],
        workflow_revision: int,
    ) -> InvocationRecord:
        return cls(
            id=new_ulid(),
            run_id=run_id,
            node_id=node_id,
            node_attempt=node_attempt,
            adapter_id=adapter_id,
            arguments=_freeze_mapping(arguments),
            workflow_revision=workflow_revision,
            status="prepared",
        )


@dataclass(frozen=True, slots=True)
class OperationDispatchResult:
    outcome: DispatchOutcome
    output: Any | None = None
    error: str | None = None


class OperationDispatcher(Protocol):
    """Trusted bridge from an adapter to the existing execution gateway."""

    def dispatch(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
    ) -> OperationDispatchResult:
        """Dispatch at most once and return a normalized outcome."""

    def resume_approved(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        proposal: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> OperationDispatchResult:
        """Execute the exact previously reviewed Operation without replanning."""

    def record_rejection(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        """Persist a denial fingerprint before the Agent may plan again."""


class InMemoryWorkflowStore:
    """Atomic reference store used by tests and in-process execution."""

    def __init__(self) -> None:
        self._states: dict[str, WorkflowState] = {}
        self._invocations: dict[str, InvocationRecord] = {}
        self._lock = RLock()

    def create(self, state: WorkflowState) -> None:
        with self._lock:
            if state.run_id in self._states:
                raise WorkflowRuntimeError(f"duplicate Workflow run {state.run_id!r}")
            self._states[state.run_id] = state

    def get(self, run_id: str) -> WorkflowState:
        with self._lock:
            try:
                return self._states[run_id]
            except KeyError as exc:
                raise WorkflowRuntimeError(f"unknown Workflow run {run_id!r}") from exc

    def discard(self, run_id: str) -> None:
        """Drop one stale process-local branch after authoritative CAS conflict."""

        with self._lock:
            self._states.pop(run_id, None)
            self._invocations = {
                key: item
                for key, item in self._invocations.items()
                if item.run_id != run_id
            }

    def commit(self, state: WorkflowState, *, expected_revision: int) -> WorkflowState:
        with self._lock:
            current = self.get(state.run_id)
            if current.revision != expected_revision:
                raise WorkflowRuntimeError(
                    f"stale Workflow revision {expected_revision}; current is {current.revision}"
                )
            if state.revision != expected_revision + 1:
                raise WorkflowRuntimeError("Workflow commit must increment revision exactly once")
            self._states[state.run_id] = state
            return state

    def prepare_invocation(self, invocation: InvocationRecord) -> None:
        with self._lock:
            if invocation.id in self._invocations:
                raise WorkflowRuntimeError(f"duplicate invocation {invocation.id!r}")
            state = self.get(invocation.run_id)
            if state.status != "running" or state.cancellation_requested:
                raise WorkflowRuntimeError("cannot prepare invocation for a non-running Workflow")
            if state.revision != invocation.workflow_revision:
                raise WorkflowRuntimeError("cannot prepare invocation against a stale revision")
            for existing in self._invocations.values():
                if (
                    existing.run_id == invocation.run_id
                    and existing.node_id == invocation.node_id
                    and existing.node_attempt == invocation.node_attempt
                    and existing.status in {"prepared", "dispatching"}
                ):
                    raise WorkflowRuntimeError("Node attempt already has an active invocation")
            self._invocations[invocation.id] = invocation

    def claim_dispatch(
        self,
        invocation_id: str,
        *,
        expected_workflow_revision: int,
    ) -> InvocationRecord:
        with self._lock:
            invocation = self._invocation(invocation_id)
            state = self.get(invocation.run_id)
            if (
                invocation.status != "prepared"
                or state.status != "running"
                or state.cancellation_requested
                or state.revision != expected_workflow_revision
                or invocation.workflow_revision != expected_workflow_revision
            ):
                raise WorkflowRuntimeError(
                    f"cannot claim invocation {invocation_id!r} for dispatch"
                )
            claimed = replace(invocation, status="dispatching")
            self._invocations[invocation_id] = claimed
            return claimed

    def finish_invocation(
        self,
        invocation_id: str,
        *,
        status: Literal["waiting", "terminal", "reconciliation_required"],
        output: Any | None = None,
        error: str | None = None,
    ) -> InvocationRecord:
        with self._lock:
            invocation = self._invocation(invocation_id)
            if invocation.status != "dispatching":
                raise WorkflowRuntimeError(
                    f"cannot finish invocation {invocation_id!r} from {invocation.status!r}"
                )
            finished = replace(invocation, status=status, output=output, error=error)
            self._invocations[invocation_id] = finished
            return finished

    def claim_resume(self, invocation_id: str, *, run_id: str) -> InvocationRecord:
        """Durably claim one waiting invocation before executing approved work."""

        with self._lock:
            invocation = self._invocation(invocation_id)
            state = self.get(run_id)
            if (
                invocation.run_id != run_id
                or invocation.status != "waiting"
                or state.status != "waiting"
                or state.pending_operation is None
                or state.pending_operation.invocation_id != invocation_id
            ):
                raise WorkflowRuntimeError(
                    f"cannot resume invocation {invocation_id!r} from current state"
                )
            claimed = replace(invocation, status="dispatching")
            self._invocations[invocation_id] = claimed
            return claimed

    def reject_waiting_invocation(self, invocation_id: str, *, reason: str) -> InvocationRecord:
        """Close a waiting invocation without executing the reviewed action."""

        with self._lock:
            invocation = self._invocation(invocation_id)
            if invocation.status != "waiting":
                raise WorkflowRuntimeError(
                    f"cannot reject invocation {invocation_id!r} from {invocation.status!r}"
                )
            rejected = replace(invocation, status="terminal", error=reason)
            self._invocations[invocation_id] = rejected
            return rejected

    def invocations(self, run_id: str) -> tuple[InvocationRecord, ...]:
        with self._lock:
            return tuple(record for record in self._invocations.values() if record.run_id == run_id)

    def restore_invocation(self, invocation: InvocationRecord) -> None:
        """Restore one checkpointed invocation before a run resumes."""

        with self._lock:
            if invocation.id in self._invocations:
                raise WorkflowRuntimeError(f"duplicate invocation {invocation.id!r}")
            if invocation.run_id not in self._states:
                raise WorkflowRuntimeError("cannot restore invocation before Workflow state")
            self._invocations[invocation.id] = invocation

    def cancel(self, run_id: str, *, reason: str) -> WorkflowState:
        with self._lock:
            state = self.get(run_id)
            if state.status in {"completed", "failed", "cancelled"}:
                return state
            active = [
                item
                for item in self._invocations.values()
                if item.run_id == run_id
                and item.status in {"prepared", "dispatching", "waiting"}
            ]
            if any(item.status == "dispatching" for item in active):
                updated = replace(
                    state,
                    cancellation_requested=True,
                    failure=reason,
                    revision=state.revision + 1,
                )
                self._states[run_id] = updated
                return updated
            for item in active:
                self._invocations[item.id] = replace(
                    item,
                    status="cancelled",
                    error=reason,
                )
            updated = replace(
                state,
                status="cancelled",
                cancellation_requested=True,
                failure=reason,
                pending_operation=None,
                revision=state.revision + 1,
            )
            self._states[run_id] = updated
            return updated

    def _invocation(self, invocation_id: str) -> InvocationRecord:
        try:
            return self._invocations[invocation_id]
        except KeyError as exc:
            raise WorkflowRuntimeError(f"unknown invocation {invocation_id!r}") from exc


class WorkflowRuntime:
    """Owner of Workflow Node attempts and declared transitions."""

    def __init__(
        self,
        *,
        adapters: OperationAdapterRegistry,
        validators: CompletionValidatorRegistry,
        dispatcher: OperationDispatcher | None,
        store: InMemoryWorkflowStore,
        brain: Brain | None = None,
        agent_profile: Mapping[str, Any] | None = None,
        task_graph_executor: Any | None = None,
    ) -> None:
        self._adapters = adapters
        self._validators = validators
        self._dispatcher = dispatcher
        self._brain = brain
        self._agent_profile = dict(agent_profile or {})
        self._task_graph_executor = task_graph_executor
        self.store = store

    def bind_dispatcher(self, dispatcher: OperationDispatcher) -> None:
        """Bind the run-scoped gateway bridge before the first Node executes."""

        self._dispatcher = dispatcher

    def bind_task_graph_executor(self, executor: Any) -> None:
        """Bind the root-scoped Task Graph executor after dispatcher construction."""

        self._task_graph_executor = executor

    def resume_waiting(
        self,
        run_id: str,
        *,
        payload: Mapping[str, Any],
        workflow: Workflow,
        contract: ExecutionContract,
        root_revision: int | None = None,
    ) -> WorkflowState:
        """Resolve and resume the exact durable work item that caused the wait."""

        state = self.store.get(run_id)
        if state.status != "waiting":
            return state
        cancel_requested = payload.get("kind") in {"cancel", "cancelled"} or payload.get(
            "decision"
        ) in {
            "cancel",
            "cancelled",
        }
        pending = state.pending_operation
        if pending is None:
            raise WorkflowRuntimeError("waiting Workflow has no pending Operation")
        self._verify_resume(state, workflow, contract)
        supplied_id = payload.get(
            "judgment_id" if pending.kind == "judgment" else "interaction_id"
        )
        if supplied_id != pending.request_id:
            raise WorkflowRuntimeError("resume payload does not match the pending Operation")

        if pending.source in {
            "task_graph_operation",
            "task_graph_task",
            "task_graph_goal",
        }:
            if cancel_requested and pending.source != "task_graph_task":
                return self._cancel_task_graph(
                    state,
                    reason="cancelled by user",
                    root_revision=root_revision,
                )
            return self._resume_task_graph(
                state,
                pending=pending,
                payload=payload,
                workflow=workflow,
                contract=contract,
                root_revision=root_revision,
            )
        if cancel_requested:
            return self.store.cancel(run_id, reason="cancelled by user")

        if pending.source == "node_review":
            return self._resume_node_review(
                state,
                pending=pending,
                payload=payload,
                workflow=workflow,
                contract=contract,
            )
        if pending.kind == "interaction" or pending.source == "autonomous_ask":
            return self._resume_interaction(state, pending=pending, payload=payload)

        kind = str(payload.get("kind") or "")
        if kind != "approve":
            return self._reject_pending_operation(
                state,
                pending=pending,
                workflow=workflow,
                contract=contract,
                reason=str(payload.get("rationale") or f"human judgment: {kind or 'rejected'}"),
            )
        return self._execute_approved_pending(
            state,
            pending=pending,
            workflow=workflow,
            contract=contract,
        )

    def _resume_node_review(
        self,
        state: WorkflowState,
        *,
        pending: PendingOperation,
        payload: Mapping[str, Any],
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> WorkflowState:
        decision = str(payload.get("decision") or payload.get("kind") or "")
        if decision in {"approve", "approved", "submitted"}:
            node = workflow.node(pending.node_id)
            result = pending.arguments.get("result")
            task_plan = _task_plan_from_result(result)
            proof = IntentConfirmationProof(
                proof_id=new_ulid(),
                source="node_review",
                run_id=state.run_id,
                workflow_id=state.workflow_id,
                execution_contract_fingerprint=state.execution_contract_fingerprint,
                input_ref=f"#/nodes/{pending.node_id}/output",
                reviewed_result_hash=compute_fingerprint(_thaw(result)),
                approved_revision=state.revision + 1,
                source_node_id=pending.node_id,
                source_node_attempt=pending.node_attempt,
                request_id=pending.request_id,
            )
            current = replace(
                state,
                status="running",
                pending_operation=None,
                task_plan=(
                    _freeze_mapping(task_plan) if task_plan is not None else state.task_plan
                ),
                intent_confirmation_proofs=(
                    *state.intent_confirmation_proofs,
                    proof,
                ),
            )
            return self._commit_transition(
                current,
                node=node,
                event="completed",
                output=result,
                error=None,
                workflow=workflow,
                contract=contract,
            )
        if decision == "revise":
            feedback = payload.get("feedback", payload.get("value"))
            human_inputs = dict(state.human_inputs)
            human_inputs[f"{pending.node_id}_review_feedback"] = feedback
            loop_state = LoopState(**state.loop_state) if state.loop_state is not None else None
            if loop_state is not None:
                loop_state["status"] = "active"
                loop_state["continuation"] = "continue"
                loop_state["pending_step_id"] = None
            return self.store.commit(
                replace(
                    state,
                    status="running",
                    pending_operation=None,
                    human_inputs=MappingProxyType(human_inputs),
                    loop_state=loop_state,
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
            )
        return self.store.cancel(state.run_id, reason="node review rejected by user")

    def _resume_interaction(
        self,
        state: WorkflowState,
        *,
        pending: PendingOperation,
        payload: Mapping[str, Any],
    ) -> WorkflowState:
        decision = str(payload.get("decision") or payload.get("kind") or "")
        accepted = decision not in {"reject", "revise", "redirect", "constrain", "clarify"}
        field = str(pending.proposal.get("field") or pending.request_id)
        value = payload.get("value", payload.get("feedback", payload.get("rationale")))
        human_inputs = dict(state.human_inputs)
        human_inputs[field] = value
        records, loop_state = _resolve_pending_step(
            state,
            pending,
            status="completed" if accepted else "failed",
            state_delta={
                "human_input": value,
                "human_decision": decision,
                "interaction_id": pending.request_id,
            },
        )
        return self.store.commit(
            replace(
                state,
                status="running",
                pending_operation=None,
                human_inputs=MappingProxyType(human_inputs),
                loop_state=loop_state,
                step_records=records,
                revision=state.revision + 1,
            ),
            expected_revision=state.revision,
        )

    def _execute_approved_pending(
        self,
        state: WorkflowState,
        *,
        pending: PendingOperation,
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> WorkflowState:
        if (
            self._dispatcher is None
            or pending.adapter_id is None
            or pending.invocation_id is None
        ):
            raise WorkflowRuntimeError("pending judgment does not bind an executable Operation")
        adapter = self._adapters.resolve(pending.adapter_id)
        self.store.claim_resume(pending.invocation_id, run_id=state.run_id)
        try:
            result = self._dispatcher.resume_approved(
                adapter,
                dict(pending.arguments),
                proposal=pending.proposal,
                decision=pending.decision,
            )
        except Exception as exc:
            result = OperationDispatchResult(
                outcome="uncertain" if adapter.side_effect else "failed",
                error=str(exc),
            )
        if result.outcome in {"waiting", "uncertain"}:
            self.store.finish_invocation(
                pending.invocation_id,
                status="reconciliation_required",
                error=result.error or "approved Operation outcome is uncertain",
            )
            return self.store.commit(
                replace(
                    state,
                    status="reconciliation_required",
                    pending_operation=None,
                    failure=result.error or "approved Operation outcome is uncertain",
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
            )
        self.store.finish_invocation(
            pending.invocation_id,
            status="terminal",
            output=result.output,
            error=result.error,
        )
        node = workflow.node(pending.node_id)
        if pending.source == "operation_node":
            current = replace(state, status="running", pending_operation=None)
            event, error = self._validate_operation_completion(
                node=node,
                adapter=adapter,
                result=result,
            )
            return self._commit_transition(
                current,
                node=node,
                event=event,
                output=result.output,
                error=error,
                workflow=workflow,
                contract=contract,
            )

        records, loop_state = _resolve_pending_step(
            state,
            pending,
            status="completed" if result.outcome == "completed" else "failed",
            state_delta={
                "operation_output": result.output,
                "operation_error": result.error,
                "approved_judgment_id": pending.request_id,
            },
        )
        task_plan = (
            _finish_operation_task(
                _plain_task_plan(state.task_plan),
                pending.arguments,
                result,
            )
            if state.task_plan is not None
            else None
        )
        return self.store.commit(
            replace(
                state,
                status="running",
                pending_operation=None,
                loop_state=loop_state,
                step_records=records,
                task_plan=(
                    _freeze_mapping(task_plan) if task_plan is not None else state.task_plan
                ),
                revision=state.revision + 1,
            ),
            expected_revision=state.revision,
        )

    def _reject_pending_operation(
        self,
        state: WorkflowState,
        *,
        pending: PendingOperation,
        workflow: Workflow,
        contract: ExecutionContract,
        reason: str,
    ) -> WorkflowState:
        if pending.adapter_id is None or pending.invocation_id is None:
            raise WorkflowRuntimeError("pending judgment does not bind an Operation")
        adapter = self._adapters.resolve(pending.adapter_id)
        if self._dispatcher is not None:
            self._dispatcher.record_rejection(adapter, dict(pending.arguments), reason=reason)
        self.store.reject_waiting_invocation(pending.invocation_id, reason=reason)
        node = workflow.node(pending.node_id)
        if pending.source == "operation_node":
            return self._commit_transition(
                replace(state, status="running", pending_operation=None),
                node=node,
                event="failed",
                output=None,
                error=reason,
                workflow=workflow,
                contract=contract,
            )
        records, loop_state = _resolve_pending_step(
            state,
            pending,
            status="failed",
            state_delta={
                "operation_error": reason,
                "rejected_judgment_id": pending.request_id,
            },
        )
        task_plan = (
            _finish_operation_task(
                _plain_task_plan(state.task_plan),
                pending.arguments,
                OperationDispatchResult(outcome="failed", error=reason),
            )
            if state.task_plan is not None
            else None
        )
        return self.store.commit(
            replace(
                state,
                status="running",
                pending_operation=None,
                loop_state=loop_state,
                step_records=records,
                task_plan=(
                    _freeze_mapping(task_plan) if task_plan is not None else state.task_plan
                ),
                revision=state.revision + 1,
            ),
            expected_revision=state.revision,
        )

    def cancel(self, run_id: str, *, reason: str) -> WorkflowState:
        """Cancel a Workflow run through the store's race-safe path."""

        return self.store.cancel(run_id, reason=reason)

    def start(
        self,
        *,
        workflow: Workflow,
        contract: ExecutionContract,
        workflow_input: Mapping[str, Any],
        run_id: str | None = None,
    ) -> WorkflowState:
        self._verify_contract_definition(workflow, contract)
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            raise WorkflowRuntimeError("explicit Workflow run_id must be non-empty")
        try:
            validate_instance(workflow.input_schema, dict(workflow_input), context="Workflow input")
        except WorkflowInstanceError as exc:
            raise WorkflowRuntimeError(str(exc)) from exc
        selected_run_id = run_id.strip() if run_id is not None else new_ulid()
        direct_proofs: tuple[IntentConfirmationProof, ...] = ()
        if "intent" in workflow_input:
            direct_proofs = (
                IntentConfirmationProof(
                    proof_id=new_ulid(),
                    source="user_input",
                    run_id=selected_run_id,
                    workflow_id=workflow.id,
                    execution_contract_fingerprint=contract.fingerprint,
                    input_ref="#/workflow/input/intent",
                    reviewed_result_hash=compute_fingerprint(workflow_input),
                    approved_revision=0,
                ),
            )
        state = WorkflowState(
            run_id=selected_run_id,
            workflow_id=workflow.id,
            definition_fingerprint=workflow.definition_fingerprint,
            execution_contract_fingerprint=contract.fingerprint,
            workflow_input=_freeze_mapping(workflow_input),
            status="running",
            current_node_id=workflow.start_node,
            node_attempt=1,
            revision=0,
            transition_count=0,
            node_outputs=MappingProxyType({}),
            transitions=(),
            intent_confirmation_proofs=direct_proofs,
            task_plan=(
                _freeze_mapping(cast(Mapping[str, Any], self._agent_profile["task_plan"]))
                if isinstance(self._agent_profile.get("task_plan"), Mapping)
                else None
            ),
        )
        self.store.create(state)
        return state

    def advance(
        self,
        run_id: str,
        *,
        workflow: Workflow,
        contract: ExecutionContract,
        root_revision: int | None = None,
    ) -> WorkflowState:
        state = self.store.get(run_id)
        self._verify_resume(state, workflow, contract)
        if state.status != "running":
            return state
        node = workflow.node(state.current_node_id)
        if node.execution == "task_graph":
            return self._execute_task_graph(
                state,
                node,
                workflow,
                contract,
                root_revision=root_revision,
            )
        if node.execution == "autonomous":
            return self._execute_autonomous(state, node, workflow, contract)
        return self._execute_operation(state, node, workflow, contract)

    def _execute_task_graph(
        self,
        state: WorkflowState,
        node: Node,
        workflow: Workflow,
        contract: ExecutionContract,
        *,
        root_revision: int | None,
    ) -> WorkflowState:
        executor = self._task_graph_executor
        if executor is None:
            return self._fail_integrity(state, "task_graph Node has no root executor")
        revision = self._task_graph_revision(state, root_revision)
        try:
            inputs = _resolve_node_inputs(node, state)
            inputs.pop("intent_confirmation_proof", None)
            confirmation = _intent_confirmation_for_node(
                node,
                state,
                workflow,
                inputs.get("intent"),
            )
            if confirmation is not None:
                inputs["intent_confirmation_proof"] = confirmation
            step = executor.advance(
                inputs=inputs,
                root_revision=revision,
                parent_node_attempt=state.node_attempt,
            )
        except Exception as exc:
            return self._fail_integrity(state, f"task_graph execution failed: {exc}")
        return self._commit_task_graph_step(
            state,
            node=node,
            workflow=workflow,
            contract=contract,
            step=step,
        )

    def _resume_task_graph(
        self,
        state: WorkflowState,
        *,
        pending: PendingOperation,
        payload: Mapping[str, Any],
        workflow: Workflow,
        contract: ExecutionContract,
        root_revision: int | None,
    ) -> WorkflowState:
        executor = self._task_graph_executor
        if executor is None:
            raise WorkflowRuntimeError("waiting task_graph Node has no root executor")
        from ..long_task.runtime import TaskGraphPending

        step = executor.resume(
            pending=TaskGraphPending(
                kind=(
                    "goal"
                    if pending.source == "task_graph_goal"
                    else (
                        "task"
                        if pending.source == "task_graph_task"
                        else "operation"
                    )
                ),
                request_id=pending.request_id,
                attempt_id=pending.invocation_id,
                adapter_id=pending.adapter_id,
                dispatch_key=pending.dispatch_key,
                arguments=pending.arguments,
                proposal=pending.proposal,
                decision=pending.decision,
            ),
            payload=payload,
            root_revision=self._task_graph_revision(state, root_revision),
        )
        return self._commit_task_graph_step(
            state,
            node=workflow.node(pending.node_id),
            workflow=workflow,
            contract=contract,
            step=step,
        )

    def _cancel_task_graph(
        self,
        state: WorkflowState,
        *,
        reason: str,
        root_revision: int | None,
    ) -> WorkflowState:
        executor = self._task_graph_executor
        if executor is None:
            raise WorkflowRuntimeError("waiting task_graph Node has no root executor")
        step = executor.cancel(
            root_revision=self._task_graph_revision(state, root_revision),
            reason=reason,
        )
        task_plan = (
            _freeze_mapping(step.task_plan) if isinstance(step.task_plan, Mapping) else None
        )
        return self.store.commit(
            replace(
                state,
                status="cancelled",
                cancellation_requested=True,
                failure=reason,
                pending_operation=None,
                task_plan=task_plan,
                revision=state.revision + 1,
            ),
            expected_revision=state.revision,
        )

    def _commit_task_graph_step(
        self,
        state: WorkflowState,
        *,
        node: Node,
        workflow: Workflow,
        contract: ExecutionContract,
        step: Any,
    ) -> WorkflowState:
        outcome = str(step.outcome)
        task_plan = (
            _freeze_mapping(step.task_plan) if isinstance(step.task_plan, Mapping) else None
        )
        if outcome == "running":
            return self.store.commit(
                replace(
                    state,
                    status="running",
                    pending_operation=None,
                    task_plan=task_plan,
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
            )
        if outcome == "waiting":
            if step.pending is None:
                return self._fail_integrity(state, "task_graph wait has no pending record")
            pending = PendingOperation(
                id=new_ulid(),
                kind=(
                    "interaction"
                    if step.pending.kind == "task"
                    and (step.pending.decision or {}).get("decision_class")
                    == "interaction"
                    else "judgment"
                ),
                source=(
                    "task_graph_goal"
                    if step.pending.kind == "goal"
                    else (
                        "task_graph_task"
                        if step.pending.kind == "task"
                        else "task_graph_operation"
                    )
                ),
                node_id=node.id,
                node_attempt=state.node_attempt,
                request_id=step.pending.request_id,
                step_id=None,
                invocation_id=step.pending.attempt_id,
                adapter_id=step.pending.adapter_id,
                arguments=_freeze_mapping(step.pending.arguments or {}),
                proposal=_freeze_mapping(
                    step.pending.proposal
                    or {
                        "prompt": step.pending.reason or "Review the pending Task Graph decision",
                        "summary": (
                            "Goal verification"
                            if step.pending.kind == "goal"
                            else "Task Operation"
                        ),
                    }
                ),
                decision=_freeze_mapping(
                    step.pending.decision
                    or {"reason": step.pending.reason or "human judgment required"}
                ),
                dispatch_key=step.pending.dispatch_key,
            )
            return self.store.commit(
                replace(
                    state,
                    status="waiting",
                    pending_operation=pending,
                    task_plan=task_plan,
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
            )
        if outcome == "completed":
            try:
                self._validate_task_graph_completion(node, step.output)
            except WorkflowRuntimeError as exc:
                return self._commit_transition(
                    state,
                    node=node,
                    event="failed",
                    output=None,
                    error=str(exc),
                    workflow=workflow,
                    contract=contract,
                )
            return self._commit_transition(
                replace(state, task_plan=task_plan),
                node=node,
                event="completed",
                output=step.output,
                error=None,
                workflow=workflow,
                contract=contract,
            )
        return self._commit_transition(
            replace(state, task_plan=task_plan),
            node=node,
            event="failed",
            output=None,
            error=step.error or "Task Graph failed",
            workflow=workflow,
            contract=contract,
        )

    def _validate_task_graph_completion(self, node: Node, output: Any) -> None:
        if node.completion_output_schema is None:
            raise WorkflowRuntimeError("task_graph Node completion schema is missing")
        validate_instance(
            node.completion_output_schema,
            output,
            context=f"Node {node.id!r} Task Graph output",
        )
        for field in node.completion_required:
            if not _is_meaningful(_required_value(output, field)):
                raise WorkflowRuntimeError(
                    f"Node {node.id!r} completion requires meaningful field {field!r}"
                )
        if node.completion_validator is not None:
            validator = self._validators.resolve(node.completion_validator)
            reason = validator.rejection_reason(output)
            if reason is not None:
                raise WorkflowRuntimeError(
                    f"completion validator {validator.id!r} rejected Node output: {reason}"
                )

    def _task_graph_revision(
        self,
        state: WorkflowState,
        supplied: int | None,
    ) -> int:
        if supplied is not None:
            return supplied
        current = getattr(self._task_graph_executor, "current_state", None)
        current_revision = int(getattr(current, "revision", state.revision))
        return max(state.revision, current_revision) + 1

    def _execute_autonomous(
        self,
        state: WorkflowState,
        node: Node,
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> WorkflowState:
        if self._brain is None:
            return self._fail_integrity(state, "autonomous Node has no Brain")
        if node.goal is None or node.completion_output_schema is None:
            return self._fail_integrity(state, "autonomous Node contract is incomplete")

        resolved_inputs = _resolve_node_inputs(node, state)
        input_event = (
            {"type": "human_inputs", "values": dict(state.human_inputs)}
            if state.human_inputs
            else None
        )
        loop_state = state.loop_state or initialize_loop_state(
            workflow_run_id=state.run_id,
            workflow_id=state.workflow_id,
            node_id=node.id,
            node_attempt=state.node_attempt,
            agent_name=str(self._agent_profile.get("name") or "agent"),
            intent_version=int(self._agent_profile.get("intent_version") or 1),
            max_auto_steps=_autonomous_step_budget(state, node, contract),
        )
        loop = AgentLoop(state=loop_state, brain=self._brain)
        completion_only = _task_plan_is_closed(state.task_plan)
        try:
            prepared = loop.prepare_step(
                step_id=new_ulid(),
                node=AutonomousNodeContext(
                    goal=node.goal,
                    inputs=resolved_inputs,
                    completion={
                        "output_schema": _thaw(node.completion_output_schema),
                        "validator": node.completion_validator,
                        "required": list(node.completion_required),
                        "review": node.completion_review,
                    },
                ),
                event=input_event,
                intent=self._agent_profile.get("intent")
                if isinstance(self._agent_profile.get("intent"), Mapping)
                else {},
                intent_clarity={},
                autonomy_scope={},
                agent_profile=self._agent_profile,
                recent_steps=[
                    item
                    for item in state.step_records
                    if item["node_id"] == node.id and item["node_attempt"] == state.node_attempt
                ],
                available_capabilities={
                    "tools": [] if completion_only else list(node.capability_tools or ())
                },
                task_plan=state.task_plan,
            )
        except BrainPlanningError as exc:
            return self._commit_transition(
                state,
                node=node,
                event="failed",
                output=None,
                error=f"brain_planning_failed: {exc}",
                workflow=workflow,
                contract=contract,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._fail_integrity(state, f"brain_decision_integrity_error: {exc}")

        decision = prepared["decision"]
        operation = decision["operation"]
        if operation is None:
            redundant_review_ask = (
                node.completion_review == "required"
                and decision["ask"] is not None
                and decision["ask"].get("input_type") == "confirm"
            )
            task_plan_ask = state.task_plan is not None and decision["ask"] is not None
            suppressed_ask = redundant_review_ask or task_plan_ask
            if suppressed_ask:
                decision["ask"] = None
                decision["continuation"] = "continue"
            completed = loop.complete_step(
                prepared["record"],
                status=(
                    "failed"
                    if suppressed_ask
                    else ("waiting" if decision["ask"] is not None else "completed")
                ),
                state_delta=(
                    {
                        "completion_feedback": (
                            "The Harness already reviews this Node result; submit the "
                            "draft with complete_node instead of requesting confirmation"
                        )
                    }
                    if redundant_review_ask
                    else (
                        {
                            "completion_feedback": (
                                "An active TaskPlan cannot request user input directly; "
                                "record the current task as blocked to use the canonical "
                                "task-gap interaction"
                            )
                        }
                        if task_plan_ask
                        else None
                    )
                ),
            )
            pending: PendingOperation | None = None
            loop_state = completed["loop"]
            if decision["ask"] is not None:
                request_id = new_ulid()
                pending = PendingOperation(
                    id=new_ulid(),
                    kind=(
                        "judgment" if decision["human_judgment"]["required"] else "interaction"
                    ),
                    source="autonomous_ask",
                    node_id=node.id,
                    node_attempt=state.node_attempt,
                    request_id=request_id,
                    step_id=prepared["record"]["step_id"],
                    invocation_id=None,
                    adapter_id=None,
                    arguments=MappingProxyType({}),
                    proposal=_freeze_mapping(decision["ask"]),
                    decision=_freeze_mapping(decision["human_judgment"]),
                )
                loop_state = _waiting_loop_state(loop_state, prepared["record"]["step_id"])
            progressed = self._commit_loop_progress(
                state,
                loop_state,
                completed["record"],
                pending_operation=pending,
            )
            if completed["continuation"]["outcome"] == "fail":
                return self._commit_transition(
                    progressed,
                    node=node,
                    event="failed",
                    output=None,
                    error=completed["continuation"]["reason"],
                    workflow=workflow,
                    contract=contract,
                )
            return progressed

        if operation["kind"] == "workflow_control":
            return self._complete_autonomous_node(
                state,
                node=node,
                workflow=workflow,
                contract=contract,
                loop=loop,
                record=prepared["record"],
                arguments=operation["arguments"],
            )

        if operation["target"] not in set(node.capability_tools or ()):
            return self._fail_integrity(
                state,
                f"brain_decision_integrity_error: Operation {operation['target']!r} "
                "exceeds autonomous Node capabilities",
            )
        try:
            adapter = self._adapters.resolve(operation["target"])
        except ValueError as exc:
            return self._fail_integrity(
                state,
                f"brain_decision_integrity_error: {exc}",
            )
        if adapter.kind != operation["kind"]:
            return self._fail_integrity(
                state,
                "brain_decision_integrity_error: Operation kind does not match adapter",
            )
        operation["arguments"] = _materialize_operation_arguments(
            state,
            adapter,
            operation["arguments"],
        )
        try:
            operation_task_plan = _start_operation_task(
                state.task_plan,
                operation["arguments"],
                operation_target=str(operation["target"]),
            )
        except WorkflowRuntimeError as exc:
            completed = loop.complete_step(
                prepared["record"],
                status="failed",
                state_delta={"operation_error": str(exc)},
            )
            progressed = self._commit_loop_progress(
                state,
                completed["loop"],
                completed["record"],
            )
            if completed["continuation"]["outcome"] == "fail":
                return self._commit_transition(
                    progressed,
                    node=node,
                    event="failed",
                    output=None,
                    error=str(exc),
                    workflow=workflow,
                    contract=contract,
                )
            return progressed
        budget_error = _operation_budget_error(
            state,
            adapter,
            operation["arguments"],
        )
        if budget_error is not None:
            completed = loop.complete_step(
                prepared["record"],
                status="failed",
                state_delta={"operation_error": budget_error},
            )
            progressed = self._commit_loop_progress(
                state,
                completed["loop"],
                completed["record"],
            )
            if completed["continuation"]["outcome"] == "fail":
                return self._commit_transition(
                    progressed,
                    node=node,
                    event="failed",
                    output=None,
                    error=budget_error,
                    workflow=workflow,
                    contract=contract,
                )
            return progressed
        prerequisite_error = _fresh_output_prerequisite_error(
            state,
            adapter,
            operation["arguments"],
            self.store.invocations(state.run_id),
        )
        if prerequisite_error is not None:
            completed = loop.complete_step(
                prepared["record"],
                status="failed",
                state_delta={"operation_error": prerequisite_error},
            )
            progressed = self._commit_loop_progress(
                state,
                completed["loop"],
                completed["record"],
            )
            if completed["continuation"]["outcome"] == "fail":
                return self._commit_transition(
                    progressed,
                    node=node,
                    event="failed",
                    output=None,
                    error=prerequisite_error,
                    workflow=workflow,
                    contract=contract,
                )
            return progressed
        protocol_error = _research_protocol_error(
            state,
            adapter,
            operation["arguments"],
        )
        if protocol_error is not None:
            completed = loop.complete_step(
                prepared["record"],
                status="failed",
                state_delta={"operation_error": protocol_error},
            )
            progressed = self._commit_loop_progress(
                state,
                completed["loop"],
                completed["record"],
            )
            if completed["continuation"]["outcome"] == "fail":
                return self._commit_transition(
                    progressed,
                    node=node,
                    event="failed",
                    output=None,
                    error=protocol_error,
                    workflow=workflow,
                    contract=contract,
                )
            return progressed
        if self._dispatcher is None:
            return self._fail_integrity(state, "Workflow runtime has no Operation dispatcher")
        invocation = InvocationRecord.prepared(
            run_id=state.run_id,
            node_id=node.id,
            node_attempt=state.node_attempt,
            adapter_id=adapter.id,
            arguments=dict(operation["arguments"]),
            workflow_revision=state.revision,
        )
        self.store.prepare_invocation(invocation)
        self.store.claim_dispatch(invocation.id, expected_workflow_revision=state.revision)
        try:
            dispatch = self._dispatcher.dispatch(adapter, dict(operation["arguments"]))
        except Exception as exc:
            dispatch = OperationDispatchResult(
                outcome="uncertain" if adapter.side_effect else "failed",
                error=str(exc),
            )
        task_result_error = _task_operation_result_error(
            state,
            operation["arguments"],
            dispatch,
        )
        if task_result_error is not None:
            dispatch = OperationDispatchResult(
                outcome="failed",
                output=dispatch.output,
                error=task_result_error,
            )
        if dispatch.outcome == "uncertain":
            self.store.finish_invocation(
                invocation.id,
                status="reconciliation_required",
                error=dispatch.error or "Autonomous Operation outcome is uncertain",
            )
            return self.store.commit(
                replace(
                    state,
                    status="reconciliation_required",
                    failure=dispatch.error or "Autonomous Operation outcome is uncertain",
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
            )
        if dispatch.outcome == "waiting":
            self.store.finish_invocation(
                invocation.id,
                status="waiting",
                output=dispatch.output,
                error=dispatch.error,
            )
        else:
            self.store.finish_invocation(
                invocation.id,
                status="terminal",
                output=dispatch.output,
                error=dispatch.error,
            )
        step_status = (
            "failed"
            if dispatch.outcome == "failed"
            else ("waiting" if dispatch.outcome == "waiting" else "completed")
        )
        updated_task_plan = _finish_operation_task(
            operation_task_plan,
            operation["arguments"],
            dispatch,
        )
        completion_loop = AgentLoop(
            state=_reserve_task_plan_completion_step(
                loop.state,
                before=state.task_plan,
                after=updated_task_plan,
            ),
            brain=self._brain,
        )
        completed = completion_loop.complete_step(
            prepared["record"],
            status=step_status,
            state_delta={
                "operation_output": dispatch.output,
                "operation_error": dispatch.error,
            },
        )
        pending = None
        loop_state = completed["loop"]
        if dispatch.outcome == "waiting":
            proposal, judgment = _waiting_dispatch_details(dispatch)
            request_id = str(judgment.get("approval_id") or new_ulid())
            pending = PendingOperation(
                id=new_ulid(),
                kind="judgment",
                source="autonomous_operation",
                node_id=node.id,
                node_attempt=state.node_attempt,
                request_id=request_id,
                step_id=prepared["record"]["step_id"],
                invocation_id=invocation.id,
                adapter_id=adapter.id,
                arguments=_freeze_mapping(operation["arguments"]),
                proposal=_freeze_mapping(proposal),
                decision=_freeze_mapping(judgment),
            )
            loop_state = _waiting_loop_state(loop_state, prepared["record"]["step_id"])
        progressed = self._commit_loop_progress(
            state,
            loop_state,
            completed["record"],
            pending_operation=pending,
            task_plan=updated_task_plan,
        )
        if completed["continuation"]["outcome"] == "fail":
            return self._commit_transition(
                progressed,
                node=node,
                event="failed",
                output=None,
                error=dispatch.error or completed["continuation"]["reason"],
                workflow=workflow,
                contract=contract,
            )
        return progressed

    def _complete_autonomous_node(
        self,
        state: WorkflowState,
        *,
        node: Node,
        workflow: Workflow,
        contract: ExecutionContract,
        loop: AgentLoop,
        record: StepRecord,
        arguments: Mapping[str, Any],
    ) -> WorkflowState:
        result = arguments.get("result")
        rejection = "complete_node requires result" if "result" not in arguments else None
        if rejection is None:
            try:
                if node.completion_review == "required":
                    result = _normalize_review_result(result)
                if _uses_legacy_task_plan(state.task_plan):
                    result = _assemble_task_plan_result(state, result)
                self._validate_complete_node(
                    state,
                    node=node,
                    result=result,
                    workflow=workflow,
                )
            except (WorkflowInstanceError, WorkflowRuntimeError, ValueError) as exc:
                rejection = str(exc)

        completed = loop.complete_step(
            record,
            state_delta={
                "completion_result": result,
                "completion_feedback": rejection,
            },
        )
        if rejection is not None:
            progressed = self._commit_loop_progress(
                state,
                completed["loop"],
                completed["record"],
            )
            if completed["continuation"]["outcome"] == "fail":
                return self._commit_transition(
                    progressed,
                    node=node,
                    event="failed",
                    output=None,
                    error=completed["continuation"]["reason"],
                    workflow=workflow,
                    contract=contract,
                )
            return progressed
        if node.completion_review == "required":
            request_id = new_ulid()
            pending = PendingOperation(
                id=new_ulid(),
                kind="interaction",
                source="node_review",
                node_id=node.id,
                node_attempt=state.node_attempt,
                request_id=request_id,
                step_id=completed["record"]["step_id"],
                invocation_id=None,
                adapter_id=None,
                arguments=_freeze_mapping({"result": result}),
                proposal=_freeze_mapping(
                    {
                        "kind": "node_review",
                        "prompt": "Review the proposed Node result before continuing.",
                        "node_id": node.id,
                        "draft": result,
                    }
                ),
                decision=MappingProxyType({}),
            )
            waiting_loop = _waiting_loop_state(
                completed["loop"],
                completed["record"]["step_id"],
            )
            return self._commit_loop_progress(
                state,
                waiting_loop,
                completed["record"],
                pending_operation=pending,
            )
        progressed = self._commit_loop_progress(
            state,
            completed["loop"],
            completed["record"],
        )
        return self._commit_transition(
            progressed,
            node=node,
            event="completed",
            output=result,
            error=None,
            workflow=workflow,
            contract=contract,
        )

    def _validate_complete_node(
        self,
        state: WorkflowState,
        *,
        node: Node,
        result: Any,
        workflow: Workflow,
    ) -> None:
        if node.completion_output_schema is None:
            raise WorkflowRuntimeError("autonomous Node completion contract is incomplete")
        validate_instance(
            node.completion_output_schema,
            result,
            context=f"Node {node.id!r} completion",
        )
        for field in node.completion_required:
            value = _required_value(result, field)
            if not _is_meaningful(value):
                raise WorkflowRuntimeError(
                    f"Node {node.id!r} completion requires meaningful field {field!r}"
                )
        if node.completion_validator is not None:
            validator = self._validators.resolve(node.completion_validator)
            try:
                reason = validator.rejection_reason(result)
            except Exception as exc:
                raise WorkflowRuntimeError(
                    f"completion validator {validator.id!r} failed: {exc}"
                ) from exc
            if reason is not None:
                raise WorkflowRuntimeError(
                    f"completion validator {validator.id!r} rejected Node result: {reason}"
                )
        if state.task_plan is not None:
            items = state.task_plan.get("items")
            if not isinstance(items, tuple | list) or not items:
                raise WorkflowRuntimeError("TaskPlan is malformed or empty")
            open_items = [
                str(item.get("id") or "unknown")
                for item in items
                if not isinstance(item, Mapping) or item.get("status") != "completed"
            ]
            if open_items:
                raise WorkflowRuntimeError(
                    f"TaskPlan still has open items: {', '.join(open_items)}"
                )
            if _uses_legacy_task_plan(state.task_plan):
                _validate_task_plan_result(state, result)
        active_steps = [
            item["step_id"]
            for item in state.step_records
            if item["node_id"] == node.id
            and item["node_attempt"] == state.node_attempt
            and item["status"] in {"running", "waiting"}
        ]
        if active_steps:
            raise WorkflowRuntimeError(
                f"Node attempt still has unresolved StepRecord(s): {', '.join(active_steps)}"
            )
        active_invocations = [
            item.id
            for item in self.store.invocations(state.run_id)
            if item.node_id == node.id
            and item.node_attempt == state.node_attempt
            and item.status in {"prepared", "dispatching", "waiting", "reconciliation_required"}
        ]
        if state.pending_operation is not None or active_invocations:
            raise WorkflowRuntimeError("Node attempt still has an unresolved Operation or effect")
        self._preflight_next_node(state, node=node, result=result, workflow=workflow)

    def _preflight_next_node(
        self,
        state: WorkflowState,
        *,
        node: Node,
        result: Any,
        workflow: Workflow,
    ) -> None:
        target = node.transitions.get("completed")
        if target is None:
            raise WorkflowRuntimeError(
                f"Node {node.id!r} has no transition for completion"
            )
        if target in {WORKFLOW_COMPLETE, WORKFLOW_FAIL}:
            return
        next_node = workflow.node(target)
        outputs = dict(state.node_outputs)
        outputs[node.id] = _freeze(result=result)
        projected = replace(state, node_outputs=MappingProxyType(outputs))
        try:
            inputs = _resolve_node_inputs(next_node, projected)
            if next_node.execution == "operation":
                if next_node.operation is None:
                    raise WorkflowRuntimeError(
                        f"operation Node {next_node.id!r} has no adapter"
                    )
                adapter = self._adapters.resolve_node_adapter(next_node.operation)
                inputs = _materialize_operation_arguments(projected, adapter, inputs)
                _validate_schema(
                    adapter.input_schema,
                    inputs,
                    context=f"next Node {next_node.id!r} input",
                )
        except (KeyError, TypeError, ValueError, WorkflowRuntimeError) as exc:
            raise WorkflowRuntimeError(
                f"next Node {next_node.id!r} is not ready: {exc}"
            ) from exc

    def _commit_loop_progress(
        self,
        state: WorkflowState,
        loop_state: LoopState,
        record: StepRecord,
        *,
        pending_operation: PendingOperation | None = None,
        task_plan: TaskPlan | None = None,
    ) -> WorkflowState:
        status: WorkflowStatus = "waiting" if loop_state["status"] == "waiting" else "running"
        return self.store.commit(
            replace(
                state,
                status=status,
                loop_state=LoopState(**loop_state),
                step_records=(*state.step_records, StepRecord(**record)),
                pending_operation=pending_operation,
                task_plan=(
                    _freeze_mapping(task_plan) if task_plan is not None else state.task_plan
                ),
                revision=state.revision + 1,
            ),
            expected_revision=state.revision,
        )

    def _fail_integrity(self, state: WorkflowState, error: str) -> WorkflowState:
        return self.store.commit(
            replace(
                state,
                status="failed",
                failure=error,
                revision=state.revision + 1,
            ),
            expected_revision=state.revision,
        )

    def _execute_operation(
        self,
        state: WorkflowState,
        node: Node,
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> WorkflowState:
        if node.operation is None:
            raise WorkflowRuntimeError(f"operation Node {node.id!r} has no adapter")
        if self._dispatcher is None:
            raise WorkflowRuntimeError("Workflow runtime has no Operation dispatcher")
        adapter = self._adapters.resolve_node_adapter(node.operation)
        arguments = _resolve_node_inputs(node, state)
        arguments = _materialize_operation_arguments(state, adapter, arguments)
        _validate_schema(adapter.input_schema, arguments, context=f"Node {node.id!r} input")
        prerequisite_error = _fresh_output_prerequisite_error(
            state,
            adapter,
            arguments,
            self.store.invocations(state.run_id),
        )
        if prerequisite_error is not None:
            return self._commit_transition(
                state,
                node=node,
                event="failed",
                output=None,
                error=prerequisite_error,
                workflow=workflow,
                contract=contract,
            )
        protocol_error = _research_protocol_error(state, adapter, arguments)
        if protocol_error is not None:
            return self._commit_transition(
                state,
                node=node,
                event="failed",
                output=None,
                error=protocol_error,
                workflow=workflow,
                contract=contract,
            )
        invocation = InvocationRecord.prepared(
            run_id=state.run_id,
            node_id=node.id,
            node_attempt=state.node_attempt,
            adapter_id=adapter.id,
            arguments=arguments,
            workflow_revision=state.revision,
        )
        self.store.prepare_invocation(invocation)
        self.store.claim_dispatch(
            invocation.id,
            expected_workflow_revision=state.revision,
        )
        try:
            result = self._dispatcher.dispatch(adapter, arguments)
        except Exception as exc:
            if adapter.side_effect:
                result = OperationDispatchResult(outcome="uncertain", error=str(exc))
            else:
                result = OperationDispatchResult(outcome="failed", error=str(exc))

        if result.outcome == "uncertain":
            self.store.finish_invocation(
                invocation.id,
                status="reconciliation_required",
                error=result.error or "Operation outcome is uncertain",
            )
            current = self.store.get(state.run_id)
            return self.store.commit(
                replace(
                    current,
                    status="reconciliation_required",
                    failure=result.error or "Operation outcome is uncertain",
                    revision=current.revision + 1,
                ),
                expected_revision=current.revision,
            )

        self.store.finish_invocation(
            invocation.id,
            status="waiting" if result.outcome == "waiting" else "terminal",
            output=result.output,
            error=result.error,
        )
        current = self.store.get(state.run_id)
        if current.cancellation_requested:
            return self.store.commit(
                replace(current, status="cancelled", revision=current.revision + 1),
                expected_revision=current.revision,
            )

        if result.outcome == "waiting":
            proposal, judgment = _waiting_dispatch_details(result)
            pending = PendingOperation(
                id=new_ulid(),
                kind="judgment",
                source="operation_node",
                node_id=node.id,
                node_attempt=state.node_attempt,
                request_id=str(judgment.get("approval_id") or new_ulid()),
                step_id=None,
                invocation_id=invocation.id,
                adapter_id=adapter.id,
                arguments=_freeze_mapping(arguments),
                proposal=_freeze_mapping(proposal),
                decision=_freeze_mapping(judgment),
            )
            return self.store.commit(
                replace(
                    current,
                    status="waiting",
                    pending_operation=pending,
                    revision=current.revision + 1,
                ),
                expected_revision=current.revision,
            )
        event, error = self._validate_operation_completion(
            node=node,
            adapter=adapter,
            result=result,
        )

        return self._commit_transition(
            current,
            node=node,
            event=event,
            output=result.output,
            error=error,
            workflow=workflow,
            contract=contract,
        )

    def _validate_operation_completion(
        self,
        *,
        node: Node,
        adapter: OperationAdapter,
        result: OperationDispatchResult,
    ) -> tuple[str, str | None]:
        if result.outcome != "completed":
            return "failed", result.error
        try:
            _validate_schema(
                adapter.output_schema,
                result.output,
                context=f"adapter {adapter.id!r} output",
            )
            if node.completion_output_schema is not None:
                validate_instance(
                    node.completion_output_schema,
                    result.output,
                    context=f"Node {node.id!r} output",
                )
            if node.completion_validator is not None:
                validator = self._validators.resolve(node.completion_validator)
                reason = validator.rejection_reason(result.output)
                if reason is not None:
                    raise WorkflowRuntimeError(
                        f"completion validator {validator.id!r} rejected Node output: {reason}"
                    )
        except (WorkflowInstanceError, WorkflowRuntimeError, ValueError) as exc:
            return "failed", str(exc)
        return "completed", None

    def _commit_transition(
        self,
        state: WorkflowState,
        *,
        node: Node,
        event: str,
        output: Any,
        error: str | None,
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> WorkflowState:
        target = node.transitions.get(event)
        if target is None:
            return self.store.commit(
                replace(
                    state,
                    status="failed",
                    failure=error or f"Node {node.id!r} has no transition for {event!r}",
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
            )
        transition_count = state.transition_count + 1
        maximum = int(contract.snapshot["limits"].get("max_transitions", 1))
        if transition_count > maximum:
            return self.store.commit(
                replace(
                    state,
                    status="failed",
                    failure="Workflow transition budget exhausted",
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
            )
        record = TransitionRecord(
            source_node_id=node.id,
            source_attempt=state.node_attempt,
            event=event,
            target=target,
        )
        outputs = dict(state.node_outputs)
        if event == "completed":
            outputs[node.id] = result_output = _freeze(result=output)
        else:
            result_output = None
        frozen_outputs = MappingProxyType(outputs)
        transitions = (*state.transitions, record)
        if target == WORKFLOW_COMPLETE:
            return self.store.commit(
                replace(
                    state,
                    status="completed",
                    output=result_output,
                    node_outputs=frozen_outputs,
                    transitions=transitions,
                    transition_count=transition_count,
                    revision=state.revision + 1,
                    loop_state=None,
                    pending_operation=None,
                ),
                expected_revision=state.revision,
            )
        if target == WORKFLOW_FAIL:
            return self.store.commit(
                replace(
                    state,
                    status="failed",
                    failure=error or f"Node {node.id!r} failed",
                    node_outputs=frozen_outputs,
                    transitions=transitions,
                    transition_count=transition_count,
                    revision=state.revision + 1,
                    loop_state=None,
                    pending_operation=None,
                ),
                expected_revision=state.revision,
            )
        workflow.node(target)
        return self.store.commit(
            replace(
                state,
                current_node_id=target,
                node_attempt=state.node_attempt + 1,
                node_outputs=frozen_outputs,
                transitions=transitions,
                transition_count=transition_count,
                revision=state.revision + 1,
                loop_state=None,
                pending_operation=None,
            ),
            expected_revision=state.revision,
        )

    @staticmethod
    def _verify_contract_definition(
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> None:
        if contract.snapshot.get("definition_fingerprint") != workflow.definition_fingerprint:
            raise WorkflowRuntimeError("execution contract does not bind this Workflow definition")

    @staticmethod
    def _verify_resume(
        state: WorkflowState,
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> None:
        if workflow.id != state.workflow_id:
            raise WorkflowRuntimeError("Workflow selection changed during resume")
        if workflow.definition_fingerprint != state.definition_fingerprint:
            raise WorkflowRuntimeError("Workflow definition changed during resume")
        if contract.fingerprint != state.execution_contract_fingerprint:
            raise WorkflowRuntimeError("execution contract changed during resume")


def _resolve_node_inputs(node: Node, state: WorkflowState) -> dict[str, Any]:
    return {key: _resolve_input_value(value, state) for key, value in node.inputs.items()}


def _intent_confirmation_for_node(
    node: Node,
    state: WorkflowState,
    workflow: Workflow,
    intent: Any,
) -> dict[str, Any] | None:
    raw_binding = node.inputs.get("intent")
    if not isinstance(raw_binding, Mapping) or set(raw_binding) != {"$ref"}:
        return None
    input_ref = raw_binding.get("$ref")
    if not isinstance(input_ref, str):
        return None
    for proof in reversed(state.intent_confirmation_proofs):
        if (
            proof.run_id != state.run_id
            or proof.workflow_id != state.workflow_id
            or proof.execution_contract_fingerprint
            != state.execution_contract_fingerprint
            or proof.approved_revision > state.revision
        ):
            continue
        if proof.source == "user_input":
            if input_ref != proof.input_ref and not input_ref.startswith(
                proof.input_ref + "/"
            ):
                continue
            if proof.reviewed_result_hash != compute_fingerprint(
                _thaw(state.workflow_input)
            ):
                continue
        else:
            if proof.source_node_id is None or (
                input_ref != proof.input_ref
                and not input_ref.startswith(proof.input_ref + "/")
            ):
                continue
            source_node = workflow.node(proof.source_node_id)
            if source_node.completion_review != "required":
                continue
            source_output = state.node_outputs.get(proof.source_node_id)
            if (
                source_output is None
                or proof.reviewed_result_hash != compute_fingerprint(
                    _thaw(source_output)
                )
                or not any(
                    item.source_node_id == proof.source_node_id
                    and item.source_attempt == proof.source_node_attempt
                    and item.event == "completed"
                    for item in state.transitions
                )
            ):
                continue
        return {
            **proof.snapshot(),
            "confirmed_intent_hash": compute_fingerprint(intent),
        }
    return None


def _autonomous_step_budget(
    state: WorkflowState,
    node: Node,
    contract: ExecutionContract,
) -> int:
    configured = node.max_steps or int(contract.snapshot["limits"].get("max_steps", 20))
    if state.task_plan is None:
        return configured
    items = state.task_plan.get("items")
    if not isinstance(items, list | tuple) or not items:
        return configured
    # Each research item normally uses search + finding. Two additional steps
    # per item cover bounded protocol repair, plus four for final completion.
    return max(configured, len(items) * 4 + 4)


def _task_plan_is_closed(plan: Mapping[str, Any] | None) -> bool:
    if plan is None:
        return False
    items = plan.get("items")
    return bool(items) and isinstance(items, list | tuple) and all(
        isinstance(item, Mapping) and item.get("status") == "completed" for item in items
    )


def _uses_legacy_task_plan(plan: Mapping[str, Any] | None) -> bool:
    return plan is not None and plan.get("kind") != "task_graph"


def _reserve_task_plan_completion_step(
    loop: LoopState,
    *,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> LoopState:
    """Reserve one final synthesis step when the last TaskPlan item closes."""

    closes_plan = not _task_plan_is_closed(before) and _task_plan_is_closed(after)
    hits_ceiling = loop["step_index"] + 1 >= loop["max_auto_steps"]
    if not closes_plan or not hits_ceiling:
        return loop
    extended = LoopState(**loop)
    extended["max_auto_steps"] = loop["max_auto_steps"] + 1
    return extended


def _resolve_input_value(value: Any, state: WorkflowState) -> Any:
    if not isinstance(value, Mapping) or set(value) != {"$ref"}:
        return _thaw(value)
    ref = value["$ref"]
    if not isinstance(ref, str):
        raise WorkflowRuntimeError("Node input reference must be a string")
    if ref == "#/workflow/input":
        return _thaw(state.workflow_input)
    if ref.startswith("#/workflow/input/"):
        return _resolve_pointer(state.workflow_input, ref.removeprefix("#/workflow/input/"))
    if ref.startswith("#/nodes/"):
        parts = ref.removeprefix("#/nodes/").split("/")
        if len(parts) < 2 or parts[1] != "output":
            raise WorkflowRuntimeError(f"invalid Node output reference {ref!r}")
        try:
            value = state.node_outputs[parts[0]]
        except KeyError as exc:
            raise WorkflowRuntimeError(f"Node output is not committed for {parts[0]!r}") from exc
        return _resolve_pointer(value, "/".join(parts[2:])) if len(parts) > 2 else _thaw(value)
    raise WorkflowRuntimeError(f"unsupported Node input reference {ref!r}")


def _resolve_pointer(value: Any, pointer: str) -> Any:
    current = value
    if not pointer:
        return _thaw(current)
    for raw in pointer.split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, tuple | list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise WorkflowRuntimeError(f"unresolved Workflow input pointer /{pointer}")
    return _thaw(current)


def _validate_schema(schema: Mapping[str, Any], value: Any, *, context: str) -> None:
    try:
        Draft202012Validator(_thaw(schema)).validate(value)
    except ValidationError as exc:
        raise WorkflowRuntimeError(f"{context}: {exc.message}") from exc


def _waiting_dispatch_details(
    result: OperationDispatchResult,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(result.output, Mapping):
        raise WorkflowRuntimeError("waiting Operation did not persist its reviewed proposal")
    proposal = result.output.get("proposal")
    decision = result.output.get("decision")
    if not isinstance(proposal, Mapping) or not isinstance(decision, Mapping):
        raise WorkflowRuntimeError("waiting Operation has incomplete review identity")
    return proposal, decision


def _waiting_loop_state(loop_state: LoopState, step_id: str) -> LoopState:
    updated = LoopState(**loop_state)
    updated["status"] = "waiting"
    updated["continuation"] = "wait_for_judgment"
    updated["pending_step_id"] = step_id
    return updated


def _resolve_pending_step(
    state: WorkflowState,
    pending: PendingOperation,
    *,
    status: Literal["completed", "failed"],
    state_delta: Mapping[str, Any],
) -> tuple[tuple[StepRecord, ...], LoopState | None]:
    if pending.step_id is None or state.loop_state is None:
        raise WorkflowRuntimeError("pending autonomous work has no Step identity")
    records = list(state.step_records)
    matches = [index for index, item in enumerate(records) if item["step_id"] == pending.step_id]
    if len(matches) != 1 or records[matches[0]]["status"] != "waiting":
        raise WorkflowRuntimeError("pending StepRecord identity is missing or no longer waiting")
    record = StepRecord(**records[matches[0]])
    record["status"] = status
    record["state_delta"] = dict(state_delta)
    records[matches[0]] = record
    loop_state = LoopState(**state.loop_state)
    loop_state["status"] = "active"
    loop_state["continuation"] = "continue"
    loop_state["pending_step_id"] = None
    return tuple(records), loop_state


def _required_value(result: Any, field: str) -> Any:
    current = result
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _normalize_review_result(result: Any) -> Any:
    if not isinstance(result, Mapping):
        return result
    task_plan = _task_plan_from_result(result)
    if task_plan is None:
        return dict(result)
    normalized = dict(result)
    normalized["task_plan"] = task_plan
    return normalized


def _task_plan_from_result(result: Any) -> TaskPlan | None:
    from ..tasks import create_task_plan

    if not isinstance(result, Mapping):
        return None
    raw_plan = result.get("task_plan")
    if not isinstance(raw_plan, Mapping):
        return None
    raw_items = raw_plan.get("items")
    if not isinstance(raw_items, list | tuple):
        raise WorkflowRuntimeError("reviewed task_plan.items must be an array")
    tasks = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise WorkflowRuntimeError("reviewed task_plan items must be objects")
        tasks.append({"id": item.get("id"), "title": item.get("title")})
    try:
        return create_task_plan(tasks)
    except ValueError as exc:
        raise WorkflowRuntimeError(f"reviewed task_plan is invalid: {exc}") from exc


def _plain_task_plan(plan: Mapping[str, Any]) -> TaskPlan:
    items = plan.get("items")
    if not isinstance(items, list | tuple):
        raise WorkflowRuntimeError("TaskPlan items must be an array")
    result: TaskPlan = {
        "version": int(plan.get("version") or 1),
        "items": [
            cast("TaskItem", dict(item)) for item in items if isinstance(item, Mapping)
        ],
        "current_task_id": cast(str | None, plan.get("current_task_id")),
        "current_action": cast(str | None, plan.get("current_action")),
        "last_activity": cast(str | None, plan.get("last_activity")),
    }
    return result


def _start_operation_task(
    plan: Mapping[str, Any] | None,
    arguments: Mapping[str, Any],
    *,
    operation_target: str = "",
) -> TaskPlan | None:
    from ..tasks import resume_task, start_task

    if plan is None:
        return None
    task_id = str(arguments.get("task_id") or "").strip()
    if operation_target == "get_current_time":
        return _plain_task_plan(plan)
    if not task_id:
        raise WorkflowRuntimeError("active TaskPlan requires Operation argument 'task_id'")
    plain = _plain_task_plan(plan)
    item = next((item for item in plain["items"] if item["id"] == task_id), None)
    if item is None:
        raise WorkflowRuntimeError(f"Operation references unknown TaskPlan item {task_id!r}")
    action = str(
        arguments.get("query")
        or " | ".join(
            str(item.get("query") or "")
            for item in arguments.get("searches") or []
            if isinstance(item, Mapping)
        )
        or " | ".join(str(item) for item in arguments.get("queries") or [])
        or arguments.get("question")
        or arguments.get("conclusion")
        or arguments.get("subject")
        or item["title"]
    )
    try:
        if item["status"] == "blocked":
            return resume_task(plain, task_id, current_action=action)
        if item["status"] == "in_progress" and plain["current_task_id"] == task_id:
            plain["current_action"] = action
            plain["last_activity"] = action
            return plain
        return start_task(plain, task_id, current_action=action)
    except ValueError as exc:
        raise WorkflowRuntimeError(f"cannot start TaskPlan item {task_id!r}: {exc}") from exc


def _finish_operation_task(
    plan: TaskPlan | None,
    arguments: Mapping[str, Any],
    dispatch: OperationDispatchResult,
) -> TaskPlan | None:
    from ..tasks import complete_task

    if plan is None:
        return None
    task_id = str(arguments.get("task_id") or "").strip()
    try:
        if dispatch.outcome != "completed" or not isinstance(dispatch.output, Mapping):
            return plan
        task_resolution = str(dispatch.output.get("task_resolution") or "")
        if task_resolution == "completed":
            summary = str(dispatch.output.get("conclusion") or "Research question resolved")
            return complete_task(plan, task_id, summary=summary)
        if task_resolution == "blocked":
            return _complete_limited_task(plan, task_id)
        resolution = str(dispatch.output.get("resolution") or "")
        if resolution == "sourced":
            sources = dispatch.output.get("sources")
            count = len(sources) if isinstance(sources, list | tuple) else 0
            plan["last_activity"] = f"Collected {count} usable source(s)"
        elif resolution == "no_evidence":
            plan["last_activity"] = "Query returned no usable evidence; revise the query"
        elif resolution == "unavailable":
            plan["last_activity"] = "Search services unavailable; retry or declare a blocker"
    except ValueError as exc:
        raise WorkflowRuntimeError(f"cannot update TaskPlan item {task_id!r}: {exc}") from exc
    return plan


def _task_operation_result_error(
    state: WorkflowState,
    arguments: Mapping[str, Any],
    dispatch: OperationDispatchResult,
) -> str | None:
    if dispatch.outcome != "completed" or not isinstance(dispatch.output, Mapping):
        return None
    if dispatch.output.get("task_resolution") != "completed":
        return None
    task_id = str(arguments.get("task_id") or "")
    citations = _citation_urls(dispatch.output.get("citations"))
    observed = _observed_source_urls_by_task(state).get(task_id, set())
    if not citations or not citations.issubset(observed):
        return (
            f"TaskPlan item {task_id!r} finding citations must come from usable "
            "sources observed for that question"
        )
    return None


def _research_protocol_error(
    state: WorkflowState,
    adapter: OperationAdapter,
    arguments: Mapping[str, Any],
) -> str | None:
    if adapter.id == "verify_claim_evidence":
        return _verify_claim_protocol_error(state, arguments)
    if adapter.id == "record_research_finding":
        return _record_finding_protocol_error(state, arguments)
    return None


def _materialize_operation_arguments(
    state: WorkflowState,
    adapter: OperationAdapter,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve persisted protocol outputs into canonical downstream arguments."""

    materialized = dict(arguments)
    authority_bindings = _research_authority_bindings(state)
    if adapter.id == "verify_claim_evidence":
        materialized["authority_bindings"] = authority_bindings
        return materialized
    if adapter.id != "record_research_finding":
        return materialized
    method = str(materialized.get("verification_method") or "").strip()
    if method == "unverifiable_flag":
        authority_binding_fingerprint = _authority_binding_fingerprint(
            authority_bindings
        )
        materialized["verification_id"] = ""
        materialized["evidence"] = []
        materialized["verified_claim"] = ""
        materialized["authority_binding_fingerprint"] = authority_binding_fingerprint
        materialized["provenance"] = {
            "verification_id": "",
            "search_ids": [],
            "evaluated_urls": [],
            "evaluations": [],
            "searches": [],
            "authority_binding_fingerprint": authority_binding_fingerprint,
        }
        return materialized
    verification_id = str(materialized.get("verification_id") or "").strip()
    verification = _verification_outputs(state).get(verification_id)
    if verification is None:
        return materialized
    if str(verification.get("task_id") or "").strip() != str(
        materialized.get("task_id") or ""
    ).strip():
        return materialized
    verified_claim = str(verification.get("claim") or "")
    materialized["verified_claim"] = verified_claim
    materialized["conclusion"] = verified_claim
    materialized["authority_binding_fingerprint"] = str(
        verification.get("authority_binding_fingerprint") or ""
    )
    materialized["evidence"] = _thaw(verification.get("evidence") or [])
    materialized["provenance"] = _research_finding_provenance(
        state,
        task_id=str(materialized.get("task_id") or "").strip(),
        verification=verification,
    )
    return materialized


def _research_authority_bindings(state: WorkflowState) -> list[dict[str, Any]]:
    """Read the reviewed binding set without interpreting child-model arguments."""

    manifest = state.workflow_input.get("context_manifest")
    if not isinstance(manifest, Mapping):
        return []
    extensions = manifest.get("extensions")
    if not isinstance(extensions, Mapping):
        return []
    research_task = extensions.get("research_task")
    if not isinstance(research_task, Mapping):
        return []
    bindings = research_task.get("authority_bindings")
    if not isinstance(bindings, list | tuple) or not all(
        isinstance(item, Mapping) for item in bindings
    ):
        return []
    return [dict(_thaw(item)) for item in bindings]


def _authority_binding_fingerprint(
    authority_bindings: list[dict[str, Any]],
) -> str:
    return "sha256:" + compute_fingerprint(authority_bindings)


def _research_finding_provenance(
    state: WorkflowState,
    *,
    task_id: str,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    time_by_token: dict[str, Mapping[str, Any]] = {}
    search_token_by_id: dict[str, str] = {}
    for record in state.step_records:
        operation = record["decision"].get("operation")
        output = record["state_delta"].get("operation_output")
        if not isinstance(operation, Mapping) or not isinstance(output, Mapping):
            continue
        target = str(operation.get("target") or "")
        if target == "get_current_time":
            token = str(output.get("time_token") or "").strip()
            if token:
                time_by_token[token] = output
            continue
        if target != "public_web_search":
            continue
        arguments = operation.get("arguments")
        if not isinstance(arguments, Mapping) or str(
            arguments.get("task_id") or ""
        ).strip() != task_id:
            continue
        search_id = str(output.get("search_id") or "").strip()
        token = str(arguments.get("time_token") or "").strip()
        if search_id and token:
            search_token_by_id[search_id] = token

    search_outputs = _task_search_outputs(state, task_id)
    verification_search_ids = [
        str(item).strip()
        for item in verification.get("search_ids") or []
        if str(item).strip()
    ]
    searches: list[dict[str, Any]] = []
    for search_id in verification_search_ids:
        output = search_outputs.get(search_id)
        token = search_token_by_id.get(search_id, "")
        current_time = time_by_token.get(token)
        if output is None or current_time is None:
            continue
        usable_urls = [
            str(source.get("url") or "").strip()
            for source in output.get("sources") or []
            if isinstance(source, Mapping)
            and source.get("usable") is True
            and str(source.get("url") or "").strip().startswith(("http://", "https://"))
        ]
        searches.append(
            {
                "search_id": search_id,
                "structured_searches": _thaw(output.get("searches") or []),
                "usable_urls": list(dict.fromkeys(usable_urls)),
                "current_time": {
                    "issued_at": str(current_time.get("issued_at") or ""),
                    "current_date": str(current_time.get("current_date") or ""),
                    "timezone": str(current_time.get("timezone") or ""),
                },
            }
        )
    return {
        "verification_id": str(verification.get("verification_id") or ""),
        "authority_binding_fingerprint": str(
            verification.get("authority_binding_fingerprint") or ""
        ),
        "search_ids": verification_search_ids,
        "evaluated_urls": [
            str(item).strip()
            for item in verification.get("evaluated_urls") or []
            if str(item).strip()
        ],
        "evaluations": _thaw(verification.get("evaluations") or []),
        "searches": searches,
    }


def _verify_claim_protocol_error(
    state: WorkflowState,
    arguments: Mapping[str, Any],
) -> str | None:
    task_id = str(arguments.get("task_id") or "").strip()
    requested_ids = {
        str(item).strip()
        for item in arguments.get("search_ids") or []
        if str(item).strip()
    }
    searches = _task_search_outputs(state, task_id)
    all_ids = set(searches)
    if not all_ids:
        return f"TaskPlan item {task_id!r} has no completed search to verify"
    if requested_ids != all_ids:
        return (
            f"TaskPlan item {task_id!r} verification must cover every search_id "
            f"from the current task; expected {sorted(all_ids)!r}"
        )
    expected_urls = set().union(
        *(_usable_source_urls(output) for output in searches.values())
    )
    item_urls = {
        str(item.get("source_url") or "").strip()
        for item in arguments.get("items") or []
        if isinstance(item, Mapping) and str(item.get("source_url") or "").strip()
    }
    if item_urls != expected_urls:
        missing = sorted(expected_urls - item_urls)
        unknown = sorted(item_urls - expected_urls)
        details: list[str] = []
        if missing:
            details.append("missing usable URLs: " + ", ".join(missing))
        if unknown:
            details.append("unknown URLs: " + ", ".join(unknown))
        return (
            f"TaskPlan item {task_id!r} verification must evaluate every usable "
            "source from its searches; " + "; ".join(details)
        )
    return None


def _record_finding_protocol_error(
    state: WorkflowState,
    arguments: Mapping[str, Any],
) -> str | None:
    task_id = str(arguments.get("task_id") or "").strip()
    method = str(arguments.get("verification_method") or "").strip()
    verification_id = str(arguments.get("verification_id") or "").strip()
    searches = _task_search_outputs(state, task_id)
    if method == "unverifiable_flag":
        if searches:
            return (
                f"TaskPlan item {task_id!r} cannot use unverifiable_flag after searching"
            )
        if verification_id:
            return "unverifiable_flag findings must not supply verification_id"
        return None
    verification = _verification_outputs(state).get(verification_id)
    if verification is None or str(verification.get("task_id") or "") != task_id:
        return (
            f"TaskPlan item {task_id!r} requires a verification_id produced for "
            "that task in this run"
        )
    current_search_ids = set(searches)
    covered_ids = {
        str(item).strip()
        for item in verification.get("search_ids") or []
        if str(item).strip()
    }
    if covered_ids != current_search_ids:
        return (
            f"TaskPlan item {task_id!r} verification is stale; verify all current "
            "search outputs before recording the Finding"
        )
    expected_urls = set().union(
        *(_usable_source_urls(output) for output in searches.values())
    ) if searches else set()
    evaluated_urls = {
        str(item).strip()
        for item in verification.get("evaluated_urls") or []
        if str(item).strip()
    }
    if evaluated_urls != expected_urls:
        return (
            f"TaskPlan item {task_id!r} verification does not cover its complete "
            "usable source set"
        )
    if _evidence_signature(arguments.get("evidence")) != _evidence_signature(
        verification.get("evidence")
    ):
        return (
            f"TaskPlan item {task_id!r} Finding evidence must exactly match its "
            "verified evidence"
        )
    conclusion = " ".join(str(arguments.get("conclusion") or "").split())
    verified_claim = " ".join(str(verification.get("claim") or "").split())
    if conclusion != verified_claim:
        return (
            f"TaskPlan item {task_id!r} Finding conclusion must exactly match "
            "the verified claim; verify the revised conclusion before recording it"
        )
    return None


def _task_search_outputs(
    state: WorkflowState,
    task_id: str,
) -> dict[str, Mapping[str, Any]]:
    outputs: dict[str, Mapping[str, Any]] = {}
    for record in state.step_records:
        operation = record["decision"].get("operation")
        if not isinstance(operation, Mapping) or operation.get("target") != "public_web_search":
            continue
        operation_arguments = operation.get("arguments")
        if not isinstance(operation_arguments, Mapping) or str(
            operation_arguments.get("task_id") or ""
        ) != task_id:
            continue
        output = record["state_delta"].get("operation_output")
        if not isinstance(output, Mapping):
            continue
        search_id = str(output.get("search_id") or "").strip()
        if search_id:
            outputs[search_id] = output
    return outputs


def _verification_outputs(state: WorkflowState) -> dict[str, Mapping[str, Any]]:
    outputs: dict[str, Mapping[str, Any]] = {}
    for record in state.step_records:
        operation = record["decision"].get("operation")
        if not isinstance(operation, Mapping) or operation.get("target") != (
            "verify_claim_evidence"
        ):
            continue
        output = record["state_delta"].get("operation_output")
        if not isinstance(output, Mapping):
            continue
        verification_id = str(output.get("verification_id") or "").strip()
        if verification_id:
            outputs[verification_id] = output
    return outputs


def _usable_source_urls(output: Mapping[str, Any]) -> set[str]:
    sources = output.get("sources")
    if not isinstance(sources, list | tuple):
        return set()
    return {
        str(source.get("url") or "").strip()
        for source in sources
        if isinstance(source, Mapping)
        and source.get("usable") is True
        and str(source.get("url") or "").strip().startswith(("http://", "https://"))
    }


def _validate_task_plan_result(state: WorkflowState, result: Any) -> None:
    """Validate final TaskPlan coverage against sources observed in this Node."""

    if not isinstance(result, Mapping) or "key_findings" not in result:
        return
    raw_results = result.get("key_findings")
    if not isinstance(raw_results, list | tuple):
        raise WorkflowRuntimeError("TaskPlan completion key_findings must be an array")
    plan_items = state.task_plan.get("items") if state.task_plan is not None else None
    if not isinstance(plan_items, list | tuple):
        raise WorkflowRuntimeError("TaskPlan completion has no valid items")
    planned = {
        str(item.get("id") or ""): item
        for item in plan_items
        if isinstance(item, Mapping) and item.get("id")
    }
    reported: dict[str, Mapping[str, Any]] = {}
    for item in raw_results:
        if not isinstance(item, Mapping):
            raise WorkflowRuntimeError("TaskPlan completion results must be objects")
        task_id = str(item.get("task_id") or "")
        if not task_id or task_id in reported:
            raise WorkflowRuntimeError("TaskPlan completion task ids must be unique")
        reported[task_id] = item
    missing = sorted(set(planned) - set(reported))
    unknown = sorted(set(reported) - set(planned))
    if missing or unknown:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise WorkflowRuntimeError("TaskPlan completion coverage mismatch: " + "; ".join(details))

    observed_by_task = _observed_source_urls_by_task(state)
    observed_urls = set().union(*observed_by_task.values()) if observed_by_task else set()
    findings = _recorded_task_findings(state)
    global_citations = _citation_urls(result.get("citations"))
    if not global_citations.issubset(observed_urls):
        raise WorkflowRuntimeError("final citations must come from observed usable sources")
    limited_items: list[str] = []
    used_citations: set[str] = set()
    for task_id, item in reported.items():
        status = str(item.get("status") or "")
        citations = _evidence_urls(item.get("evidence"))
        plan_summary = str(planned[task_id].get("summary") or "")
        if plan_summary.startswith("[limited]"):
            if status != "limited" or citations:
                raise WorkflowRuntimeError(
                    f"limited TaskPlan item {task_id!r} must be reported without citations"
                )
            limited_items.append(task_id)
            continue
        if status != "sourced" or not citations:
            raise WorkflowRuntimeError(
                f"resolved TaskPlan item {task_id!r} requires sourced citations"
            )
        if not citations.issubset(observed_by_task.get(task_id, set())):
            raise WorkflowRuntimeError(
                f"TaskPlan item {task_id!r} cites a source not observed for that question"
            )
        if not citations.issubset(global_citations):
            raise WorkflowRuntimeError(
                f"TaskPlan item {task_id!r} citations must appear in final citations"
            )
        finding = findings.get(task_id)
        if finding is None:
            raise WorkflowRuntimeError(
                f"TaskPlan item {task_id!r} has no recorded research finding"
            )
        if str(item.get("conclusion") or "").strip() != str(
            finding.get("conclusion") or ""
        ).strip():
            raise WorkflowRuntimeError(
                f"TaskPlan item {task_id!r} result must match its recorded finding"
            )
        if citations != _citation_urls(finding.get("citations")):
            raise WorkflowRuntimeError(
                f"TaskPlan item {task_id!r} citations must match its recorded finding"
            )
        for field in ("implications", "confidence"):
            if str(item.get(field) or "").strip() != str(
                finding.get(field) or ""
            ).strip():
                raise WorkflowRuntimeError(
                    f"TaskPlan item {task_id!r} {field} must match its recorded finding"
                )
        if _evidence_signature(item.get("evidence")) != _evidence_signature(
            finding.get("evidence")
        ):
            raise WorkflowRuntimeError(
                f"TaskPlan item {task_id!r} evidence must match its recorded finding"
            )
        used_citations.update(citations)
    limitations = result.get("limitations")
    if limited_items and not (
        isinstance(limitations, list | tuple)
        and any(str(item).strip() for item in limitations)
    ):
        raise WorkflowRuntimeError(
            "limited TaskPlan items require an explicit final limitation"
        )
    if global_citations != used_citations:
        raise WorkflowRuntimeError(
            "final citations must exactly match the sources used by key findings"
        )


def _assemble_task_plan_result(state: WorkflowState, result: Any) -> dict[str, Any]:
    """Build canonical findings and citations from recorded TaskPlan evidence."""

    if not isinstance(result, Mapping):
        raise WorkflowRuntimeError("TaskPlan completion result must be an object")
    plan_items = state.task_plan.get("items") if state.task_plan is not None else None
    if not isinstance(plan_items, list | tuple):
        raise WorkflowRuntimeError("TaskPlan completion has no valid items")
    if any(
        not isinstance(item, Mapping) or item.get("status") != "completed"
        for item in plan_items
    ):
        return dict(result)
    recorded = _recorded_task_findings(state)
    findings: list[dict[str, Any]] = []
    citations: list[str] = []
    limitations = [
        str(item).strip()
        for item in result.get("limitations") or []
        if str(item).strip()
    ]
    for item in plan_items:
        if not isinstance(item, Mapping):
            raise WorkflowRuntimeError("TaskPlan contains a malformed item")
        task_id = str(item.get("id") or "")
        question = str(item.get("title") or task_id)
        summary = str(item.get("summary") or "")
        if summary.startswith("[limited]"):
            source = recorded.get(task_id)
            finding = {
                "task_id": task_id,
                "question": str(source.get("question") or question) if source else question,
                "conclusion": (
                    str(source.get("conclusion") or "")
                    if source
                    else "公开证据不足, 当前无法形成确定结论。"
                ),
                "implications": (
                    str(source.get("implications") or "")
                    if source
                    else "最终报告不对该问题作确定结论。"
                ),
                "confidence": str(source.get("confidence") or "low") if source else "low",
                "status": "limited",
                "evidence": _thaw(source.get("evidence") or []) if source else [],
            }
            source_limitations = source.get("limitations") if source else None
            if isinstance(source_limitations, list | tuple):
                for limitation in source_limitations:
                    text = str(limitation).strip()
                    if text and text not in limitations:
                        limitations.append(text)
            fallback = f"问题“{question}”缺少足够公开证据, 报告保留该疑问。"
            if not source_limitations and fallback not in limitations:
                limitations.append(fallback)
        else:
            source = recorded.get(task_id)
            if source is None:
                raise WorkflowRuntimeError(
                    f"TaskPlan item {task_id!r} has no recorded research finding"
                )
            evidence = _thaw(source.get("evidence") or [])
            finding = {
                "task_id": task_id,
                "question": str(source.get("question") or question),
                "conclusion": str(source.get("conclusion") or ""),
                "implications": str(source.get("implications") or ""),
                "confidence": str(source.get("confidence") or ""),
                "status": "sourced",
                "evidence": evidence,
            }
            if isinstance(evidence, list | tuple):
                for evidence_item in evidence:
                    if not isinstance(evidence_item, Mapping):
                        continue
                    url = str(evidence_item.get("source_url") or "").strip()
                    if url.startswith(("http://", "https://")) and url not in citations:
                        citations.append(url)
            source_limitations = source.get("limitations")
            if isinstance(source_limitations, list | tuple):
                for limitation in source_limitations:
                    text = str(limitation).strip()
                    if text and text not in limitations:
                        limitations.append(text)
        findings.append(finding)
    assembled = dict(result)
    assembled["key_findings"] = findings
    assembled["citations"] = citations
    assembled["limitations"] = limitations
    return assembled


def _observed_source_urls_by_task(state: WorkflowState) -> dict[str, set[str]]:
    urls: dict[str, set[str]] = {}
    for record in state.step_records:
        output = record["state_delta"].get("operation_output")
        if not isinstance(output, Mapping):
            continue
        operation = record["decision"].get("operation")
        arguments = operation.get("arguments") if isinstance(operation, Mapping) else None
        task_id = (
            str(arguments.get("task_id") or "") if isinstance(arguments, Mapping) else ""
        )
        if not task_id:
            continue
        sources = output.get("sources")
        if not isinstance(sources, list | tuple):
            continue
        for source in sources:
            if isinstance(source, Mapping) and source.get("usable") is True:
                url = str(source.get("url") or "").strip()
                if url.startswith(("http://", "https://")):
                    urls.setdefault(task_id, set()).add(url)
    return urls


def _recorded_task_findings(state: WorkflowState) -> dict[str, Mapping[str, Any]]:
    findings: dict[str, Mapping[str, Any]] = {}
    for record in state.step_records:
        output = record["state_delta"].get("operation_output")
        if not isinstance(output, Mapping) or output.get("task_resolution") not in {
            "completed",
            "blocked",
        }:
            continue
        task_id = str(output.get("task_id") or "")
        if task_id:
            findings[task_id] = output
    return findings


def _citation_urls(value: Any) -> set[str]:
    if not isinstance(value, list | tuple):
        return set()
    return {
        str(item).strip()
        for item in value
        if str(item).strip().startswith(("http://", "https://"))
    }


def _evidence_urls(value: Any) -> set[str]:
    if not isinstance(value, list | tuple):
        return set()
    return {
        str(item.get("source_url") or "").strip()
        for item in value
        if isinstance(item, Mapping)
        and str(item.get("source_url") or "").strip().startswith(("http://", "https://"))
    }


def _evidence_signature(value: Any) -> set[tuple[str, str, str, str, str, str, str]]:
    if not isinstance(value, list | tuple):
        return set()
    return {
        (
            str(item.get("claim") or "").strip(),
            str(item.get("source_url") or "").strip(),
            str(item.get("source_type") or "").strip(),
            str(item.get("stance") or "").strip(),
            str(item.get("independence") or "").strip(),
            str(item.get("directness") or "").strip(),
            str(item.get("as_of") or "").strip(),
        )
        for item in value
        if isinstance(item, Mapping)
    }


def _complete_limited_task(plan: TaskPlan, task_id: str) -> TaskPlan:
    updated = _plain_task_plan(plan)
    item = next((item for item in updated["items"] if item["id"] == task_id), None)
    if item is None or item["status"] != "in_progress":
        raise WorkflowRuntimeError(
            f"cannot complete non-active TaskPlan item {task_id!r} with limitations"
        )
    item["status"] = "completed"
    item["summary"] = "[limited] Continued with an unresolved evidence gap"
    updated["current_task_id"] = None
    updated["current_action"] = None
    updated["last_activity"] = item["summary"]
    return updated


def _is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | list | tuple | set):
        return bool(value)
    return True


def _operation_budget_error(
    state: WorkflowState,
    adapter: OperationAdapter,
    arguments: Mapping[str, Any],
) -> str | None:
    maximum = adapter.max_calls_per_node
    relevant = [
        record
        for record in state.step_records
        if record["node_id"] == state.current_node_id
        and record["node_attempt"] == state.node_attempt
    ]
    last_human_index = max(
        (
            record["index"]
            for record in relevant
            if "human_input" in record["state_delta"]
        ),
        default=0,
    )
    count = 0
    for record in relevant:
        operation = record["decision"].get("operation")
        if (
            record["index"] > last_human_index
            and operation is not None
            and operation["target"] == adapter.id
        ):
            count += 1
    if maximum is not None and count >= maximum:
        return (
            f"Operation {adapter.id!r} exhausted its per-Node input-round budget "
            f"of {maximum} call(s)"
        )
    task_maximum = adapter.max_calls_per_task
    task_id = str(arguments.get("task_id") or "")
    if task_maximum is None or not task_id:
        return None
    task_count = 0
    for record in relevant:
        operation = record["decision"].get("operation")
        if operation is None:
            continue
        operation_arguments = operation.get("arguments")
        operation_task_id = (
            str(operation_arguments.get("task_id") or "")
            if isinstance(operation_arguments, Mapping)
            else ""
        )
        if operation_task_id != task_id:
            continue
        if operation["target"] == "record_research_finding":
            task_count = 0
        elif operation["target"] == adapter.id:
            task_count += 1
    if task_count < task_maximum:
        return None
    return (
        f"Operation {adapter.id!r} exhausted its per-Task budget of "
        f"{task_maximum} call(s) for {task_id!r}"
    )


def _fresh_output_prerequisite_error(
    state: WorkflowState,
    adapter: OperationAdapter,
    arguments: Mapping[str, Any],
    invocations: tuple[InvocationRecord, ...],
) -> str | None:
    prerequisite = adapter.fresh_output_prerequisite
    if prerequisite is None:
        return None
    argument_name = str(prerequisite["argument"])
    token = str(arguments.get(argument_name) or "").strip()
    issuer_adapter = str(prerequisite["issuer_adapter"])
    output_field = str(prerequisite["issuer_output_field"])
    issued_at_field = str(prerequisite["issued_at_field"])
    if not token:
        return (
            f"Operation {adapter.id!r} requires a fresh {argument_name!r}; "
            f"call {issuer_adapter!r} immediately before retrying"
        )
    issuer: InvocationRecord | None = None
    issuer_index: int | None = None
    issued_at = ""
    for index in range(len(invocations) - 1, -1, -1):
        invocation = invocations[index]
        if invocation.run_id != state.run_id or invocation.adapter_id != issuer_adapter:
            continue
        if invocation.status != "terminal" or not isinstance(invocation.output, Mapping):
            continue
        if str(invocation.output.get(output_field) or "").strip() != token:
            continue
        issuer = invocation
        issuer_index = index
        issued_at = str(invocation.output.get(issued_at_field) or "").strip()
        break
    if issuer is None:
        return (
            f"Operation {adapter.id!r} received an unknown or cross-run "
            f"{argument_name!r}; call {issuer_adapter!r} again"
        )
    try:
        parsed = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
    except ValueError:
        return (
            f"Operation {issuer_adapter!r} returned an invalid {issued_at_field!r}; "
            f"call it again before retrying {adapter.id!r}"
        )
    if age_seconds < -5 or age_seconds > int(prerequisite["ttl_seconds"]):
        return (
            f"Operation {adapter.id!r} received an expired {argument_name!r}; "
            f"call {issuer_adapter!r} again"
        )
    for invocation in invocations:
        if invocation.run_id != state.run_id or invocation.id == issuer.id:
            continue
        if str(invocation.arguments.get(argument_name) or "").strip() == token:
            return (
                f"Operation {adapter.id!r} received an already-used {argument_name!r}; "
                f"call {issuer_adapter!r} again"
            )
    if issuer_index is not None and issuer_index != len(invocations) - 1:
        return (
            f"Operation {adapter.id!r} requires {issuer_adapter!r} immediately before "
            f"the search; call it again to obtain a new {argument_name!r}"
        )
    return None


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], _freeze(result=dict(value)))


def _freeze(*, result: Any) -> Any:
    if isinstance(result, Mapping):
        return MappingProxyType({str(key): _freeze(result=item) for key, item in result.items()})
    if isinstance(result, list | tuple):
        return tuple(_freeze(result=item) for item in result)
    return result


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "DispatchOutcome",
    "InMemoryWorkflowStore",
    "IntentConfirmationProof",
    "InvocationRecord",
    "InvocationStatus",
    "OperationDispatchResult",
    "OperationDispatcher",
    "PendingOperation",
    "TransitionRecord",
    "WorkflowRuntime",
    "WorkflowRuntimeError",
    "WorkflowState",
    "WorkflowStatus",
]
