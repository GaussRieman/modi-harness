"""Durable Workflow execution for deterministic Operation Nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from threading import RLock
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from .._utils import new_ulid
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


@dataclass(frozen=True, slots=True)
class PendingOperation:
    """Exact durable work item awaiting one external decision or value."""

    id: str
    kind: Literal["judgment", "interaction"]
    source: Literal["operation_node", "autonomous_operation", "autonomous_ask"]
    node_id: str
    node_attempt: int
    request_id: str
    step_id: str | None
    invocation_id: str | None
    adapter_id: str | None
    arguments: Mapping[str, Any]
    proposal: Mapping[str, Any]
    decision: Mapping[str, Any]


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
    ) -> None:
        self._adapters = adapters
        self._validators = validators
        self._dispatcher = dispatcher
        self._brain = brain
        self._agent_profile = dict(agent_profile or {})
        self.store = store

    def bind_dispatcher(self, dispatcher: OperationDispatcher) -> None:
        """Bind the run-scoped gateway bridge before the first Node executes."""

        self._dispatcher = dispatcher

    def resume_waiting(
        self,
        run_id: str,
        *,
        payload: Mapping[str, Any],
        workflow: Workflow,
        contract: ExecutionContract,
    ) -> WorkflowState:
        """Resolve and resume the exact durable work item that caused the wait."""

        state = self.store.get(run_id)
        if state.status != "waiting":
            return state
        if payload.get("kind") == "cancel" or payload.get("decision") == "cancel":
            return self.store.cancel(run_id, reason="cancelled by user")
        pending = state.pending_operation
        if pending is None:
            raise WorkflowRuntimeError("waiting Workflow has no pending Operation")
        self._verify_resume(state, workflow, contract)
        supplied_id = payload.get(
            "judgment_id" if pending.kind == "judgment" else "interaction_id"
        )
        if supplied_id != pending.request_id:
            raise WorkflowRuntimeError("resume payload does not match the pending Operation")

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
        return self.store.commit(
            replace(
                state,
                status="running",
                pending_operation=None,
                loop_state=loop_state,
                step_records=records,
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
        return self.store.commit(
            replace(
                state,
                status="running",
                pending_operation=None,
                loop_state=loop_state,
                step_records=records,
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
    ) -> WorkflowState:
        self._verify_contract_definition(workflow, contract)
        try:
            validate_instance(workflow.input_schema, dict(workflow_input), context="Workflow input")
        except WorkflowInstanceError as exc:
            raise WorkflowRuntimeError(str(exc)) from exc
        state = WorkflowState(
            run_id=new_ulid(),
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
    ) -> WorkflowState:
        state = self.store.get(run_id)
        self._verify_resume(state, workflow, contract)
        if state.status != "running":
            return state
        node = workflow.node(state.current_node_id)
        if node.execution == "autonomous":
            return self._execute_autonomous(state, node, workflow, contract)
        return self._execute_operation(state, node, workflow, contract)

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
            max_auto_steps=node.max_steps or int(contract.snapshot["limits"].get("max_steps", 20)),
        )
        loop = AgentLoop(state=loop_state, brain=self._brain)
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
                available_capabilities={"tools": list(node.capability_tools or ())},
                task_plan=self._agent_profile.get("task_plan")
                if isinstance(self._agent_profile.get("task_plan"), Mapping)
                else None,
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
            completed = loop.complete_step(
                prepared["record"],
                status="waiting" if decision["ask"] is not None else "completed",
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
        budget_error = _operation_budget_error(state, adapter)
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
        completed = loop.complete_step(
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
        progressed = self._commit_loop_progress(state, completed["loop"], completed["record"])
        if rejection is not None:
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
    ) -> WorkflowState:
        status: WorkflowStatus = "waiting" if loop_state["status"] == "waiting" else "running"
        return self.store.commit(
            replace(
                state,
                status=status,
                loop_state=LoopState(**loop_state),
                step_records=(*state.step_records, StepRecord(**record)),
                pending_operation=pending_operation,
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
        _validate_schema(adapter.input_schema, arguments, context=f"Node {node.id!r} input")
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
) -> str | None:
    maximum = adapter.max_calls_per_node
    if maximum is None:
        return None
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
    if count < maximum:
        return None
    return (
        f"Operation {adapter.id!r} exhausted its per-Node input-round budget "
        f"of {maximum} call(s)"
    )


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
