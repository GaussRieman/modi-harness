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
        status: Literal["terminal", "reconciliation_required"],
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

    def invocations(self, run_id: str) -> tuple[InvocationRecord, ...]:
        with self._lock:
            return tuple(record for record in self._invocations.values() if record.run_id == run_id)

    def cancel(self, run_id: str, *, reason: str) -> WorkflowState:
        with self._lock:
            state = self.get(run_id)
            if state.status in {"completed", "failed", "cancelled"}:
                return state
            active = [
                item
                for item in self._invocations.values()
                if item.run_id == run_id and item.status in {"prepared", "dispatching"}
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
    ) -> WorkflowState:
        """Resume the same pinned Node after an external interaction."""

        state = self.store.get(run_id)
        if state.status != "waiting":
            return state
        if payload.get("kind") == "cancel" or payload.get("decision") == "cancel":
            return self.store.cancel(run_id, reason="cancelled by user")
        loop_state = state.loop_state
        if loop_state is not None:
            loop_state = LoopState(
                **{
                    **loop_state,
                    "status": "active",
                    "continuation": "continue",
                }
            )
        return self.store.commit(
            replace(
                state,
                status="running",
                loop_state=loop_state,
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
                event=None,
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
            completed = loop.complete_step(prepared["record"])
            progressed = self._commit_loop_progress(state, completed["loop"], completed["record"])
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
        if self._dispatcher is None:
            return self._fail_integrity(state, "Workflow runtime has no Operation dispatcher")
        try:
            dispatch = self._dispatcher.dispatch(adapter, dict(operation["arguments"]))
        except Exception as exc:
            dispatch = OperationDispatchResult(
                outcome="uncertain" if adapter.side_effect else "failed",
                error=str(exc),
            )
        if dispatch.outcome == "uncertain":
            return self.store.commit(
                replace(
                    state,
                    status="reconciliation_required",
                    failure=dispatch.error or "Autonomous Operation outcome is uncertain",
                    revision=state.revision + 1,
                ),
                expected_revision=state.revision,
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
        progressed = self._commit_loop_progress(state, completed["loop"], completed["record"])
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
        if "result" not in arguments:
            return self._fail_integrity(
                state,
                "brain_decision_integrity_error: complete_node requires result",
            )
        result = arguments["result"]
        rejection: str | None = None
        try:
            if node.completion_output_schema is None or node.completion_validator is None:
                raise WorkflowRuntimeError("autonomous Node completion contract is incomplete")
            validate_instance(
                node.completion_output_schema,
                result,
                context=f"Node {node.id!r} completion",
            )
            validator = self._validators.resolve(node.completion_validator)
            if not validator.validate(result):
                raise WorkflowRuntimeError(
                    f"completion validator {validator.id!r} rejected Node result"
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

    def _commit_loop_progress(
        self,
        state: WorkflowState,
        loop_state: LoopState,
        record: StepRecord,
    ) -> WorkflowState:
        status: WorkflowStatus = "waiting" if loop_state["status"] == "waiting" else "running"
        return self.store.commit(
            replace(
                state,
                status=status,
                loop_state=LoopState(**loop_state),
                step_records=(*state.step_records, StepRecord(**record)),
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
            status="terminal",
            output=result.output,
            error=result.error,
        )
        current = self.store.get(state.run_id)
        if current.cancellation_requested:
            return self.store.commit(
                replace(current, status="cancelled", revision=current.revision + 1),
                expected_revision=current.revision,
            )

        event = "failed"
        error = result.error
        if result.outcome == "waiting":
            return self.store.commit(
                replace(current, status="waiting", revision=current.revision + 1),
                expected_revision=current.revision,
            )
        if result.outcome == "completed":
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
                    if not validator.validate(result.output):
                        raise WorkflowRuntimeError(
                            f"completion validator {validator.id!r} rejected Node output"
                        )
            except (WorkflowInstanceError, WorkflowRuntimeError) as exc:
                error = str(exc)
            else:
                event = "completed"

        return self._commit_transition(
            current,
            node=node,
            event=event,
            output=result.output,
            error=error,
            workflow=workflow,
            contract=contract,
        )

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
    "TransitionRecord",
    "WorkflowRuntime",
    "WorkflowRuntimeError",
    "WorkflowState",
    "WorkflowStatus",
]
