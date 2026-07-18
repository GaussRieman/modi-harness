"""Deterministic Operation-only Task Graph parent runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

from .._utils import canonical_json, compute_fingerprint, new_ulid
from ..workflow.components import PinnedComponent, PinnedComponentRegistry
from ..workflow.contract import ExecutionContract, OperationAdapter, OperationAdapterRegistry
from ..workflow.definition import validate_instance
from ..workflow.types import TaskGraphNodeConfig
from ..workspace import SealedBlobRef, TaskArtifactStore
from .graph import apply_graph_patch, ready_tasks, validate_graph
from .submission import CandidateSubmission
from .transitions import transition_attempt, transition_graph, transition_task
from .types import (
    ArtifactRecord,
    AuditEvent,
    CandidateReceipt,
    ComponentInvocationKind,
    CriterionCoverage,
    DependencyRef,
    DurableComponentInvocation,
    GraphLimits,
    GraphPatch,
    IntentCriterion,
    IntentVersion,
    LeaseRecord,
    LongTaskState,
    TaskAttempt,
    TaskGraphRun,
    TaskRun,
    VerificationRecord,
)
from .verification import (
    invoke_component,
    json_value,
    prepare_component_invocation,
    verification_record,
    verifier_outcome,
)

TaskGraphOutcome = Literal["running", "waiting", "completed", "failed"]


class TaskGraphOperationBridge(Protocol):
    def dispatch_task_operation(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        dispatch_key: str,
    ) -> Any: ...

    def resume_task_operation(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        dispatch_key: str,
        proposal: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class TaskGraphPending:
    kind: Literal["operation", "goal"]
    request_id: str
    attempt_id: str | None = None
    adapter_id: str | None = None
    dispatch_key: str | None = None
    arguments: Mapping[str, Any] | None = None
    proposal: Mapping[str, Any] | None = None
    decision: Mapping[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TaskGraphStep:
    outcome: TaskGraphOutcome
    task_plan: Mapping[str, Any] | None
    output: Mapping[str, Any] | None = None
    error: str | None = None
    pending: TaskGraphPending | None = None


class TaskGraphRuntimeError(RuntimeError):
    """The persisted Task Graph or one of its pinned bindings is invalid."""


class OperationTaskGraphRuntime:
    """Advance one Operation-only root aggregate by one durable semantic step."""

    def __init__(
        self,
        *,
        root_run_id: str,
        node_id: str,
        config: TaskGraphNodeConfig,
        contract: ExecutionContract,
        components: PinnedComponentRegistry,
        adapters: OperationAdapterRegistry,
        dispatcher: TaskGraphOperationBridge,
        artifacts: TaskArtifactStore,
        state: LongTaskState | None = None,
    ) -> None:
        self._root_run_id = root_run_id
        self._node_id = node_id
        self._config = config
        self._contract = contract
        self._components = components
        self._adapters = adapters
        self._dispatcher = dispatcher
        self._artifacts = artifacts
        self._parent_node_attempt: int | None = None
        self.current_state = state

    def advance(
        self,
        *,
        inputs: Mapping[str, Any],
        root_revision: int,
        parent_node_attempt: int = 1,
    ) -> TaskGraphStep:
        try:
            if parent_node_attempt < 1:
                raise TaskGraphRuntimeError("parent Node attempt must be positive")
            self._parent_node_attempt = parent_node_attempt
            if self.current_state is None:
                return self._initialize(inputs, root_revision)
            state = self.current_state
            graph = state.graph
            if graph is None:
                return self._seed_graph(state, root_revision)
            if graph.status == "planning":
                return self._failed(state, root_revision, "Task Graph has no committed seed")
            if graph.status == "failed":
                return TaskGraphStep("failed", self.task_plan(), error="Task Graph failed")
            if graph.status == "completed":
                return TaskGraphStep("completed", self.task_plan(), output=self._node_output(state))
            if graph.status == "waiting":
                raise TaskGraphRuntimeError("waiting Task Graph requires exact resume payload")
            if graph.status == "verifying":
                return self._verify_goal(state, root_revision)

            receipt = next((item for item in state.receipts if item.status == "received"), None)
            if receipt is not None:
                return self._verify_candidate(state, receipt, root_revision)
            criterion = self._pending_criterion(state)
            if criterion is not None:
                return self._verify_criterion(state, criterion, root_revision)
            active_attempt = self._dispatchable_attempt(state)
            if active_attempt is not None:
                if active_attempt.status == "created":
                    return self._lease_attempt(state, active_attempt, root_revision)
                return self._dispatch_attempt(state, active_attempt, root_revision)
            ready = ready_tasks(graph)
            if ready:
                return self._prepare_attempt(state, ready[0], root_revision)
            return self._finish_or_fail_graph(state, root_revision)
        except Exception as exc:
            if self.current_state is None:
                raise TaskGraphRuntimeError(str(exc)) from exc
            return self._failed(
                self._fail_prepared_invocations(self.current_state, str(exc)),
                root_revision,
                str(exc),
            )

    def resume(
        self,
        *,
        pending: TaskGraphPending,
        payload: Mapping[str, Any],
        root_revision: int,
    ) -> TaskGraphStep:
        try:
            return self._resume_pending(
                pending=pending,
                payload=payload,
                root_revision=root_revision,
            )
        except Exception as exc:
            return self._failed(
                self._fail_prepared_invocations(self._require_state(), str(exc)),
                root_revision,
                str(exc),
            )

    def _resume_pending(
        self,
        *,
        pending: TaskGraphPending,
        payload: Mapping[str, Any],
        root_revision: int,
    ) -> TaskGraphStep:
        state = self._require_state()
        graph = self._require_graph(state)
        if graph.status != "waiting":
            raise TaskGraphRuntimeError("Task Graph is not waiting")
        decision = str(payload.get("kind") or payload.get("decision") or "")
        if pending.kind == "goal":
            return self._failed(
                state,
                root_revision,
                "ambiguous Goal requires replan or Intent rebase unavailable in Slice 1",
            )
        if pending.attempt_id is None or pending.adapter_id is None:
            raise TaskGraphRuntimeError("pending Operation has no Attempt binding")
        attempt = self._attempt(state, pending.attempt_id)
        task = self._task_by_ref(graph, attempt.task_ref)
        if decision != "approve":
            return self._fail_attempt(
                state,
                task,
                attempt,
                root_revision,
                str(payload.get("rationale") or "Operation rejected by human"),
            )
        adapter = self._resolve_attempt_adapter(task, attempt)
        arguments = dict(pending.arguments or self._operation_arguments(state, attempt))
        result = self._dispatcher.resume_task_operation(
            adapter,
            arguments,
            dispatch_key=attempt.dispatch_key,
            proposal=pending.proposal or {},
            decision=pending.decision or {},
        )
        resumed_state = replace(
            state,
            graph=transition_graph(graph, "active"),
            attempts=self._replace_attempts(
                state,
                transition_attempt(attempt, "running"),
            ),
        )
        resumed_task = transition_task(task, "running")
        resumed_state = replace(
            resumed_state,
            graph=self._replace_task(cast(TaskGraphRun, resumed_state.graph), resumed_task),
        )
        return self._consume_dispatch_result(
            resumed_state,
            resumed_task,
            self._attempt(resumed_state, attempt.attempt_id),
            adapter,
            result,
            root_revision,
        )

    def cancel(self, *, root_revision: int, reason: str) -> TaskGraphStep:
        state = self._require_state()
        graph = self._require_graph(state)
        attempts = list(state.attempts)
        tasks = list(graph.tasks)
        for index, attempt in enumerate(attempts):
            if attempt.status in {"created", "leased", "running", "waiting", "submitted"}:
                attempts[index] = transition_attempt(attempt, "cancelled", failure=reason)
        for index, task in enumerate(tasks):
            if task.status in {"pending", "running", "waiting", "verifying"}:
                tasks[index] = transition_task(
                    task,
                    "cancelled",
                    active_attempt_id=None,
                    failure=reason,
                )
        cancelled_graph = transition_graph(replace(graph, tasks=tuple(tasks)), "cancelled")
        self._commit(
            replace(state, graph=cancelled_graph, attempts=tuple(attempts)),
            root_revision,
            "graph_cancelled",
            {"reason": reason},
        )
        return TaskGraphStep("failed", self.task_plan(), error=reason)

    def receive_child_submission(
        self,
        submission: CandidateSubmission,
        *,
        root_revision: int,
    ) -> CandidateReceipt:
        state = self._require_state()
        duplicate = next(
            (item for item in state.receipts if item.submission_id == submission.submission_id),
            None,
        )
        if duplicate is not None:
            if duplicate.payload_hash != submission.payload_hash:
                raise TaskGraphRuntimeError(
                    "submission ID was reused with a different payload hash"
                )
            return duplicate
        pair = next(
            (
                item
                for item in state.receipts
                if item.attempt_id == submission.attempt_id
                and item.submission_sequence == submission.submission_sequence
            ),
            None,
        )
        if pair is not None:
            raise TaskGraphRuntimeError("submission sequence already belongs to another ID")
        attempt = self._attempt(state, submission.attempt_id)
        graph = self._require_graph(state)
        task = self._task_by_ref(graph, attempt.task_ref)
        self._validate_child_submission_binding(state, task, attempt, submission)
        expected_sequence = attempt.submission_sequence + 1
        if submission.submission_sequence != expected_sequence:
            raise TaskGraphRuntimeError(
                f"submission sequence gap: expected {expected_sequence}, "
                f"got {submission.submission_sequence}"
            )
        if submission.outcome != "candidate_completed":
            raise TaskGraphRuntimeError("only candidate_completed can enter Task verification")
        receipt = CandidateReceipt(
            submission_id=submission.submission_id,
            attempt_id=submission.attempt_id,
            submission_sequence=submission.submission_sequence,
            payload_hash=submission.payload_hash,
            status="received",
            task_ref=submission.task_ref,
            child_run_id=submission.child_run_id,
            lease_epoch=submission.lease_epoch,
            lease_token_hash=compute_fingerprint(submission.lease_token),
            context_manifest_fingerprint=submission.context_manifest_fingerprint,
            completion_contract_hash=submission.completion_contract_hash,
            parent_execution_contract_fingerprint=(
                submission.parent_execution_contract_fingerprint
            ),
            submission_outcome=submission.outcome,
            submission_snapshot=submission.snapshot(),
            decision="pending",
        )
        submitted = transition_attempt(
            _as_running_attempt(attempt),
            "submitted",
            submission_sequence=submission.submission_sequence,
            lease=replace(attempt.lease, retiring=True),
        )
        verifying = transition_task(_as_running_task(task), "verifying")
        committed = self._commit(
            replace(
                state,
                graph=self._replace_task(graph, verifying),
                attempts=self._replace_attempts(state, submitted),
                receipts=(*state.receipts, receipt),
            ),
            root_revision,
            "candidate_submitted",
            {
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
                "submission_id": submission.submission_id,
            },
        )
        return next(
            item for item in committed.receipts if item.submission_id == submission.submission_id
        )

    def task_plan(self) -> Mapping[str, Any] | None:
        state = self.current_state
        if state is None or state.graph is None:
            return None
        active = {ref.key for ref in state.graph.active_task_refs}
        tasks = [item for item in state.graph.tasks if item.ref.key in active]
        items: list[dict[str, Any]] = []
        current_task_id: str | None = None
        current_action: str | None = None
        for task in sorted(tasks, key=lambda item: (-item.priority, item.task_id)):
            status = {
                "pending": "pending",
                "running": "in_progress",
                "verifying": "in_progress",
                "waiting": "blocked",
                "completed": "completed",
                "failed": "blocked",
                "cancelled": "blocked",
            }[task.status]
            if status == "in_progress" and current_task_id is None:
                current_task_id = task.task_id
                current_action = task.goal
            items.append(
                {
                    "id": task.task_id,
                    "title": task.goal,
                    "status": status,
                    "summary": task.failure or ("Completed" if task.status == "completed" else None),
                }
            )
        return {
            "version": state.graph.revision,
            "items": items,
            "current_task_id": current_task_id,
            "current_action": current_action,
            "last_activity": state.events[-1].event_type if state.events else None,
        }

    def _initialize(self, inputs: Mapping[str, Any], root_revision: int) -> TaskGraphStep:
        intent = _parse_intent(inputs.get("intent", inputs))
        state = LongTaskState(
            root_run_id=self._root_run_id,
            revision=root_revision,
            intents=(intent,),
            graph=None,
            criterion_coverage=tuple(
                CriterionCoverage(item.id, "unsatisfied") for item in intent.success_criteria
            ),
            events=(
                AuditEvent(
                    new_ulid(),
                    "intent_confirmed" if intent.status == "confirmed" else "intent_rejected",
                    root_revision,
                    {"intent_id": intent.intent_id, "intent_version": intent.version},
                ),
            ),
        )
        self.current_state = state
        if intent.status != "confirmed":
            return TaskGraphStep(
                "failed",
                None,
                error="Task Graph execution requires a confirmed Intent",
            )
        return TaskGraphStep("running", None)

    def _seed_graph(self, state: LongTaskState, root_revision: int) -> TaskGraphStep:
        intent = self._confirmed_intent(state)
        component = self._component("planner", self._config.planner)
        empty = TaskGraphRun(
            graph_id=(
                "graph-"
                + compute_fingerprint(
                    {"root_run_id": self._root_run_id, "node_id": self._node_id}
                )[:24]
            ),
            intent_id=intent.intent_id,
            intent_version=intent.version,
            revision=0,
            status="planning",
            limits=GraphLimits(
                self._config.limits.max_tasks,
                self._config.limits.max_graph_depth,
                self._config.limits.max_replans,
                self._config.limits.max_concurrency,
                self._config.limits.max_child_runs,
            ),
            required_criteria=tuple(
                item.id for item in intent.success_criteria if item.required
            ),
        )
        inputs = {
            "intent": json_value(intent),
            "graph": json_value(empty),
            "trigger": "seed",
            "allowed_operation_adapters": [
                {
                    "id": adapter_id,
                    "fingerprint": compute_fingerprint(
                        self._adapters.resolve_node_adapter(adapter_id).snapshot()
                    ),
                }
                for adapter_id in self._config.operation_adapters
            ],
        }
        call = self._component_call(
            state,
            component,
            kind="planner",
            idempotency_key=f"root/{self._root_run_id}/graph/seed",
            inputs=inputs,
            root_revision=root_revision,
        )
        if isinstance(call, TaskGraphStep):
            return call
        output, invocation = call
        patch = output.get("patch") if isinstance(output, Mapping) else output
        if not isinstance(patch, GraphPatch):
            raise TaskGraphRuntimeError("Planner must return a GraphPatch or {'patch': GraphPatch}")
        graph = apply_graph_patch(empty, patch)
        self._validate_operation_only_graph(graph, intent)
        self._commit(
            replace(
                state,
                graph=graph,
                component_invocations=self._replace_component_invocations(
                    state, invocation
                ),
            ),
            root_revision,
            "graph_created",
            {"graph_id": graph.graph_id, "graph_revision": graph.revision},
        )
        return TaskGraphStep("running", self.task_plan())

    def _prepare_attempt(
        self,
        state: LongTaskState,
        task: TaskRun,
        root_revision: int,
    ) -> TaskGraphStep:
        binding = task.executor_policy.preferred_binding
        if binding.mode != "operation":
            raise TaskGraphRuntimeError("Slice 1 supports only operation Task bindings")
        if self._parent_node_attempt is None:
            raise TaskGraphRuntimeError("parent Node attempt is unavailable")
        adapter = self._resolve_task_adapter(task)
        if adapter.side_effect:
            raise TaskGraphRuntimeError(
                f"Operation adapter {adapter.id!r} has side effects; "
                "Slice 1 requires side-effect-free Operations"
            )
        attempt_id = new_ulid()
        component = self._component("context_builder", self._config.context_builder)
        context_inputs = {
            "intent": json_value(self._confirmed_intent(state)),
            "task": json_value(task),
            "dependency_outputs": self._dependency_outputs(state, task),
        }
        call = self._component_call(
            state,
            component,
            kind="context_builder",
            idempotency_key=(
                f"root/{self._root_run_id}/task/{task.task_id}/"
                f"revision/{task.task_revision}/context/{compute_fingerprint(context_inputs)}"
            ),
            inputs=context_inputs,
            root_revision=root_revision,
        )
        if isinstance(call, TaskGraphStep):
            return call
        output, invocation = call
        if not isinstance(output, Mapping) or not isinstance(
            output.get("operation_arguments"), Mapping
        ):
            raise TaskGraphRuntimeError(
                "Context Builder must return a mapping with operation_arguments"
            )
        arguments = dict(cast(Mapping[str, Any], output["operation_arguments"]))
        validate_instance(
            adapter.input_schema,
            arguments,
            context=f"Task {task.task_id!r} Operation input",
        )
        context_payload = canonical_json(
            {
                "manifest": json_value(output.get("context_manifest", {})),
                "operation_arguments": arguments,
            }
        )
        sealed = self._artifacts.seal(
            self._artifacts.stage(
                attempt_id,
                context_payload,
                mime_type="application/json",
                trust="trusted",
                metadata={"kind": "context_manifest", "task_id": task.task_id},
            )
        )
        artifact = self._artifact_record(
            sealed, kind="context_manifest", attempt_id=attempt_id
        )
        dispatch_key = compute_fingerprint(
            {
                "root_run_id": self._root_run_id,
                "task_ref": json_value(task.ref),
                "attempt_id": attempt_id,
                "binding": json_value(binding),
            }
        )
        attempt = TaskAttempt(
            attempt_id=attempt_id,
            task_ref=task.ref,
            status="created",
            executor_binding=binding,
            context_manifest_ref=sealed.uri,
            completion_contract_hash=compute_fingerprint(json_value(task.completion_contract)),
            dispatch_key=dispatch_key,
            lease=LeaseRecord(
                owner_id=self._root_run_id,
                epoch=1,
                token=compute_fingerprint({"attempt_id": attempt_id, "epoch": 1}),
                expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            ),
            parent_execution_contract_fingerprint=self._contract.fingerprint,
            parent_node_id=self._node_id,
            parent_node_attempt=self._parent_node_attempt,
        )
        running = transition_task(task, "running", active_attempt_id=attempt_id)
        graph = self._replace_task(self._require_graph(state), running)
        self._commit(
            replace(
                state,
                graph=graph,
                attempts=(*state.attempts, attempt),
                artifacts=(*state.artifacts, artifact),
                component_invocations=self._replace_component_invocations(
                    state, invocation
                ),
            ),
            root_revision,
            "attempt_prepared",
            {"task_id": task.task_id, "attempt_id": attempt_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _lease_attempt(
        self,
        state: LongTaskState,
        attempt: TaskAttempt,
        root_revision: int,
    ) -> TaskGraphStep:
        leased = transition_attempt(attempt, "leased")
        self._commit(
            replace(state, attempts=self._replace_attempts(state, leased)),
            root_revision,
            "task_leased",
            {"attempt_id": attempt.attempt_id, "dispatch_key": attempt.dispatch_key},
        )
        return TaskGraphStep("running", self.task_plan())

    def _dispatch_attempt(
        self,
        state: LongTaskState,
        attempt: TaskAttempt,
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        task = self._task_by_ref(graph, attempt.task_ref)
        adapter = self._resolve_attempt_adapter(task, attempt)
        arguments = self._operation_arguments(state, attempt)
        result = self._dispatcher.dispatch_task_operation(
            adapter,
            arguments,
            dispatch_key=attempt.dispatch_key,
        )
        return self._consume_dispatch_result(
            state,
            task,
            attempt,
            adapter,
            result,
            root_revision,
        )

    def _consume_dispatch_result(
        self,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
        adapter: OperationAdapter,
        result: Any,
        root_revision: int,
    ) -> TaskGraphStep:
        outcome = str(getattr(result, "outcome", "failed"))
        error = cast(str | None, getattr(result, "error", None))
        output = getattr(result, "output", None)
        if outcome == "waiting":
            proposal, decision = _waiting_details(output)
            waiting_attempt = transition_attempt(
                _as_running_attempt(attempt), "waiting"
            )
            waiting_task = transition_task(_as_running_task(task), "waiting")
            graph = transition_graph(
                self._replace_task(self._require_graph(state), waiting_task), "waiting"
            )
            updated = self._commit(
                replace(
                    state,
                    graph=graph,
                    attempts=self._replace_attempts(state, waiting_attempt),
                ),
                root_revision,
                "task_waiting",
                {"task_id": task.task_id, "attempt_id": attempt.attempt_id},
            )
            return TaskGraphStep(
                "waiting",
                self.task_plan(),
                pending=TaskGraphPending(
                    kind="operation",
                    request_id=str(decision.get("approval_id") or new_ulid()),
                    attempt_id=attempt.attempt_id,
                    adapter_id=adapter.id,
                    dispatch_key=attempt.dispatch_key,
                    arguments=self._operation_arguments(updated, waiting_attempt),
                    proposal=proposal,
                    decision=decision,
                    reason=error,
                ),
            )
        if outcome != "completed":
            return self._fail_attempt(
                state,
                task,
                attempt,
                root_revision,
                error or ("Operation outcome is uncertain" if outcome == "uncertain" else outcome),
            )
        validate_instance(
            adapter.output_schema,
            output,
            context=f"adapter {adapter.id!r} output",
        )
        payload = canonical_json(json_value(output))
        sealed = self._artifacts.seal(
            self._artifacts.stage(
                attempt.attempt_id,
                payload,
                mime_type="application/json",
                trust="untrusted",
                metadata={"kind": "candidate_output", "task_id": task.task_id},
            )
        )
        artifact = self._artifact_record(
            sealed, kind="candidate_output", attempt_id=attempt.attempt_id
        )
        receipt = CandidateReceipt(
            submission_id=new_ulid(),
            attempt_id=attempt.attempt_id,
            submission_sequence=attempt.submission_sequence + 1,
            payload_hash=sealed.content_hash,
            status="received",
            result_refs=(sealed.uri,),
        )
        submitted = transition_attempt(
            _as_running_attempt(attempt),
            "submitted",
            submission_sequence=receipt.submission_sequence,
        )
        verifying = transition_task(_as_running_task(task), "verifying")
        graph = self._replace_task(self._require_graph(state), verifying)
        self._commit(
            replace(
                state,
                graph=graph,
                attempts=self._replace_attempts(state, submitted),
                receipts=(*state.receipts, receipt),
                artifacts=(*state.artifacts, artifact),
            ),
            root_revision,
            "candidate_submitted",
            {
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
                "submission_id": receipt.submission_id,
            },
        )
        return TaskGraphStep("running", self.task_plan())

    def _verify_candidate(
        self,
        state: LongTaskState,
        receipt: CandidateReceipt,
        root_revision: int,
    ) -> TaskGraphStep:
        attempt = self._attempt(state, receipt.attempt_id)
        graph = self._require_graph(state)
        task = self._task_by_ref(graph, attempt.task_ref)
        submission = (
            CandidateSubmission.from_snapshot(receipt.submission_snapshot)
            if receipt.submission_snapshot
            else None
        )
        if submission is None:
            output = self._read_json_artifact(state, receipt.result_refs[0])
        else:
            self._validate_submission_artifacts(submission)
            output = submission.result
        validator_ids = task.completion_contract.validator_ids
        if not validator_ids:
            raise TaskGraphRuntimeError(f"Task {task.task_id!r} has no verifier")
        calls: list[tuple[PinnedComponent, Mapping[str, Any], str]] = []
        for validator_id in validator_ids:
            if validator_id not in self._config.task_validators:
                raise TaskGraphRuntimeError(
                    f"Task {task.task_id!r} selects unpinned verifier {validator_id!r}"
                )
            component = self._component("task_validators", validator_id)
            inputs = {
                "intent": json_value(self._confirmed_intent(state)),
                "task": json_value(task),
                "attempt": json_value(attempt),
                "candidate": output,
                "receipt": json_value(receipt),
            }
            calls.append(
                (
                    component,
                    inputs,
                    f"root/{self._root_run_id}/submission/{receipt.submission_id}/"
                    f"verifier/{validator_id}/{component.version}",
                )
            )
        prepared = list(state.component_invocations)
        added: list[DurableComponentInvocation] = []
        for component, call_inputs, key in calls:
            matches = [item for item in prepared if item.idempotency_key == key]
            if len(matches) > 1:
                raise TaskGraphRuntimeError(f"duplicate component invocation key {key!r}")
            if not matches:
                invocation = prepare_component_invocation(
                    component,
                    kind="task_verifier",
                    idempotency_key=key,
                    inputs=call_inputs,
                )
                prepared.append(invocation)
                added.append(invocation)
        if added:
            self._commit(
                replace(state, component_invocations=tuple(prepared)),
                root_revision,
                "component_invocations_prepared",
                {
                    "component_ids": [item.component_id for item in added],
                    "submission_id": receipt.submission_id,
                },
            )
            return TaskGraphStep("running", self.task_plan())
        existing_records = {
            item.validator_id: item
            for item in state.verification_records
            if item.submission_id == receipt.submission_id and item.kind == "task"
        }
        for component, call_inputs, key in calls:
            if component.id in existing_records:
                continue
            invocation = next(
                item for item in state.component_invocations if item.idempotency_key == key
            )
            value, completed_invocation = invoke_component(
                component,
                invocation=invocation,
                inputs=call_inputs,
            )
            outcome, result = verifier_outcome(component, value)
            record = verification_record(
                component=component,
                invocation=completed_invocation,
                submission_id=receipt.submission_id,
                target_ref=f"task:{task.task_id}:{task.task_revision}",
                outcome=outcome,
                result=result,
            )
            self._commit(
                replace(
                    state,
                    component_invocations=self._replace_component_invocations(
                        state, completed_invocation
                    ),
                    verification_records=(*state.verification_records, record),
                ),
                root_revision,
                "task_verifier_completed",
                {
                    "submission_id": receipt.submission_id,
                    "validator_id": component.id,
                    "outcome": outcome,
                },
            )
            return TaskGraphStep("running", self.task_plan())
        records = [
            item
            for item in state.verification_records
            if item.submission_id == receipt.submission_id and item.kind == "task"
        ]
        outcomes = [item.outcome for item in records]
        if len(records) != len(validator_ids):
            raise TaskGraphRuntimeError("not every pinned Task verifier has a durable result")
        if any(item == "repairable" for item in outcomes):
            return self._repair_child_submission(
                state,
                task,
                attempt,
                receipt,
                records,
                root_revision,
            )
        if any(item != "passed" for item in outcomes):
            rejected = replace(
                receipt,
                status="rejected",
                validator_record_ids=tuple(item.record_id for item in records),
                reason="Task verifier rejected candidate",
            )
            failed_state = replace(
                state,
                receipts=self._replace_receipts(state, rejected),
            )
            return self._fail_attempt(
                failed_state,
                task,
                attempt,
                root_revision,
                rejected.reason or "Task verification failed",
            )
        if submission is None:
            artifacts: tuple[ArtifactRecord, ...] = ()
            evidence: tuple[Any, ...] = ()
            result_refs = receipt.result_refs
        else:
            artifacts, evidence, result_refs = self._commit_submission_candidates(
                task, attempt, submission, records
            )
        accepted = replace(
            receipt,
            status="accepted",
            validator_record_ids=tuple(item.record_id for item in records),
            result_refs=result_refs,
            decision="accepted",
        )
        completed_attempt = transition_attempt(
            attempt, "completed", output_refs=result_refs
        )
        completed_task = transition_task(
            task,
            "completed",
            output_refs=result_refs,
            active_attempt_id=None,
        )
        self._commit(
            replace(
                state,
                graph=self._replace_task(graph, completed_task),
                attempts=self._replace_attempts(state, completed_attempt),
                receipts=self._replace_receipts(state, accepted),
                artifacts=(*state.artifacts, *artifacts),
                evidence_records=(*state.evidence_records, *evidence),
            ),
            root_revision,
            "task_completed",
            {"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _repair_child_submission(
        self,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
        receipt: CandidateReceipt,
        records: list[VerificationRecord],
        root_revision: int,
    ) -> TaskGraphStep:
        next_epoch = attempt.lease.epoch + 1
        next_token = compute_fingerprint(
            {"attempt_id": attempt.attempt_id, "epoch": next_epoch}
        )
        repaired_receipt = replace(
            receipt,
            status="repairable",
            validator_record_ids=tuple(item.record_id for item in records),
            decision="repairable",
            reason="Task verifier requested repair",
        )
        resumed_attempt = transition_attempt(
            attempt,
            "running",
            lease=replace(
                attempt.lease,
                epoch=next_epoch,
                token=next_token,
                retiring=False,
            ),
        )
        resumed_task = transition_task(task, "pending")
        resumed_task = replace(
            resumed_task,
            status="running",
            active_attempt_id=attempt.attempt_id,
        )
        self._commit(
            replace(
                state,
                graph=self._replace_task(self._require_graph(state), resumed_task),
                attempts=self._replace_attempts(state, resumed_attempt),
                receipts=self._replace_receipts(state, repaired_receipt),
            ),
            root_revision,
            "candidate_repair_requested",
            {
                "submission_id": receipt.submission_id,
                "attempt_id": attempt.attempt_id,
                "lease_epoch": next_epoch,
            },
        )
        return TaskGraphStep("running", self.task_plan())

    def _validate_submission_artifacts(self, submission: CandidateSubmission) -> None:
        for candidate in submission.artifact_candidates:
            try:
                self._artifacts.read_verified(
                    SealedBlobRef(
                        uri=candidate.uri,
                        content_hash=candidate.content_hash,
                        size_bytes=candidate.size_bytes,
                        mime_type=candidate.mime_type,
                        trust_level="untrusted",
                        metadata={},
                    )
                )
            except Exception as exc:
                raise TaskGraphRuntimeError(f"candidate artifact integrity failed: {exc}") from exc

    @staticmethod
    def _commit_submission_candidates(
        task: TaskRun,
        attempt: TaskAttempt,
        submission: CandidateSubmission,
        records: list[VerificationRecord],
    ) -> tuple[tuple[ArtifactRecord, ...], tuple[Any, ...], tuple[str, ...]]:
        from .types import EvidenceRecord

        artifacts = tuple(
            ArtifactRecord(
                artifact_id=new_ulid(),
                kind="candidate_output",
                uri=item.uri,
                content_hash=item.content_hash,
                size_bytes=item.size_bytes,
                mime_type=item.mime_type,
                trust_level="untrusted",
                producer_attempt_id=attempt.attempt_id,
                task_ref=task.ref,
                producer_child_run_id=attempt.child_run_id,
                artifact_type=item.artifact_type,
                schema_version=item.schema_version,
                visibility=item.visibility,
            )
            for item in submission.artifact_candidates
        )
        verifier_id = ",".join(
            sorted(item.validator_id or "" for item in records if item.validator_id)
        )
        evidence = tuple(
            EvidenceRecord(
                evidence_id=item.claim_id,
                criterion_id=None,
                claim=item.statement,
                source_ref=item.source_candidate_uri,
                producer_attempt_id=attempt.attempt_id,
                verification_method="task_verifier",
                verification_status="verified",
                verifier_id=verifier_id,
                verified_at=datetime.now(UTC).isoformat(),
                child_run_id=attempt.child_run_id,
                visibility=item.visibility,
            )
            for item in submission.evidence_claims
        )
        result_refs = tuple(item.uri for item in submission.artifact_candidates)
        if not result_refs:
            result_refs = (f"submission://{submission.submission_id}/result",)
        return artifacts, evidence, result_refs

    def _verify_criterion(
        self,
        state: LongTaskState,
        criterion: IntentCriterion,
        root_revision: int,
    ) -> TaskGraphStep:
        validator_id = criterion.validator_id
        if validator_id is None:
            if len(self._config.criterion_validators) != 1:
                raise TaskGraphRuntimeError(
                    f"criterion {criterion.id!r} has no unambiguous verifier"
                )
            validator_id = self._config.criterion_validators[0]
        if validator_id not in self._config.criterion_validators:
            raise TaskGraphRuntimeError(
                f"criterion {criterion.id!r} selects unpinned verifier {validator_id!r}"
            )
        component = self._component("criterion_validators", validator_id)
        graph = self._require_graph(state)
        supporting = [
            item
            for item in self._active_tasks(graph)
            if item.status == "completed" and criterion.id in item.supports
        ]
        inputs = {
            "intent": json_value(self._confirmed_intent(state)),
            "criterion": json_value(criterion),
            "tasks": [json_value(item) for item in supporting],
        }
        call = self._component_call(
            state,
            component,
            kind="criterion_verifier",
            idempotency_key=(
                f"root/{self._root_run_id}/criterion/{criterion.id}/graph/{graph.revision}"
            ),
            inputs=inputs,
            root_revision=root_revision,
        )
        if isinstance(call, TaskGraphStep):
            return call
        value, invocation = call
        outcome, result = verifier_outcome(component, value)
        status = "satisfied" if outcome == "passed" else "blocked"
        record = VerificationRecord(
            record_id=new_ulid(),
            kind="criterion",
            target_ref=f"criterion:{criterion.id}",
            component_fingerprint=component.fingerprint,
            input_hash=invocation.input_hash,
            status=_verification_status(outcome),
            evidence_refs=tuple(result.get("evidence_refs") or ()),
            reason=cast(str | None, result.get("reason")),
        )
        coverage = replace(
            self._coverage(state, criterion.id),
            status=cast(Any, status),
            evidence_refs=record.evidence_refs,
            verified_by=record.record_id,
        )
        self._commit(
            replace(
                state,
                component_invocations=self._replace_component_invocations(
                    state, invocation
                ),
                verification_records=(*state.verification_records, record),
                criterion_coverage=self._replace_coverage(state, coverage),
            ),
            root_revision,
            "criterion_verified" if status == "satisfied" else "criterion_verification_failed",
            {"criterion_id": criterion.id, "status": status},
        )
        return TaskGraphStep("running", self.task_plan())

    def _finish_or_fail_graph(
        self,
        state: LongTaskState,
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        active = self._active_tasks(graph)
        failed = [item.task_id for item in active if item.required and item.status == "failed"]
        if failed:
            return self._failed(
                state,
                root_revision,
                f"required Task failed: {', '.join(sorted(failed))}",
            )
        incomplete = [
            item.task_id
            for item in active
            if item.required and item.status not in {"completed", "cancelled"}
        ]
        if incomplete:
            return self._failed(
                state,
                root_revision,
                f"Task Graph has no ready work: {', '.join(sorted(incomplete))}",
            )
        self._commit(
            replace(state, graph=transition_graph(graph, "verifying")),
            root_revision,
            "goal_verification_started",
            {"graph_id": graph.graph_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _verify_goal(self, state: LongTaskState, root_revision: int) -> TaskGraphStep:
        graph = self._require_graph(state)
        component = self._component("goal_verifier", self._config.goal_verifier)
        inputs = {
            "intent": json_value(self._confirmed_intent(state)),
            "graph": json_value(graph),
            "criterion_coverage": [json_value(item) for item in state.criterion_coverage],
            "output_refs": [
                ref for task in self._active_tasks(graph) for ref in task.output_refs
            ],
        }
        call = self._component_call(
            state,
            component,
            kind="goal_verifier",
            idempotency_key=(
                f"root/{self._root_run_id}/goal/graph/{graph.revision}/"
                f"input/{compute_fingerprint(inputs)}"
            ),
            inputs=inputs,
            root_revision=root_revision,
        )
        if isinstance(call, TaskGraphStep):
            return call
        value, invocation = call
        outcome, result = verifier_outcome(component, value)
        record = VerificationRecord(
            record_id=new_ulid(),
            kind="goal",
            target_ref=f"graph:{graph.graph_id}:{graph.revision}",
            component_fingerprint=component.fingerprint,
            input_hash=invocation.input_hash,
            status=_verification_status(outcome),
            evidence_refs=tuple(result.get("evidence_refs") or ()),
            reason=cast(str | None, result.get("reason")),
        )
        base = replace(
            state,
            component_invocations=self._replace_component_invocations(state, invocation),
            verification_records=(*state.verification_records, record),
        )
        required_ids = set(graph.required_criteria)
        satisfied_ids = {
            item.criterion_id
            for item in state.criterion_coverage
            if item.status == "satisfied"
        }
        if outcome == "passed" and required_ids <= satisfied_ids:
            completed = self._commit(
                replace(base, graph=transition_graph(graph, "completed")),
                root_revision,
                "goal_verified",
                {"graph_id": graph.graph_id, "record_id": record.record_id},
            )
            return TaskGraphStep("completed", self.task_plan(), output=self._node_output(completed))
        if outcome == "ambiguous":
            self._commit(
                replace(base, graph=transition_graph(graph, "waiting")),
                root_revision,
                "goal_verification_waiting",
                {"graph_id": graph.graph_id, "record_id": record.record_id},
            )
            return TaskGraphStep(
                "waiting",
                self.task_plan(),
                pending=TaskGraphPending(
                    kind="goal",
                    request_id=new_ulid(),
                    reason=record.reason or "Goal verification is ambiguous",
                ),
            )
        reason = record.reason or (
            "Goal verifier passed without satisfying every required criterion"
            if outcome == "passed"
            else f"Goal verifier returned {outcome}"
        )
        return self._failed(base, root_revision, reason)

    def _failed(
        self,
        state: LongTaskState,
        root_revision: int,
        reason: str,
    ) -> TaskGraphStep:
        graph = state.graph
        if graph is not None and graph.status not in {"failed", "completed", "cancelled"}:
            graph = transition_graph(graph, "failed")
        self._commit(
            replace(state, graph=graph),
            root_revision,
            "graph_failed",
            {"reason": reason},
        )
        return TaskGraphStep("failed", self.task_plan(), error=reason)

    def _fail_attempt(
        self,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
        root_revision: int,
        reason: str,
    ) -> TaskGraphStep:
        failed_attempt = transition_attempt(attempt, "failed", failure=reason)
        failed_task = transition_task(
            _as_running_task(task) if task.status == "waiting" else task,
            "failed",
            active_attempt_id=None,
            failure=reason,
        )
        graph = transition_graph(
            self._replace_task(self._require_graph(state), failed_task), "failed"
        )
        self._commit(
            replace(
                state,
                graph=graph,
                attempts=self._replace_attempts(state, failed_attempt),
            ),
            root_revision,
            "task_failed",
            {"task_id": task.task_id, "attempt_id": attempt.attempt_id, "reason": reason},
        )
        return TaskGraphStep("failed", self.task_plan(), error=reason)

    def _commit(
        self,
        state: LongTaskState,
        root_revision: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> LongTaskState:
        if self.current_state is not None and root_revision <= self.current_state.revision:
            raise TaskGraphRuntimeError("root revision must advance monotonically")
        committed = replace(
            state,
            revision=root_revision,
            events=(
                *state.events,
                AuditEvent(new_ulid(), event_type, root_revision, payload),
            ),
        )
        self.current_state = committed
        return committed

    @staticmethod
    def _fail_prepared_invocations(
        state: LongTaskState,
        reason: str,
    ) -> LongTaskState:
        if not any(item.status == "prepared" for item in state.component_invocations):
            return state
        return replace(
            state,
            component_invocations=tuple(
                replace(item, status="failed", error=reason)
                if item.status == "prepared"
                else item
                for item in state.component_invocations
            ),
        )

    def _component_call(
        self,
        state: LongTaskState,
        component: PinnedComponent,
        *,
        kind: ComponentInvocationKind,
        idempotency_key: str,
        inputs: Mapping[str, Any],
        root_revision: int,
    ) -> tuple[Any, DurableComponentInvocation] | TaskGraphStep:
        matches = [
            item
            for item in state.component_invocations
            if item.idempotency_key == idempotency_key
        ]
        if len(matches) > 1:
            raise TaskGraphRuntimeError(
                f"duplicate component invocation key {idempotency_key!r}"
            )
        if not matches:
            prepared = prepare_component_invocation(
                component,
                kind=kind,
                idempotency_key=idempotency_key,
                inputs=inputs,
            )
            self._commit(
                replace(
                    state,
                    component_invocations=(*state.component_invocations, prepared),
                ),
                root_revision,
                "component_invocation_prepared",
                {
                    "component_id": component.id,
                    "idempotency_key": idempotency_key,
                },
            )
            return TaskGraphStep("running", self.task_plan())
        invocation = matches[0]
        if invocation.status != "prepared":
            raise TaskGraphRuntimeError(
                f"completed component invocation {idempotency_key!r} has no committed transition"
            )
        return invoke_component(component, invocation=invocation, inputs=inputs)

    def _component(self, field: str, component_id: str) -> PinnedComponent:
        node = self._contract_node()
        bindings = cast(Mapping[str, Any], node["bindings"])
        value = bindings[field]
        snapshot: Mapping[str, Any] | None
        if isinstance(value, Mapping):
            snapshot = value
        else:
            snapshots = [item for item in value if isinstance(item, Mapping)]
            snapshot = next(
                (item for item in snapshots if item.get("id") == component_id),
                None,
            )
            if snapshot is None:
                raise TaskGraphRuntimeError(
                    f"Task Graph contract has no pinned component {component_id!r}"
                )
        assert snapshot is not None
        return self._components.resolve_pinned(snapshot)

    def _contract_node(self) -> Mapping[str, Any]:
        task_graph = self._contract.snapshot.get("task_graph")
        if not isinstance(task_graph, Mapping):
            raise TaskGraphRuntimeError("execution contract has no Task Graph envelope")
        nodes = task_graph.get("nodes")
        if not isinstance(nodes, tuple | list):
            raise TaskGraphRuntimeError("execution contract Task Graph nodes are malformed")
        for item in nodes:
            if isinstance(item, Mapping) and item.get("node_id") == self._node_id:
                return item
        raise TaskGraphRuntimeError(
            f"execution contract has no Task Graph Node {self._node_id!r}"
        )

    def _validate_operation_only_graph(
        self,
        graph: TaskGraphRun,
        intent: IntentVersion,
    ) -> None:
        validate_graph(graph)
        allowed = set(self._config.operation_adapters)
        intent_hash = compute_fingerprint(json_value(intent))
        for task in self._active_tasks(graph):
            if task.kind != "executable":
                raise TaskGraphRuntimeError("Slice 1 does not support expandable Tasks")
            if (
                task.status != "pending"
                or task.active_attempt_id is not None
                or task.output_refs
                or task.failure is not None
            ):
                raise TaskGraphRuntimeError(
                    f"seed Task {task.task_id!r} must start as clean pending work"
                )
            if task.intent_version != intent.version or task.intent_binding_hash != intent_hash:
                raise TaskGraphRuntimeError(
                    f"seed Task {task.task_id!r} does not bind the confirmed Intent"
                )
            bindings = task.executor_policy.allowed_bindings
            if not bindings or any(item.mode != "operation" for item in bindings):
                raise TaskGraphRuntimeError("Slice 1 Task bindings must all be operation mode")
            if task.executor_policy.preferred_binding not in bindings:
                raise TaskGraphRuntimeError("preferred Task binding is not allowed")
            for binding in bindings:
                if binding.id not in allowed:
                    raise TaskGraphRuntimeError(
                        f"Task selects unpinned Operation adapter {binding.id!r}"
                    )
                adapter = self._adapters.resolve_node_adapter(binding.id)
                expected = compute_fingerprint(adapter.snapshot())
                if binding.component_fingerprint != expected:
                    raise TaskGraphRuntimeError(
                        f"Task binding fingerprint changed for adapter {binding.id!r}"
                    )

    def _resolve_task_adapter(self, task: TaskRun) -> OperationAdapter:
        binding = task.executor_policy.preferred_binding
        adapter = self._adapters.resolve_node_adapter(binding.id)
        expected = compute_fingerprint(adapter.snapshot())
        if binding.component_fingerprint != expected:
            raise TaskGraphRuntimeError(
                f"Task binding fingerprint changed for adapter {binding.id!r}"
            )
        return adapter

    def _resolve_attempt_adapter(
        self,
        task: TaskRun,
        attempt: TaskAttempt,
    ) -> OperationAdapter:
        if attempt.executor_binding != task.executor_policy.preferred_binding:
            raise TaskGraphRuntimeError("Attempt binding does not match persisted Task binding")
        return self._resolve_task_adapter(task)

    def _operation_arguments(
        self,
        state: LongTaskState,
        attempt: TaskAttempt,
    ) -> dict[str, Any]:
        payload = self._read_json_artifact(state, attempt.context_manifest_ref)
        arguments = payload.get("operation_arguments") if isinstance(payload, Mapping) else None
        if not isinstance(arguments, Mapping):
            raise TaskGraphRuntimeError("Context Manifest has no Operation arguments")
        return dict(arguments)

    def _validate_child_submission_binding(
        self,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
        submission: CandidateSubmission,
    ) -> None:
        if attempt.executor_binding.mode != "child_agent":
            raise TaskGraphRuntimeError("CandidateSubmission requires a child Agent Attempt")
        if attempt.status not in {"running", "waiting"} or task.status not in {
            "running",
            "waiting",
        }:
            raise TaskGraphRuntimeError("CandidateSubmission Attempt is not active")
        expected = (
            submission.task_ref == task.ref
            and submission.attempt_id == attempt.attempt_id
            and submission.child_run_id == attempt.child_run_id
            and submission.lease_epoch == attempt.lease.epoch
            and submission.lease_token == attempt.lease.token
            and submission.context_manifest_fingerprint
            == attempt.context_manifest_fingerprint
            and submission.completion_contract_hash == attempt.completion_contract_hash
            and submission.parent_execution_contract_fingerprint
            == attempt.parent_execution_contract_fingerprint
            == self._contract.fingerprint
        )
        if not expected:
            raise TaskGraphRuntimeError("CandidateSubmission binding or fencing is stale")
        if any(
            item.producer_attempt_id != attempt.attempt_id
            or item.producer_child_run_id != attempt.child_run_id
            for item in submission.artifact_candidates
        ):
            raise TaskGraphRuntimeError("artifact candidate producer provenance mismatch")
        if any(
            item.producer_attempt_id != attempt.attempt_id
            or item.producer_child_run_id != attempt.child_run_id
            for item in submission.evidence_claims
        ):
            raise TaskGraphRuntimeError("evidence claim producer provenance mismatch")
        if len(
            [
                item
                for item in state.receipts
                if item.submission_id == submission.submission_id
            ]
        ) > 1:
            raise TaskGraphRuntimeError("duplicate submission ID in root state")

    def _read_json_artifact(self, state: LongTaskState, uri: str) -> Any:
        artifact = next((item for item in state.artifacts if item.uri == uri), None)
        if artifact is None:
            raise TaskGraphRuntimeError(f"unknown Task Artifact {uri!r}")
        data = self._artifacts.read_verified(
            SealedBlobRef(
                uri=artifact.uri,
                content_hash=artifact.content_hash,
                size_bytes=artifact.size_bytes,
                mime_type=artifact.mime_type,
                trust_level=artifact.trust_level,
                metadata={},
            )
        )
        try:
            return json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskGraphRuntimeError(f"Task Artifact is not valid JSON: {exc}") from exc

    @staticmethod
    def _artifact_record(
        sealed: SealedBlobRef,
        *,
        kind: Literal["context_manifest", "candidate_output"],
        attempt_id: str,
    ) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=new_ulid(),
            kind=kind,
            uri=sealed.uri,
            content_hash=sealed.content_hash,
            size_bytes=sealed.size_bytes,
            mime_type=sealed.mime_type,
            trust_level=sealed.trust_level,
            producer_attempt_id=attempt_id,
        )

    def _pending_criterion(self, state: LongTaskState) -> IntentCriterion | None:
        graph = self._require_graph(state)
        active = self._active_tasks(graph)
        for criterion in self._confirmed_intent(state).success_criteria:
            coverage = self._coverage(state, criterion.id)
            if coverage.status != "unsatisfied":
                continue
            supporting = [task for task in active if criterion.id in task.supports]
            if supporting and all(task.status == "completed" for task in supporting):
                return criterion
        return None

    def _dispatchable_attempt(self, state: LongTaskState) -> TaskAttempt | None:
        for attempt in state.attempts:
            if attempt.status in {"created", "leased"}:
                return attempt
        return None

    def _dependency_outputs(self, state: LongTaskState, task: TaskRun) -> list[str]:
        graph = self._require_graph(state)
        refs: list[str] = []
        for dependency in task.depends_on:
            if dependency.kind != "task":
                raise TaskGraphRuntimeError("Slice 1 does not support Group dependencies")
            refs.extend(self._task_by_ref(graph, dependency).output_refs)
        return refs

    @staticmethod
    def _active_tasks(graph: TaskGraphRun) -> tuple[TaskRun, ...]:
        refs = {item.key for item in graph.active_task_refs}
        return tuple(item for item in graph.tasks if item.ref.key in refs)

    @staticmethod
    def _replace_task(graph: TaskGraphRun, task: TaskRun) -> TaskGraphRun:
        if task.ref not in graph.active_task_refs:
            raise TaskGraphRuntimeError(f"Task {task.task_id!r} is not active")
        return replace(
            graph,
            tasks=tuple(task if item.ref == task.ref else item for item in graph.tasks),
        )

    @staticmethod
    def _replace_attempts(
        state: LongTaskState,
        attempt: TaskAttempt,
    ) -> tuple[TaskAttempt, ...]:
        if not any(item.attempt_id == attempt.attempt_id for item in state.attempts):
            raise TaskGraphRuntimeError(f"unknown Attempt {attempt.attempt_id!r}")
        return tuple(
            attempt if item.attempt_id == attempt.attempt_id else item
            for item in state.attempts
        )

    @staticmethod
    def _replace_receipts(
        state: LongTaskState,
        receipt: CandidateReceipt,
    ) -> tuple[CandidateReceipt, ...]:
        return tuple(
            receipt if item.submission_id == receipt.submission_id else item
            for item in state.receipts
        )

    @staticmethod
    def _replace_component_invocations(
        state: LongTaskState,
        invocation: DurableComponentInvocation,
    ) -> tuple[DurableComponentInvocation, ...]:
        if not any(
            item.invocation_id == invocation.invocation_id
            for item in state.component_invocations
        ):
            raise TaskGraphRuntimeError(
                f"unknown component invocation {invocation.invocation_id!r}"
            )
        return tuple(
            invocation if item.invocation_id == invocation.invocation_id else item
            for item in state.component_invocations
        )

    @staticmethod
    def _replace_coverage(
        state: LongTaskState,
        coverage: CriterionCoverage,
    ) -> tuple[CriterionCoverage, ...]:
        return tuple(
            coverage if item.criterion_id == coverage.criterion_id else item
            for item in state.criterion_coverage
        )

    @staticmethod
    def _task_by_ref(graph: TaskGraphRun, ref: DependencyRef) -> TaskRun:
        if ref.kind != "task":
            raise TaskGraphRuntimeError("expected Task reference")
        for task in graph.tasks:
            if task.ref == ref:
                return task
        raise TaskGraphRuntimeError(f"unknown Task ref {ref.id!r}@{ref.revision}")

    @staticmethod
    def _attempt(state: LongTaskState, attempt_id: str) -> TaskAttempt:
        for attempt in state.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        raise TaskGraphRuntimeError(f"unknown Attempt {attempt_id!r}")

    @staticmethod
    def _coverage(state: LongTaskState, criterion_id: str) -> CriterionCoverage:
        for coverage in state.criterion_coverage:
            if coverage.criterion_id == criterion_id:
                return coverage
        raise TaskGraphRuntimeError(f"unknown criterion {criterion_id!r}")

    @staticmethod
    def _require_graph(state: LongTaskState) -> TaskGraphRun:
        if state.graph is None:
            raise TaskGraphRuntimeError("Task Graph has not been seeded")
        return state.graph

    def _require_state(self) -> LongTaskState:
        if self.current_state is None:
            raise TaskGraphRuntimeError("Task Graph state is not initialized")
        return self.current_state

    @staticmethod
    def _confirmed_intent(state: LongTaskState) -> IntentVersion:
        matches = [item for item in state.intents if item.status == "confirmed"]
        if len(matches) != 1:
            raise TaskGraphRuntimeError("Task Graph requires exactly one confirmed Intent")
        return matches[0]

    def _node_output(self, state: LongTaskState) -> Mapping[str, Any]:
        graph = self._require_graph(state)
        return {
            "goal_verified": graph.status == "completed",
            "intent_id": graph.intent_id,
            "intent_version": graph.intent_version,
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "criterion_coverage": [json_value(item) for item in state.criterion_coverage],
            "task_outputs": {
                task.task_id: list(task.output_refs) for task in self._active_tasks(graph)
            },
        }


def _parse_intent(raw: Any) -> IntentVersion:
    if isinstance(raw, IntentVersion):
        return raw
    if not isinstance(raw, Mapping):
        raise TaskGraphRuntimeError("Task Graph input must contain an Intent mapping")
    criteria_raw = raw.get("success_criteria")
    if not isinstance(criteria_raw, tuple | list) or not criteria_raw:
        raise TaskGraphRuntimeError("Intent success_criteria must be a non-empty array")
    criteria: list[IntentCriterion] = []
    for item in criteria_raw:
        if not isinstance(item, Mapping):
            raise TaskGraphRuntimeError("Intent criteria must be mappings")
        criteria.append(
            IntentCriterion(
                id=str(item.get("id") or ""),
                description=str(item.get("description") or ""),
                required=bool(item.get("required", True)),
                verification_mode=str(item.get("verification_mode") or "verifier"),
                validator_id=(
                    str(item["validator_id"]) if item.get("validator_id") is not None else None
                ),
            )
        )
    intent = IntentVersion(
        intent_id=str(raw.get("intent_id") or ""),
        version=int(raw.get("version") or 0),
        status=cast(Any, raw.get("status")),
        goal=str(raw.get("goal") or ""),
        desired_outcome=str(raw.get("desired_outcome") or ""),
        success_criteria=tuple(criteria),
        constraints=tuple(str(item) for item in raw.get("constraints") or ()),
        non_goals=tuple(str(item) for item in raw.get("non_goals") or ()),
        assumptions=tuple(str(item) for item in raw.get("assumptions") or ()),
        authority_hash=str(raw.get("authority_hash") or ""),
    )
    if not intent.intent_id or intent.version < 1 or not intent.goal or not intent.desired_outcome:
        raise TaskGraphRuntimeError("Intent identity, version, goal, and desired outcome are required")
    if any(not item.id or not item.description for item in intent.success_criteria):
        raise TaskGraphRuntimeError("Intent criteria require id and description")
    return intent


def _waiting_details(output: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(output, Mapping):
        raise TaskGraphRuntimeError("waiting Operation did not return a reviewed proposal")
    proposal = output.get("proposal")
    decision = output.get("decision")
    if not isinstance(proposal, Mapping) or not isinstance(decision, Mapping):
        raise TaskGraphRuntimeError("waiting Operation review identity is incomplete")
    return proposal, decision


def _verification_status(outcome: str) -> Any:
    return {
        "passed": "passed",
        "repairable": "repairable",
        "repairable_gap": "repairable",
        "needs_replan": "needs_replan",
        "ambiguous": "ambiguous",
        "impossible": "terminal",
        "terminal": "terminal",
    }[outcome]


def _as_running_attempt(attempt: TaskAttempt) -> TaskAttempt:
    return attempt if attempt.status == "running" else transition_attempt(attempt, "running")


def _as_running_task(task: TaskRun) -> TaskRun:
    return task if task.status == "running" else transition_task(task, "running")


__all__ = [
    "OperationTaskGraphRuntime",
    "TaskGraphOperationBridge",
    "TaskGraphOutcome",
    "TaskGraphPending",
    "TaskGraphRuntimeError",
    "TaskGraphStep",
]
