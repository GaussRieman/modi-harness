"""Deterministic Operation-only Task Graph parent runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

from .._utils import canonical_json, compute_fingerprint, new_ulid
from ..workflow.components import PinnedComponent, PinnedComponentRegistry
from ..workflow.contract import ExecutionContract, OperationAdapter, OperationAdapterRegistry
from ..workflow.definition import validate_instance
from ..workflow.types import TaskGraphNodeConfig
from ..workspace import SealedBlobRef, TaskArtifactStore
from .context import ContextManifest, DependencyContext, ManifestAuthority, ManifestBudgets
from .executors import (
    ExecutorContractError,
    PendingDecisionError,
    consume_pending_goal_decision,
    consume_pending_task_decision,
    parse_human_task_contract,
    validate_human_prompt,
    validate_human_response,
)
from .graph import apply_graph_patch, ready_tasks, validate_graph
from .groups import GroupDecision, commit_any_success_winner, evaluate_group, replace_group
from .intent import (
    IntentConfirmation,
    IntentPatch,
    IntentPatchChange,
    IntentRebasePlan,
    RebaseReuseProof,
    intent_fingerprint,
    plan_intent_rebase,
)
from .planning import (
    PlanningTrigger,
    assess_planner_patch,
    build_parent_context_projection,
    decide_planning_budget,
    normalize_discovered_work,
    prepare_planner_invocation,
)
from .resources import canonical_resource_paths
from .scheduler import SchedulerPolicy, schedule_ready_tasks
from .submission import CandidateSubmission
from .transitions import transition_attempt, transition_graph, transition_task
from .types import (
    ArtifactRecord,
    AuditEvent,
    CancellationRequest,
    CandidateReceipt,
    ComponentInvocationKind,
    CriterionCoverage,
    DependencyRef,
    DurableComponentInvocation,
    GraphLimits,
    GraphPatch,
    GroupRun,
    IntentCriterion,
    IntentVersion,
    LeaseRecord,
    LongTaskState,
    PendingGoalDecision,
    PendingTaskDecision,
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


class TaskGraphChildBridge(Protocol):
    def prepare_child(
        self,
        *,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
    ) -> tuple[int, str]: ...

    def advance_child(self, attempt: TaskAttempt) -> CandidateSubmission | None: ...

    def cancel_child(
        self,
        attempt: TaskAttempt,
        *,
        reason: str,
    ) -> CandidateSubmission | None: ...

@dataclass(frozen=True, slots=True)
class TaskGraphPending:
    kind: Literal["operation", "task", "goal"]
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
        child_bridge: TaskGraphChildBridge | None = None,
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
        self._child_bridge = child_bridge
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
            pending_rebase = self._pending_intent_rebase(state)
            if pending_rebase is not None:
                return self._advance_intent_rebase(
                    state,
                    pending_rebase,
                    root_revision,
                )
            if graph.status == "waiting":
                raise TaskGraphRuntimeError("waiting Task Graph requires exact resume payload")
            if graph.status == "verifying":
                final_criterion = self._pending_criterion(state)
                if final_criterion is not None:
                    return self._verify_criterion(
                        state,
                        final_criterion,
                        root_revision,
                        final=True,
                    )
                required_ids = set(graph.required_criteria)
                blocked_required = sorted(
                    item.criterion_id
                    for item in state.criterion_coverage
                    if item.criterion_id in required_ids and item.status != "satisfied"
                )
                if blocked_required:
                    return self._failed(
                        state,
                        root_revision,
                        "final required criterion failed: "
                        + ", ".join(blocked_required),
                    )
                return self._verify_goal(state, root_revision)

            receipt = next((item for item in state.receipts if item.status == "received"), None)
            if receipt is not None:
                return self._verify_candidate(state, receipt, root_revision)
            planning = self._pending_planning(state)
            if planning is not None:
                trigger, repair_attempt, discovered_work = planning
                return self._advance_planning(
                    state,
                    trigger,
                    repair_attempt,
                    discovered_work,
                    root_revision,
                )
            criterion = self._pending_criterion(state)
            if criterion is not None:
                return self._verify_criterion(state, criterion, root_revision)
            group_step = self._advance_group(state, root_revision)
            if group_step is not None:
                return group_step
            cancellation = self._pending_cancellation(state)
            if cancellation is not None:
                return self._process_cancellation(state, cancellation, root_revision)
            active_attempt = self._dispatchable_attempt(state)
            if active_attempt is not None:
                if active_attempt.status == "created":
                    return self._lease_attempt(state, active_attempt, root_revision)
                return self._dispatch_attempt(state, active_attempt, root_revision)
            ready = ready_tasks(graph)
            if ready:
                batch = schedule_ready_tasks(
                    graph,
                    state.attempts,
                    SchedulerPolicy(
                        graph.limits.max_concurrency,
                        dict(graph.limits.template_concurrency_limits),
                    ),
                    resource_paths_by_task={
                        task.ref: task.resource_keys for task in ready
                    },
                )
                if batch.selected:
                    return self._prepare_attempt(state, batch.selected[0], root_revision)
            active_children = self._active_child_attempts(state)
            if active_children:
                return self._advance_child_attempts(state, active_children, root_revision)
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
        except (ExecutorContractError, PendingDecisionError) as exc:
            state = self._require_state()
            graph = self._require_graph(state)
            if graph.status == "waiting":
                return TaskGraphStep(
                    "waiting",
                    self.task_plan(),
                    pending=replace(pending, reason=str(exc)),
                )
            return TaskGraphStep("running", self.task_plan(), error=str(exc))
        except Exception as exc:
            return self._failed(
                self._fail_prepared_invocations(self._require_state(), str(exc)),
                root_revision,
                str(exc),
            )

    def request_intent_rebase(
        self,
        *,
        new_intent: IntentVersion,
        patch: IntentPatch,
        confirmation: IntentConfirmation,
        request_id: str,
        root_revision: int,
        _state: LongTaskState | None = None,
    ) -> TaskGraphStep:
        """Persist one human-confirmed material Intent change before verification."""

        state = _state or self._require_state()
        graph = self._require_graph(state)
        if graph.status != "waiting":
            raise TaskGraphRuntimeError(
                "material Intent rebase requires a waiting human judgment"
            )
        if not request_id.strip():
            raise TaskGraphRuntimeError("Intent rebase request_id must be non-empty")
        if new_intent.confirmation_proof_id != request_id:
            raise TaskGraphRuntimeError(
                "confirmed Intent rebase must bind the exact human judgment request"
            )
        if self._pending_intent_rebase(state) is not None:
            raise TaskGraphRuntimeError("another Intent rebase is already pending")
        plan_intent_rebase(
            state,
            new_intent=new_intent,
            patch=patch,
            confirmation=confirmation,
        )
        self._commit(
            state,
            root_revision,
            "intent_rebase_requested",
            {
                "request_id": request_id,
                "graph_revision": graph.revision,
                "new_intent": json_value(new_intent),
                "patch": patch.snapshot(),
                "confirmation": confirmation.snapshot(),
            },
        )
        return TaskGraphStep("running", self.task_plan())

    def _resume_pending(
        self,
        *,
        pending: TaskGraphPending,
        payload: Mapping[str, Any],
        root_revision: int,
    ) -> TaskGraphStep:
        state = self._require_state()
        graph = self._require_graph(state)
        if pending.kind == "task":
            return self._resume_human_task(
                state,
                pending=pending,
                payload=payload,
                root_revision=root_revision,
            )
        if pending.kind == "goal" and any(
            item.request_id == pending.request_id
            for item in state.pending_goal_decisions
        ):
            return self._resume_goal_decision(
                state,
                pending=pending,
                payload=payload,
                root_revision=root_revision,
            )
        if graph.status != "waiting":
            raise TaskGraphRuntimeError("Task Graph is not waiting")
        decision = str(payload.get("kind") or payload.get("decision") or "")
        if pending.kind == "goal":
            updates = payload.get("intent_updates")
            if isinstance(updates, Mapping) and updates:
                try:
                    if decision not in {
                        "approve",
                        "revise",
                        "redirect",
                        "constrain",
                        "clarify",
                    }:
                        raise TaskGraphRuntimeError(
                            "Intent rebase requires an affirmative human judgment"
                        )
                    raw_intent = updates.get("new_intent", updates.get("intent"))
                    new_intent = replace(
                        _parse_intent(raw_intent),
                        status="confirmed",
                        confirmation_proof_id=pending.request_id,
                    )
                    patch = _parse_intent_patch(updates.get("patch"))
                    if not patch.patch_id:
                        patch = replace(patch, patch_id=pending.request_id)
                    confirmation = IntentConfirmation(
                        intent_id=new_intent.intent_id,
                        intent_version=new_intent.version,
                        intent_fingerprint=intent_fingerprint(new_intent),
                        confirmed_by=f"human:{decision or 'judgment'}",
                    )
                    return self.request_intent_rebase(
                        new_intent=new_intent,
                        patch=patch,
                        confirmation=confirmation,
                        request_id=pending.request_id,
                        root_revision=root_revision,
                    )
                except (TaskGraphRuntimeError, ValueError) as exc:
                    return TaskGraphStep(
                        "waiting",
                        self.task_plan(),
                        pending=TaskGraphPending(
                            kind="goal",
                            request_id=new_ulid(),
                            reason=str(exc),
                        ),
                    )
            return self._failed(
                state,
                root_revision,
                "ambiguous Goal requires a confirmed structured Intent rebase",
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
        cancelled_loser = self._is_cancelled_group_loser(state, task, attempt)
        fenced_cancellation = any(
            item.attempt_id == attempt.attempt_id
            and item.status == "requested"
            and attempt.status == "cancelled"
            and attempt.lease.retiring
            for item in state.cancellation_requests
        )
        stale_submission = cancelled_loser or fenced_cancellation
        self._validate_child_submission_binding(
            state,
            task,
            attempt,
            submission,
            allow_cancelled_loser=stale_submission,
        )
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
            status="stale" if stale_submission else "received",
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
            decision="stale" if stale_submission else "pending",
            reason=(
                (
                    "submission arrived after another any_success candidate won"
                    if cancelled_loser
                    else "submission arrived after its Attempt was fenced"
                )
                if stale_submission
                else None
            ),
        )
        if stale_submission:
            committed = self._commit(
                replace(state, receipts=(*state.receipts, receipt)),
                root_revision,
                "stale_candidate_received",
                {
                    "task_id": task.task_id,
                    "attempt_id": attempt.attempt_id,
                    "submission_id": submission.submission_id,
                },
            )
            return next(
                item
                for item in committed.receipts
                if item.submission_id == submission.submission_id
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
            attempts = [item for item in state.attempts if item.task_ref == task.ref]
            attempt = attempts[-1] if attempts else None
            status = {
                "pending": "pending",
                "running": "in_progress",
                "verifying": "in_progress",
                "waiting": (
                    "waiting_human"
                    if attempt is not None and attempt.executor_binding.mode == "human"
                    else "blocked"
                ),
                "completed": "completed",
                "failed": "blocked",
                "cancelled": "cancelled",
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
                    "executor_mode": (
                        attempt.executor_binding.mode if attempt is not None else None
                    ),
                    "attempt_status": attempt.status if attempt is not None else None,
                    "retiring": bool(attempt is not None and attempt.lease.retiring),
                    "child": (
                        {
                            "run_id": attempt.child_run_id,
                            "status": attempt.child_observation_status,
                            "revision": attempt.child_observation_revision,
                        }
                        if attempt is not None and attempt.child_run_id is not None
                        else None
                    ),
                }
            )
        current_human_request: dict[str, Any] | None = None
        pending_task = next(
            (item for item in state.pending_task_decisions if item.status == "pending"),
            None,
        )
        pending_goal = next(
            (item for item in state.pending_goal_decisions if item.status == "pending"),
            None,
        )
        if pending_task is not None:
            current_human_request = {
                "request_id": pending_task.request_id,
                "kind": pending_task.decision_class,
                "prompt": str(pending_task.prompt.get("title") or "human Task response required"),
            }
        elif pending_goal is not None:
            current_human_request = {
                "request_id": pending_goal.request_id,
                "kind": pending_goal.decision_class,
                "prompt": str(pending_goal.prompt.get("reason") or "Goal judgment required"),
            }
        return {
            "kind": "task_graph",
            "graph_id": state.graph.graph_id,
            "version": state.graph.revision,
            "graph_status": state.graph.status,
            "items": items,
            "current_task_id": current_task_id,
            "current_action": current_action,
            "last_activity": state.events[-1].event_type if state.events else None,
            "current_human_request": current_human_request,
        }

    def _initialize(self, inputs: Mapping[str, Any], root_revision: int) -> TaskGraphStep:
        raw_intent = inputs.get("intent", inputs)
        intent = _parse_intent(raw_intent)
        proof = inputs.get("intent_confirmation_proof")
        if not isinstance(proof, Mapping):
            raise TaskGraphRuntimeError(
                "Task Graph execution requires a runtime-owned Intent confirmation proof"
            )
        workflow_snapshot = self._contract.snapshot.get("workflow")
        workflow_id = (
            workflow_snapshot.get("id")
            if isinstance(workflow_snapshot, Mapping)
            else None
        )
        approved_revision = proof.get("approved_revision")
        if (
            proof.get("run_id") != self._root_run_id
            or proof.get("workflow_id") != workflow_id
            or proof.get("execution_contract_fingerprint") != self._contract.fingerprint
            or proof.get("source") not in {"user_input", "node_review"}
            or not isinstance(proof.get("proof_id"), str)
            or not str(proof["proof_id"]).strip()
            or not isinstance(proof.get("input_ref"), str)
            or not str(proof["input_ref"]).strip()
            or not isinstance(approved_revision, int)
            or isinstance(approved_revision, bool)
            or approved_revision < 0
            or approved_revision > root_revision
            or proof.get("confirmed_intent_hash")
            != compute_fingerprint(json_value(raw_intent))
        ):
            raise TaskGraphRuntimeError(
                "Intent confirmation proof does not match this run and exact Intent"
            )
        if proof.get("source") == "user_input" and intent.status != "confirmed":
            raise TaskGraphRuntimeError(
                "direct user input must carry a confirmed Intent"
            )
        if proof.get("source") == "node_review" and (
            not isinstance(proof.get("source_node_id"), str)
            or not isinstance(proof.get("source_node_attempt"), int)
            or not isinstance(proof.get("request_id"), str)
        ):
            raise TaskGraphRuntimeError(
                "reviewed Intent proof has incomplete Workflow review identity"
            )
        intent = replace(
            intent,
            status="confirmed",
            confirmation_proof_id=str(proof["proof_id"]),
        )
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
                    {
                        "intent_id": intent.intent_id,
                        "intent_version": intent.version,
                        "confirmation_proof_id": intent.confirmation_proof_id,
                    },
                ),
            ),
        )
        self.current_state = state
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
                self._config.limits.template_concurrency_limits,
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
            "allowed_child_templates": [
                {
                    "id": template["id"],
                    "fingerprint": template["fingerprint"],
                }
                for template in self._contract_child_templates()
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
        self._validate_task_graph(graph, intent)
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

    def _advance_planning(
        self,
        state: LongTaskState,
        trigger: PlanningTrigger,
        repair_attempt: int,
        discovered_work: tuple[Mapping[str, Any], ...],
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        budget = decide_planning_budget(
            graph,
            trigger,
            repair_attempt=repair_attempt,
            max_repair_attempts=2,
        )
        if not budget.allowed:
            return self._failed(
                state,
                root_revision,
                budget.reason or "Planner budget exhausted",
            )
        planner = self._component("planner", self._config.planner)
        context = build_parent_context_projection(
            state,
            trigger,
            discovered_work=discovered_work,
            recent_patches=(
                event.payload
                for event in state.events
                if event.event_type == "graph_patch_applied"
            ),
            authority_boundaries={
                "operation_adapters": list(self._config.operation_adapters),
                "child_templates": list(self._config.child_templates),
                "parent_inline_components": list(
                    self._config.parent_inline_components
                ),
                "human_task_contracts": list(self._config.human_task_contracts),
            },
        )
        inputs = {"context": json_value(context), "trigger": trigger.snapshot()}
        prepared = prepare_planner_invocation(
            planner,
            root_run_id=self._root_run_id,
            graph=graph,
            trigger=trigger,
            context=context,
            repair_attempt=repair_attempt,
        )
        matches = tuple(
            item
            for item in state.component_invocations
            if item.idempotency_key == prepared.idempotency_key
        )
        if len(matches) > 1:
            raise TaskGraphRuntimeError(
                f"duplicate Planner invocation key {prepared.idempotency_key!r}"
            )
        if not matches:
            self._commit(
                replace(
                    state,
                    component_invocations=(*state.component_invocations, prepared),
                ),
                root_revision,
                "planner_invocation_prepared",
                {
                    "graph_revision": graph.revision,
                    "trigger": trigger.kind,
                    "repair_attempt": repair_attempt,
                },
            )
            return TaskGraphStep("running", self.task_plan())
        invocation = matches[0]
        if invocation.status != "prepared":
            raise TaskGraphRuntimeError(
                "completed Planner invocation has no committed graph transition"
            )
        output, completed_invocation = invoke_component(
            planner,
            invocation=invocation,
            inputs=inputs,
        )
        proposal = output.get("patch") if isinstance(output, Mapping) else output
        assessment = assess_planner_patch(
            graph,
            trigger,
            proposal,
            repair_attempt=repair_attempt,
            max_repair_attempts=2,
        )
        if assessment.accepted and assessment.graph is not None:
            try:
                self._validate_task_graph(
                    assessment.graph,
                    self._confirmed_intent(state),
                    require_clean_seed=False,
                    verification_records=state.verification_records,
                )
                if not isinstance(proposal, GraphPatch):
                    raise TaskGraphRuntimeError("Planner proposal lost its typed GraphPatch")
                self._validate_planning_trigger_resolution(
                    graph,
                    assessment.graph,
                    trigger,
                    proposal,
                )
            except (TaskGraphRuntimeError, ValueError) as exc:
                assessment = replace(
                    assessment,
                    accepted=False,
                    graph=None,
                    feedback=str(exc),
                    retryable=budget.may_repair,
                )
        trigger_key = self._planning_trigger_key(trigger, include_details=False)
        if not assessment.accepted or assessment.graph is None:
            self._commit(
                replace(
                    state,
                    component_invocations=self._replace_component_invocations(
                        state,
                        completed_invocation,
                    ),
                ),
                root_revision,
                "planner_patch_rejected",
                {
                    "graph_revision": graph.revision,
                    "trigger": trigger.snapshot(),
                    "trigger_key": trigger_key,
                    "repair_attempt": repair_attempt,
                    "feedback": assessment.feedback,
                    "retryable": assessment.retryable,
                    "needs_fresh_context": assessment.needs_fresh_context,
                },
            )
            return TaskGraphStep("running", self.task_plan())
        updated_graph = assessment.graph
        self._commit(
            replace(
                state,
                graph=updated_graph,
                component_invocations=self._replace_component_invocations(
                    state,
                    completed_invocation,
                ),
            ),
            root_revision,
            "graph_patch_applied",
            {
                "graph_id": graph.graph_id,
                "base_revision": graph.revision,
                "graph_revision": updated_graph.revision,
                "trigger": trigger.kind,
                "trigger_key": trigger_key,
                "repair_attempt": repair_attempt,
            },
        )
        return TaskGraphStep("running", self.task_plan())

    def _prepare_attempt(
        self,
        state: LongTaskState,
        task: TaskRun,
        root_revision: int,
    ) -> TaskGraphStep:
        binding = task.executor_policy.preferred_binding
        if binding.mode == "child_agent":
            return self._prepare_child_attempt(state, task, root_revision)
        if binding.mode in {"parent_inline", "human"}:
            return self._prepare_component_attempt(state, task, root_revision)
        if binding.mode != "operation":
            raise TaskGraphRuntimeError(f"unsupported Task binding mode {binding.mode!r}")
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
                resource_keys=canonical_resource_paths(task.resource_keys),
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
                resource_locks=(
                    *state.resource_locks,
                    *self._resource_locks(attempt),
                ),
            ),
            root_revision,
            "attempt_prepared",
            {"task_id": task.task_id, "attempt_id": attempt_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _prepare_component_attempt(
        self,
        state: LongTaskState,
        task: TaskRun,
        root_revision: int,
    ) -> TaskGraphStep:
        if self._parent_node_attempt is None:
            raise TaskGraphRuntimeError("parent Node attempt is unavailable")
        binding = task.executor_policy.preferred_binding
        field = (
            "parent_inline_components"
            if binding.mode == "parent_inline"
            else "human_task_contracts"
        )
        self._resolve_task_component(binding, field)
        attempt_id = new_ulid()
        manifest = {
            "intent": json_value(self._confirmed_intent(state)),
            "task": json_value(task),
            "dependency_outputs": self._dependency_outputs(state, task),
        }
        sealed = self._artifacts.seal(
            self._artifacts.stage(
                attempt_id,
                canonical_json(manifest),
                mime_type="application/json",
                trust="trusted",
                metadata={"kind": "context_manifest", "task_id": task.task_id},
            )
        )
        artifact = self._artifact_record(
            sealed,
            kind="context_manifest",
            attempt_id=attempt_id,
        )
        dispatch_key = compute_fingerprint(
            {
                "root_run_id": self._root_run_id,
                "task_ref": json_value(task.ref),
                "attempt_id": attempt_id,
                "binding": json_value(binding),
                "context_manifest_fingerprint": sealed.content_hash,
            }
        )
        attempt = TaskAttempt(
            attempt_id=attempt_id,
            task_ref=task.ref,
            status="created",
            executor_binding=binding,
            context_manifest_ref=sealed.uri,
            completion_contract_hash=compute_fingerprint(
                json_value(task.completion_contract)
            ),
            dispatch_key=dispatch_key,
            lease=LeaseRecord(
                owner_id=self._root_run_id,
                epoch=1,
                token=compute_fingerprint({"attempt_id": attempt_id, "epoch": 1}),
                expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                resource_keys=canonical_resource_paths(task.resource_keys),
            ),
            parent_execution_contract_fingerprint=self._contract.fingerprint,
            parent_node_id=self._node_id,
            parent_node_attempt=self._parent_node_attempt,
            context_manifest_fingerprint=sealed.content_hash,
        )
        running = transition_task(task, "running", active_attempt_id=attempt_id)
        self._commit(
            replace(
                state,
                graph=self._replace_task(self._require_graph(state), running),
                attempts=(*state.attempts, attempt),
                artifacts=(*state.artifacts, artifact),
                resource_locks=(*state.resource_locks, *self._resource_locks(attempt)),
            ),
            root_revision,
            "attempt_prepared",
            {
                "task_id": task.task_id,
                "attempt_id": attempt_id,
                "executor_mode": binding.mode,
            },
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

    def _prepare_child_attempt(
        self,
        state: LongTaskState,
        task: TaskRun,
        root_revision: int,
    ) -> TaskGraphStep:
        bridge = self._child_bridge
        if bridge is None:
            raise TaskGraphRuntimeError("child Agent Task has no child runtime bridge")
        if self._parent_node_attempt is None:
            raise TaskGraphRuntimeError("parent Node attempt is unavailable")
        binding = task.executor_policy.preferred_binding
        template = self._child_template(binding)
        attempt_id = new_ulid()
        child_run_id = new_ulid()
        child_workflow = cast(Mapping[str, Any], template["definition"])["child_workflow"]
        child_contract = cast(Mapping[str, Any], template["definition"])[
            "child_execution_contract"
        ]
        manifest = self._child_context_manifest(
            state=state,
            task=task,
            attempt_id=attempt_id,
            child_run_id=child_run_id,
            template=template,
        )
        sealed = self._artifacts.seal(
            self._artifacts.stage(
                attempt_id,
                canonical_json(manifest.snapshot()),
                mime_type="application/json",
                trust="trusted",
                metadata={"kind": "context_manifest", "task_id": task.task_id},
            )
        )
        artifact = self._artifact_record(
            sealed,
            kind="context_manifest",
            attempt_id=attempt_id,
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
                resource_keys=canonical_resource_paths(task.resource_keys),
            ),
            parent_execution_contract_fingerprint=self._contract.fingerprint,
            child_run_id=child_run_id,
            child_workflow_fingerprint=str(child_workflow["fingerprint"]),
            child_execution_contract_fingerprint=str(child_contract["fingerprint"]),
            child_checkpoint_ns=(
                f"roots/{self._root_run_id}/nodes/{self._node_id}/"
                f"{self._parent_node_attempt}/attempts/{attempt_id}/"
                f"children/{child_run_id}/workflow"
            ),
            parent_node_id=self._node_id,
            parent_node_attempt=self._parent_node_attempt,
            context_manifest_fingerprint=manifest.fingerprint,
            child_template_fingerprint=str(template["fingerprint"]),
        )
        running = transition_task(task, "running", active_attempt_id=attempt_id)
        self._commit(
            replace(
                state,
                graph=self._replace_task(self._require_graph(state), running),
                attempts=(*state.attempts, attempt),
                artifacts=(*state.artifacts, artifact),
                resource_locks=(
                    *state.resource_locks,
                    *self._resource_locks(attempt),
                ),
            ),
            root_revision,
            "attempt_prepared",
            {"task_id": task.task_id, "attempt_id": attempt_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _child_context_manifest(
        self,
        *,
        state: LongTaskState,
        task: TaskRun,
        attempt_id: str,
        child_run_id: str,
        template: Mapping[str, Any],
    ) -> ContextManifest:
        definition = cast(Mapping[str, Any], template["definition"])
        template_ref = cast(Mapping[str, Any], definition["template"])
        authority = cast(Mapping[str, Any], definition["authority"])
        workflow = cast(Mapping[str, Any], definition["child_workflow"])
        child_contract = cast(Mapping[str, Any], definition["child_execution_contract"])
        intent = self._confirmed_intent(state)
        criteria = {item.id: item for item in intent.success_criteria}
        dependencies: list[DependencyContext] = []
        graph = self._require_graph(state)
        for ref in task.depends_on:
            dependency = self._task_by_ref(graph, ref)
            dependencies.append(
                DependencyContext(
                    ref=ref,
                    result_summary=f"Completed Task {dependency.task_id}",
                    artifact_refs=dependency.output_refs,
                )
            )
        static_permission = cast(Mapping[str, Any], authority["permission_profile"])
        limits = cast(Mapping[str, Any], template_ref["limits"])
        effective_adapters = tuple(authority.get("workflow_adapters", ()))
        effective_capabilities = tuple(authority.get("effective_capability_ceiling", ()))
        return ContextManifest(
            context_id=f"context/{attempt_id}",
            root_run_id=self._root_run_id,
            parent_run_id=self._root_run_id,
            parent_node_id=self._node_id,
            parent_node_attempt=cast(int, self._parent_node_attempt),
            task_attempt_id=attempt_id,
            child_run_id=child_run_id,
            template_id=str(template["id"]),
            template_fingerprint=str(template["fingerprint"]),
            child_workflow_fingerprint=str(workflow["fingerprint"]),
            child_execution_contract_fingerprint=str(child_contract["fingerprint"]),
            intent={
                "intent_id": intent.intent_id,
                "version": intent.version,
                "binding_hash": task.intent_binding_hash,
                "goal": intent.goal,
                "desired_outcome": intent.desired_outcome,
                "relevant_criteria": [
                    json_value(criteria[item]) for item in sorted(task.supports)
                ],
            },
            task={
                "ref": json_value(task.ref),
                "goal": task.goal,
                "completion_contract": json_value(task.completion_contract),
                "constraints": list(intent.constraints),
                "non_goals": list(intent.non_goals),
                "assumptions": list(intent.assumptions),
            },
            dependencies=tuple(dependencies),
            inputs={"artifact_refs": [], "evidence_refs": [], "memory_refs": []},
            authority=ManifestAuthority(
                adapters=effective_adapters,
                capabilities=effective_capabilities,
                readable_scopes=(),
                writable_scopes=(f"workspace://{child_run_id}",),
                permission_profile=cast(
                    Mapping[str, Any], static_permission["static_intersection"]
                ),
            ),
            budgets=ManifestBudgets(
                max_steps=int(limits["max_steps"]),
                timeout_seconds=int(limits["timeout_seconds"]),
            ),
        )

    def _dispatch_attempt(
        self,
        state: LongTaskState,
        attempt: TaskAttempt,
        root_revision: int,
    ) -> TaskGraphStep:
        if attempt.executor_binding.mode == "child_agent":
            return self._dispatch_child_attempt(state, attempt, root_revision)
        if attempt.executor_binding.mode == "parent_inline":
            return self._dispatch_parent_inline_attempt(state, attempt, root_revision)
        if attempt.executor_binding.mode == "human":
            return self._dispatch_human_attempt(state, attempt, root_revision)
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

    def _dispatch_parent_inline_attempt(
        self,
        state: LongTaskState,
        attempt: TaskAttempt,
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        task = self._task_by_ref(graph, attempt.task_ref)
        component = self._resolve_task_component(
            attempt.executor_binding,
            "parent_inline_components",
        )
        inputs = self._read_json_artifact(state, attempt.context_manifest_ref)
        call = self._component_call(
            state,
            component,
            kind="parent_inline",
            idempotency_key=(
                f"root/{self._root_run_id}/task/{task.task_id}/"
                f"revision/{task.task_revision}/attempt/{attempt.attempt_id}/"
                f"parent-inline/{component.id}/input/{compute_fingerprint(inputs)}"
            ),
            inputs=inputs,
            root_revision=root_revision,
        )
        if isinstance(call, TaskGraphStep):
            return call
        output, invocation = call
        return self._submit_internal_candidate(
            state,
            task,
            attempt,
            output,
            root_revision,
            invocation=invocation,
        )

    def _dispatch_human_attempt(
        self,
        state: LongTaskState,
        attempt: TaskAttempt,
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        task = self._task_by_ref(graph, attempt.task_ref)
        component = self._resolve_task_component(
            attempt.executor_binding,
            "human_task_contracts",
        )
        contract = parse_human_task_contract(component)
        raw_prompt = component.configuration.get(
            "prompt",
            {"task_id": task.task_id, "goal": task.goal},
        )
        if not isinstance(raw_prompt, Mapping):
            raise TaskGraphRuntimeError("human Task contract prompt must be a mapping")
        prompt = validate_human_prompt(contract, raw_prompt)
        request_id = new_ulid()
        input_hash = compute_fingerprint(
            {
                "context_manifest_ref": attempt.context_manifest_ref,
                "prompt": json_value(prompt),
                "contract_fingerprint": contract.fingerprint,
            }
        )
        decision = PendingTaskDecision(
            request_id=request_id,
            task_ref=task.ref,
            attempt_id=attempt.attempt_id,
            graph_revision=graph.revision,
            contract_id=contract.id,
            contract_fingerprint=contract.fingerprint,
            input_hash=input_hash,
            expected_root_revision=root_revision,
            decision_class=cast(Any, contract.decision_class),
            allowed_decisions=contract.allowed_decisions,
            prompt=prompt,
        )
        waiting_attempt = transition_attempt(
            _as_running_attempt(attempt),
            "waiting",
        )
        waiting_task = transition_task(_as_running_task(task), "waiting")
        waiting_graph = transition_graph(
            self._replace_task(graph, waiting_task),
            "waiting",
        )
        self._commit(
            replace(
                state,
                graph=waiting_graph,
                attempts=self._replace_attempts(state, waiting_attempt),
                pending_task_decisions=(*state.pending_task_decisions, decision),
            ),
            root_revision,
            "human_task_waiting",
            {
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
                "request_id": request_id,
                "contract_id": contract.id,
            },
        )
        return TaskGraphStep(
            "waiting",
            self.task_plan(),
            pending=TaskGraphPending(
                kind="task",
                request_id=request_id,
                attempt_id=attempt.attempt_id,
                adapter_id=contract.id,
                dispatch_key=attempt.dispatch_key,
                proposal={
                    "kind": "human_task",
                    "prompt": str(prompt.get("title") or task.goal),
                    "payload": prompt,
                },
                decision={
                    "decision_class": contract.decision_class,
                    "allowed_decisions": list(contract.allowed_decisions),
                },
                reason="human Task response required",
            ),
        )

    def _resume_human_task(
        self,
        state: LongTaskState,
        *,
        pending: TaskGraphPending,
        payload: Mapping[str, Any],
        root_revision: int,
    ) -> TaskGraphStep:
        matches = tuple(
            item
            for item in state.pending_task_decisions
            if item.request_id == pending.request_id
        )
        if len(matches) != 1:
            raise TaskGraphRuntimeError("unknown or duplicate human Task request")
        authoritative = matches[0]
        attempt = self._attempt(state, authoritative.attempt_id)
        task = self._task_by_ref(self._require_graph(state), authoritative.task_ref)
        component = self._resolve_task_component(
            attempt.executor_binding,
            "human_task_contracts",
        )
        contract = parse_human_task_contract(component)
        if (
            contract.id != authoritative.contract_id
            or contract.fingerprint != authoritative.contract_fingerprint
            or pending.attempt_id not in {None, authoritative.attempt_id}
        ):
            raise TaskGraphRuntimeError("human Task pending binding changed")
        selected = str(payload.get("decision") or payload.get("kind") or "")
        raw_response = payload.get("response", payload.get("value", payload))
        if not isinstance(raw_response, Mapping):
            raw_response = {"value": raw_response}
        validated = validate_human_response(
            contract,
            raw_response,
            decision=selected,
        )
        consumption = consume_pending_task_decision(
            authoritative,
            response=validated.envelope,
            observed_root_revision=state.revision,
            observed_graph_revision=self._require_graph(state).revision,
            commit_root_revision=root_revision,
        )
        if consumption.replayed:
            return TaskGraphStep("running", self.task_plan())
        decisions = tuple(
            consumption.decision if item.request_id == authoritative.request_id else item
            for item in state.pending_task_decisions
        )
        updated_state = replace(state, pending_task_decisions=decisions)
        if validated.decision in {"reject", "cancel", "cancelled"}:
            return self._fail_attempt(
                updated_state,
                task,
                attempt,
                root_revision,
                "human Task was rejected",
            )
        return self._submit_internal_candidate(
            updated_state,
            task,
            attempt,
            validated.response,
            root_revision,
        )

    def _resume_goal_decision(
        self,
        state: LongTaskState,
        *,
        pending: TaskGraphPending,
        payload: Mapping[str, Any],
        root_revision: int,
    ) -> TaskGraphStep:
        matches = tuple(
            item
            for item in state.pending_goal_decisions
            if item.request_id == pending.request_id
        )
        if len(matches) != 1:
            raise TaskGraphRuntimeError("unknown or duplicate Goal decision request")
        authoritative = matches[0]
        consumption = consume_pending_goal_decision(
            authoritative,
            response=payload,
            observed_root_revision=state.revision,
            observed_graph_revision=self._require_graph(state).revision,
            commit_root_revision=root_revision,
        )
        if consumption.replayed:
            return TaskGraphStep("running", self.task_plan())
        decisions = tuple(
            consumption.decision if item.request_id == authoritative.request_id else item
            for item in state.pending_goal_decisions
        )
        consumed_state = replace(state, pending_goal_decisions=decisions)
        decision = str(payload.get("decision") or payload.get("kind") or "")
        updates = payload.get("intent_updates")
        if isinstance(updates, Mapping) and updates:
            raw_intent = updates.get("new_intent", updates.get("intent"))
            new_intent = replace(
                _parse_intent(raw_intent),
                status="confirmed",
                confirmation_proof_id=pending.request_id,
            )
            patch = _parse_intent_patch(updates.get("patch"))
            if not patch.patch_id:
                patch = replace(patch, patch_id=pending.request_id)
            confirmation = IntentConfirmation(
                intent_id=new_intent.intent_id,
                intent_version=new_intent.version,
                intent_fingerprint=intent_fingerprint(new_intent),
                confirmed_by=f"human:{decision}",
            )
            return self.request_intent_rebase(
                new_intent=new_intent,
                patch=patch,
                confirmation=confirmation,
                request_id=pending.request_id,
                root_revision=root_revision,
                _state=consumed_state,
            )
        if decision in {"reject", "cancel", "cancelled"}:
            return self._failed(
                consumed_state,
                root_revision,
                str(payload.get("rationale") or "ambiguous Goal rejected by human"),
            )
        graph = transition_graph(self._require_graph(state), "active")
        self._commit(
            replace(consumed_state, graph=graph),
            root_revision,
            "goal_replan_requested",
            {
                "graph_revision": graph.revision,
                "target_ref": f"graph:{graph.graph_id}:{graph.revision}",
                "reason": str(
                    payload.get("rationale")
                    or payload.get("direction")
                    or "human supplied a Goal repair direction"
                ),
                "details": {
                    "goal_decision_request_id": pending.request_id,
                    "response": json_value(payload),
                },
            },
        )
        return TaskGraphStep("running", self.task_plan())

    def _dispatch_child_attempt(
        self,
        state: LongTaskState,
        attempt: TaskAttempt,
        root_revision: int,
    ) -> TaskGraphStep:
        bridge = self._child_bridge
        if bridge is None:
            raise TaskGraphRuntimeError("child Agent Task has no child runtime bridge")
        task = self._task_by_ref(self._require_graph(state), attempt.task_ref)
        child_revision, child_status = bridge.prepare_child(
            state=state,
            task=task,
            attempt=attempt,
        )
        running = transition_attempt(
            attempt,
            "running",
            child_observation_revision=child_revision,
            child_observation_status=child_status,
        )
        self._commit(
            replace(state, attempts=self._replace_attempts(state, running)),
            root_revision,
            "child_started",
            {"attempt_id": attempt.attempt_id, "child_run_id": attempt.child_run_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _advance_child_attempts(
        self,
        state: LongTaskState,
        attempts: tuple[TaskAttempt, ...],
        root_revision: int,
    ) -> TaskGraphStep:
        bridge = self._child_bridge
        if bridge is None:
            raise TaskGraphRuntimeError("child Agent Task has no child runtime bridge")
        events: list[tuple[TaskAttempt, CandidateSubmission | None, Exception | None]] = []
        with ThreadPoolExecutor(max_workers=len(attempts)) as executor:
            futures = tuple(
                (attempt, executor.submit(bridge.advance_child, attempt))
                for attempt in attempts
            )
            for attempt, future in futures:
                try:
                    events.append((attempt, future.result(), None))
                except Exception as exc:
                    events.append((attempt, None, exc))
        actionable = tuple(
            sorted(
                (item for item in events if item[1] is not None or item[2] is not None),
                key=lambda item: (item[0].task_ref.id, item[0].attempt_id),
            )
        )
        if not actionable:
            return TaskGraphStep("running", self.task_plan())
        attempt, submission, error = actionable[0]
        if error is not None:
            task = self._task_by_ref(self._require_graph(state), attempt.task_ref)
            return self._fail_attempt(
                state,
                task,
                attempt,
                root_revision,
                f"child Workflow failed: {error}",
            )
        assert submission is not None
        self.receive_child_submission(submission, root_revision=root_revision)
        return TaskGraphStep("running", self.task_plan())

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

    def _submit_internal_candidate(
        self,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
        output: Any,
        root_revision: int,
        *,
        invocation: DurableComponentInvocation | None = None,
    ) -> TaskGraphStep:
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
            sealed,
            kind="candidate_output",
            attempt_id=attempt.attempt_id,
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
        current_graph = self._require_graph(state)
        if current_graph.status == "waiting":
            current_graph = transition_graph(current_graph, "active")
        invocations = state.component_invocations
        if invocation is not None:
            invocations = self._replace_component_invocations(state, invocation)
        self._commit(
            replace(
                state,
                graph=self._replace_task(current_graph, verifying),
                attempts=self._replace_attempts(state, submitted),
                receipts=(*state.receipts, receipt),
                artifacts=(*state.artifacts, artifact),
                component_invocations=invocations,
            ),
            root_revision,
            "candidate_submitted",
            {
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
                "submission_id": receipt.submission_id,
                "executor_mode": attempt.executor_binding.mode,
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
        if any(item == "needs_replan" for item in outcomes):
            return self._request_task_replan(
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
            attempt,
            "completed",
            output_refs=result_refs,
            lease=replace(attempt.lease, retiring=False),
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
                resource_locks=tuple(
                    item
                    for item in state.resource_locks
                    if item.attempt_id != attempt.attempt_id
                ),
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
                resource_locks=tuple(
                    replace(
                        item,
                        fencing_token=next_token,
                        retiring=False,
                    )
                    if item.attempt_id == attempt.attempt_id
                    else item
                    for item in state.resource_locks
                ),
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

    def _request_task_replan(
        self,
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
        receipt: CandidateReceipt,
        records: list[VerificationRecord],
        root_revision: int,
    ) -> TaskGraphStep:
        reason = next(
            (item.reason for item in records if item.outcome == "needs_replan" and item.reason),
            "Task verification requires local replanning",
        )
        rejected = replace(
            receipt,
            status="rejected",
            validator_record_ids=tuple(item.record_id for item in records),
            decision="needs_replan",
            reason=reason,
        )
        failed_attempt = transition_attempt(
            attempt,
            "failed",
            failure=reason,
            lease=replace(attempt.lease, retiring=False),
        )
        pending_task = transition_task(
            task,
            "pending",
            active_attempt_id=None,
            failure=None,
        )
        graph = self._require_graph(state)
        self._commit(
            replace(
                state,
                graph=self._replace_task(graph, pending_task),
                attempts=self._replace_attempts(state, failed_attempt),
                receipts=self._replace_receipts(state, rejected),
                resource_locks=tuple(
                    item
                    for item in state.resource_locks
                    if item.attempt_id != attempt.attempt_id
                ),
            ),
            root_revision,
            "task_replan_requested",
            {
                "graph_revision": graph.revision,
                "target_ref": self._task_ref_token(task.ref),
                "reason": reason,
                "details": {
                    "submission_id": receipt.submission_id,
                    "verification_record_ids": [
                        item.record_id for item in records
                    ],
                },
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
        *,
        final: bool = False,
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
        active_groups = tuple(
            item for item in graph.groups if item.ref in set(graph.active_group_refs)
        )
        grouped_children = {
            child.task_ref for group in active_groups for child in group.children
        }
        supporting = [
            item
            for item in self._active_tasks(graph)
            if item.ref not in grouped_children
            and item.status == "completed"
            and criterion.id in item.supports
        ]
        supporting_groups = [
            item
            for item in active_groups
            if item.status == "completed"
            and criterion.id in item.supports
        ]
        inputs = {
            "intent": json_value(self._confirmed_intent(state)),
            "criterion": json_value(criterion),
            "tasks": [json_value(item) for item in supporting],
            "groups": [json_value(item) for item in supporting_groups],
        }
        call = self._component_call(
            state,
            component,
            kind="criterion_verifier",
            idempotency_key=(
                f"root/{self._root_run_id}/criterion/{criterion.id}/graph/{graph.revision}/"
                f"phase/{'final' if final else 'incremental'}"
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
            validator_id=component.id,
            validator_version=component.version,
            invocation_id=invocation.invocation_id,
            output_hash=invocation.output_hash,
            outcome=outcome,
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
            {"criterion_id": criterion.id, "status": status, "final": final},
        )
        return TaskGraphStep("running", self.task_plan())

    def _advance_group(
        self,
        state: LongTaskState,
        root_revision: int,
    ) -> TaskGraphStep | None:
        graph = self._require_graph(state)
        active_refs = set(graph.active_group_refs)
        for group in sorted(
            (item for item in graph.groups if item.ref in active_refs),
            key=lambda item: item.group_id,
        ):
            if group.status in {"completed", "failed", "cancelled"}:
                continue
            decision = evaluate_group(
                group,
                graph,
                rejected_task_refs=self._rejected_group_task_refs(state, group),
            )
            if decision.status == group.status and decision.status not in {
                "verifying",
                "failed",
            }:
                continue
            if decision.status == "failed":
                failed = replace(group, status="failed")
                self._commit(
                    replace(state, graph=replace_group(graph, failed)),
                    root_revision,
                    "group_failed",
                    {"group_id": group.group_id, "reason": decision.reason},
                )
                return TaskGraphStep("running", self.task_plan())
            if decision.status in {"pending", "running"}:
                updated = replace(group, status=cast(Any, decision.status))
                self._commit(
                    replace(state, graph=replace_group(graph, updated)),
                    root_revision,
                    "group_running",
                    {"group_id": group.group_id},
                )
                return TaskGraphStep("running", self.task_plan())
            return self._verify_group(state, group, decision, root_revision)
        return None

    def _verify_group(
        self,
        state: LongTaskState,
        group: GroupRun,
        decision: GroupDecision,
        root_revision: int,
    ) -> TaskGraphStep:
        validator_ids = group.completion_contract.validator_ids
        if len(validator_ids) != 1:
            raise TaskGraphRuntimeError(
                f"Group {group.group_id!r} requires exactly one verifier"
            )
        validator_id = validator_ids[0]
        if validator_id not in self._config.group_validators:
            raise TaskGraphRuntimeError(
                f"Group {group.group_id!r} selects unpinned verifier {validator_id!r}"
            )
        component = self._component("group_validators", validator_id)
        inputs = {
            "intent": json_value(self._confirmed_intent(state)),
            "group": json_value(group),
            "candidates": [json_value(item) for item in decision.candidates],
            "winner": json_value(decision.winner) if decision.winner is not None else None,
        }
        graph = self._require_graph(state)
        call = self._component_call(
            state,
            component,
            kind="group_verifier",
            idempotency_key=(
                f"root/{self._root_run_id}/group/{group.group_id}/"
                f"revision/{group.group_revision}/graph/{graph.revision}/"
                f"candidates/{compute_fingerprint(inputs)}"
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
            kind="group",
            target_ref=f"group:{group.group_id}:{group.group_revision}",
            component_fingerprint=component.fingerprint,
            input_hash=invocation.input_hash,
            status=_verification_status(outcome),
            reason=cast(str | None, result.get("reason")),
            validator_id=component.id,
            validator_version=component.version,
            invocation_id=invocation.invocation_id,
            output_hash=invocation.output_hash,
            outcome=outcome,
            artifact_refs=(
                (self._task_ref_token(decision.winner.ref),)
                if decision.winner is not None
                else tuple(
                    self._task_ref_token(candidate.ref)
                    for candidate in decision.candidates
                )
            ),
        )
        updated_state = replace(
            state,
            component_invocations=self._replace_component_invocations(state, invocation),
            verification_records=(*state.verification_records, record),
        )
        if outcome != "passed":
            status = "running" if group.join_policy == "any_success" else "failed"
            rejected = replace(
                group,
                status=cast(Any, status),
                verification_record_ref=record.record_id,
            )
            self._commit(
                replace(updated_state, graph=replace_group(graph, rejected)),
                root_revision,
                "group_verification_failed",
                {
                    "group_id": group.group_id,
                    "outcome": outcome,
                    "candidate_refs": list(record.artifact_refs),
                },
            )
            return TaskGraphStep("running", self.task_plan())
        if group.join_policy == "any_success" and decision.winner is not None:
            updated_state = commit_any_success_winner(
                updated_state,
                replace(group, verification_record_ref=record.record_id),
                decision.winner,
                reason="any_success winner verified",
            )
        else:
            completed = replace(
                group,
                status="completed",
                verification_record_ref=record.record_id,
            )
            updated_state = replace(
                updated_state,
                graph=replace_group(graph, completed),
            )
        self._commit(
            updated_state,
            root_revision,
            "group_completed",
            {"group_id": group.group_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _finish_or_fail_graph(
        self,
        state: LongTaskState,
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        active = self._active_tasks(graph)
        active_groups = [
            item for item in graph.groups if item.ref in set(graph.active_group_refs)
        ]
        group_failures = [
            item.group_id for item in active_groups if item.required and item.status == "failed"
        ]
        if group_failures:
            return self._failed(
                state,
                root_revision,
                f"required Group failed: {', '.join(sorted(group_failures))}",
            )
        grouped_children = {
            child.task_ref
            for group in active_groups
            for child in group.children
        }
        failed = [
            item.task_id
            for item in active
            if item.required
            and item.status == "failed"
            and item.ref not in grouped_children
        ]
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
            and item.ref not in grouped_children
        ]
        if incomplete:
            trigger = PlanningTrigger(
                "deadlock",
                reason="Task Graph has incomplete work but no schedulable Task",
                details={
                    "graph_revision": graph.revision,
                    "incomplete_tasks": sorted(incomplete),
                },
            )
            if graph.replan_count < graph.limits.max_replans:
                return self._advance_planning(
                    state,
                    trigger,
                    0,
                    (),
                    root_revision,
                )
            return self._failed(
                state,
                root_revision,
                f"Task Graph has no ready work: {', '.join(sorted(incomplete))}",
            )
        incomplete_groups = [
            item.group_id
            for item in active_groups
            if item.required and item.status != "completed"
        ]
        if incomplete_groups:
            return self._failed(
                state,
                root_revision,
                f"Task Graph has no ready Group work: {', '.join(sorted(incomplete_groups))}",
            )
        required_ids = set(graph.required_criteria)
        reset_coverage = tuple(
            replace(
                item,
                status="unsatisfied",
                evidence_refs=(),
                verified_by=None,
            )
            if item.criterion_id in required_ids
            else item
            for item in state.criterion_coverage
        )
        self._commit(
            replace(
                state,
                graph=transition_graph(graph, "verifying"),
                criterion_coverage=reset_coverage,
            ),
            root_revision,
            "goal_verification_started",
            {
                "graph_id": graph.graph_id,
                "required_criteria": sorted(required_ids),
            },
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
            validator_id=component.id,
            validator_version=component.version,
            invocation_id=invocation.invocation_id,
            output_hash=invocation.output_hash,
            outcome=outcome,
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
        if outcome == "repairable_gap":
            reason = record.reason or "Goal verifier reported a repairable gap"
            self._commit(
                replace(
                    base,
                    graph=transition_graph(graph, "active"),
                ),
                root_revision,
                "goal_replan_requested",
                {
                    "graph_revision": graph.revision,
                    "target_ref": f"graph:{graph.graph_id}:{graph.revision}",
                    "reason": reason,
                    "details": {
                        "verification_record_id": record.record_id,
                        "gap": result.get("gap"),
                    },
                },
            )
            return TaskGraphStep("running", self.task_plan())
        if outcome == "ambiguous":
            request_id = new_ulid()
            pending_goal = PendingGoalDecision(
                request_id=request_id,
                graph_revision=graph.revision,
                goal_verification_record_id=record.record_id,
                input_hash=invocation.input_hash,
                expected_root_revision=root_revision,
                allowed_decisions=(
                    "approve",
                    "repair",
                    "revise",
                    "redirect",
                    "constrain",
                    "clarify",
                    "reject",
                    "cancel",
                ),
                criterion_gaps=tuple(
                    item
                    for item in (result.get("criterion_gaps") or ())
                    if isinstance(item, Mapping)
                ),
                options=tuple(
                    item
                    for item in (result.get("options") or ())
                    if isinstance(item, Mapping)
                ),
                prompt={
                    "reason": record.reason or "Goal verification is ambiguous",
                    "graph_id": graph.graph_id,
                },
            )
            self._commit(
                replace(
                    base,
                    graph=transition_graph(graph, "waiting"),
                    pending_goal_decisions=(
                        *state.pending_goal_decisions,
                        pending_goal,
                    ),
                ),
                root_revision,
                "goal_verification_waiting",
                {
                    "graph_id": graph.graph_id,
                    "record_id": record.record_id,
                    "request_id": request_id,
                },
            )
            return TaskGraphStep(
                "waiting",
                self.task_plan(),
                pending=TaskGraphPending(
                    kind="goal",
                    request_id=request_id,
                    decision={
                        "allowed_decisions": list(pending_goal.allowed_decisions),
                        "criterion_gaps": [
                            json_value(item) for item in pending_goal.criterion_gaps
                        ],
                        "options": [json_value(item) for item in pending_goal.options],
                    },
                    reason=record.reason or "Goal verification is ambiguous",
                ),
            )
        reason = record.reason or (
            "Goal verifier passed without satisfying every required criterion"
            if outcome == "passed"
            else f"Goal verifier returned {outcome}"
        )
        return self._failed(base, root_revision, reason)

    @staticmethod
    def _pending_intent_rebase(state: LongTaskState) -> Mapping[str, Any] | None:
        finished = {
            str(event.payload.get("request_id"))
            for event in state.events
            if event.event_type in {"intent_rebased", "intent_rebase_failed"}
        }
        for event in reversed(state.events):
            if event.event_type != "intent_rebase_requested":
                continue
            request_id = str(event.payload.get("request_id") or "")
            if request_id and request_id not in finished:
                return event.payload
        return None

    def _advance_intent_rebase(
        self,
        state: LongTaskState,
        request: Mapping[str, Any],
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        if int(request.get("graph_revision", -1)) != graph.revision:
            raise TaskGraphRuntimeError("Intent rebase request targets a stale graph revision")
        new_intent = _parse_intent(request.get("new_intent"))
        patch = _parse_intent_patch(request.get("patch"))
        confirmation = _parse_intent_confirmation(request.get("confirmation"))
        verifier = self._component("graph_policy", self._config.graph_policy)
        candidates = [
            {
                "target_ref": {
                    "kind": item.ref.kind,
                    "id": item.ref.id,
                    "revision": item.ref.revision,
                },
                "status": item.status,
                "intent_version": item.intent_version,
                "intent_binding_hash": item.intent_binding_hash,
                "depends_on": [json_value(ref) for ref in _object_dependencies(item)],
                "completion_contract_hash": compute_fingerprint(
                    json_value(item.completion_contract)
                ),
                "output_refs": list(item.output_refs) if isinstance(item, TaskRun) else [],
            }
            for item in self._rebase_candidates(graph)
        ]
        inputs = {
            "request_id": request["request_id"],
            "prior_intent": json_value(self._confirmed_intent(state)),
            "new_intent": json_value(new_intent),
            "patch": patch.snapshot(),
            "candidates": candidates,
        }
        call = self._component_call(
            state,
            verifier,
            kind="rebase_verifier",
            idempotency_key=(
                f"root/{self._root_run_id}/rebase/{request['request_id']}/verify"
            ),
            inputs=inputs,
            root_revision=root_revision,
        )
        if isinstance(call, TaskGraphStep):
            return call
        output, invocation = call
        outcome, result = verifier_outcome(verifier, output)
        if outcome != "passed":
            raise TaskGraphRuntimeError(
                f"Intent rebase verifier returned unsupported outcome {outcome!r}"
            )
        proofs = self._rebase_reuse_proofs(
            graph=graph,
            new_intent=new_intent,
            invocation=invocation,
            result=result,
            component=verifier,
        )
        plan = plan_intent_rebase(
            state,
            new_intent=new_intent,
            patch=patch,
            confirmation=confirmation,
            reuse_proofs=proofs,
        )
        return self._apply_intent_rebase_plan(
            state,
            request=request,
            plan=plan,
            invocation=invocation,
            verifier=verifier,
            reuse_proofs=proofs,
            root_revision=root_revision,
        )

    @staticmethod
    def _rebase_candidates(
        graph: TaskGraphRun,
    ) -> tuple[TaskRun | GroupRun, ...]:
        task_refs = {item.key for item in graph.active_task_refs}
        group_refs = {item.key for item in graph.active_group_refs}
        return (
            *(item for item in graph.tasks if item.ref.key in task_refs),
            *(item for item in graph.groups if item.ref.key in group_refs),
        )

    @staticmethod
    def _rebase_reuse_proofs(
        *,
        graph: TaskGraphRun,
        new_intent: IntentVersion,
        invocation: DurableComponentInvocation,
        result: Mapping[str, Any],
        component: PinnedComponent,
    ) -> tuple[RebaseReuseProof, ...]:
        raw_decisions = result.get("reuse_decisions", result.get("reuse", ()))
        if not isinstance(raw_decisions, tuple | list):
            raise TaskGraphRuntimeError("rebase verifier reuse decisions must be an array")
        candidates = {
            item.ref.key: item
            for item in OperationTaskGraphRuntime._rebase_candidates(graph)
        }
        seen: set[tuple[str, str, int]] = set()
        proofs: list[RebaseReuseProof] = []
        for raw in raw_decisions:
            if not isinstance(raw, Mapping):
                raise TaskGraphRuntimeError("rebase verifier returned a malformed decision")
            ref = _parse_dependency_ref(raw.get("target_ref"))
            if ref.key in seen:
                raise TaskGraphRuntimeError("rebase verifier returned a duplicate target")
            seen.add(ref.key)
            item = candidates.get(ref.key)
            if item is None:
                raise TaskGraphRuntimeError("rebase verifier returned an unknown target")
            if not isinstance(raw.get("reusable"), bool):
                raise TaskGraphRuntimeError("rebase verifier reusable must be boolean")
            if not raw["reusable"] or item.status in {"pending", "failed", "cancelled"}:
                continue
            dependencies = _object_dependencies(item)
            proofs.append(
                RebaseReuseProof(
                    record_id=new_ulid(),
                    target_ref=ref,
                    prior_intent_version=item.intent_version,
                    new_intent_version=new_intent.version,
                    intent_binding_hash=item.intent_binding_hash,
                    dependency_refs=dependencies,
                    completion_contract_hash=compute_fingerprint(
                        json_value(item.completion_contract)
                    ),
                    reusable=True,
                    validator_fingerprint=component.fingerprint,
                    new_intent_fingerprint=intent_fingerprint(new_intent),
                )
            )
        return tuple(proofs)

    def _apply_intent_rebase_plan(
        self,
        state: LongTaskState,
        *,
        request: Mapping[str, Any],
        plan: IntentRebasePlan,
        invocation: DurableComponentInvocation,
        verifier: PinnedComponent,
        reuse_proofs: tuple[RebaseReuseProof, ...],
        root_revision: int,
    ) -> TaskGraphStep:
        graph = self._require_graph(state)
        if (
            state.revision != plan.expected_root_revision
            or graph.revision != plan.expected_graph_revision
        ):
            raise TaskGraphRuntimeError("Intent rebase CAS precondition is stale")
        decisions = {item.target_ref.key: item for item in plan.binding_decisions}
        cancellation_by_attempt = {item.attempt_id: item for item in plan.cancellations}
        tasks: list[TaskRun] = []
        for task in graph.tasks:
            decision = decisions.get(task.ref.key)
            if decision is None:
                tasks.append(task)
                continue
            if decision.decision == "retained":
                tasks.append(replace(task, intent_binding_state="retained"))
                continue
            status = task.status
            active_attempt_id = task.active_attempt_id
            failure = task.failure
            if status in {"pending", "running", "waiting", "verifying"}:
                status = "cancelled"
                active_attempt_id = None
                failure = "superseded by confirmed Intent rebase"
            tasks.append(
                replace(
                    task,
                    intent_binding_state="invalidated",
                    status=status,
                    active_attempt_id=active_attempt_id,
                    failure=failure,
                )
            )
        tasks.extend(plan.task_revisions_to_append)
        groups: list[GroupRun] = []
        for group in graph.groups:
            decision = decisions.get(group.ref.key)
            if decision is None:
                groups.append(group)
                continue
            if decision.decision == "retained":
                groups.append(replace(group, intent_binding_state="retained"))
                continue
            status = group.status
            if status in {"pending", "running", "verifying"}:
                status = "cancelled"
            groups.append(
                replace(
                    group,
                    intent_binding_state="invalidated",
                    status=status,
                    winner_task_ref=None,
                    verification_record_ref=None,
                )
            )
        groups.extend(plan.group_revisions_to_append)
        attempts: list[TaskAttempt] = []
        for attempt in state.attempts:
            cancellation = cancellation_by_attempt.get(attempt.attempt_id)
            if cancellation is None:
                attempts.append(attempt)
                continue
            attempts.append(
                replace(
                    attempt,
                    status="cancelled",
                    failure=cancellation.reason,
                    lease=replace(attempt.lease, retiring=True),
                )
            )
        locks = tuple(
            replace(lock, retiring=True)
            if lock.attempt_id in cancellation_by_attempt
            else lock
            for lock in state.resource_locks
        )
        cancellation_requests = (
            *state.cancellation_requests,
            *(
                CancellationRequest(
                    cancellation_id=new_ulid(),
                    attempt_id=item.attempt_id,
                    reason=item.reason,
                    lease_epoch=item.lease_epoch,
                    lease_token=item.lease_token,
                )
                for item in plan.cancellations
            ),
        )
        old_intents = tuple(
            plan.superseded_intent
            if item.intent_id == plan.prior_intent.intent_id
            and item.version == plan.prior_intent.version
            else item
            for item in state.intents
        )
        intents = (*old_intents, plan.new_intent)
        verification_records = (
            *state.verification_records,
            *(
                VerificationRecord(
                    record_id=proof.record_id,
                    kind="rebase",
                    target_ref=_ref_token(proof.target_ref),
                    component_fingerprint=invocation.component_fingerprint,
                    input_hash=invocation.input_hash,
                    status="passed",
                    reason="exact reusable completion proof",
                    validator_id=verifier.id,
                    validator_version=verifier.version,
                    invocation_id=invocation.invocation_id,
                    output_hash=invocation.output_hash,
                    outcome="passed",
                )
                for proof in reuse_proofs
            ),
        )
        updated_graph = replace(
            graph,
            intent_id=plan.new_intent.intent_id,
            intent_version=plan.new_intent.version,
            revision=plan.next_graph_revision,
            status="active",
            required_criteria=tuple(
                item.id for item in plan.new_intent.success_criteria if item.required
            ),
            tasks=tuple(tasks),
            groups=tuple(groups),
            active_task_refs=plan.active_task_refs,
            active_group_refs=plan.active_group_refs,
        )
        updated_state = replace(
            state,
            intents=intents,
            graph=updated_graph,
            attempts=tuple(attempts),
            component_invocations=self._replace_component_invocations(state, invocation),
            verification_records=verification_records,
            criterion_coverage=tuple(
                CriterionCoverage(item.id, "unsatisfied")
                for item in plan.new_intent.success_criteria
            ),
            resource_locks=locks,
            cancellation_requests=cancellation_requests,
        )
        self._validate_task_graph(
            updated_graph,
            plan.new_intent,
            require_clean_seed=False,
            verification_records=verification_records,
            allow_incomplete_coverage=True,
        )
        self._commit(
            updated_state,
            root_revision,
            "intent_rebased",
            {
                "request_id": request["request_id"],
                "graph_revision": updated_graph.revision,
                "intent_version": plan.new_intent.version,
                "binding_decisions": [json_value(item) for item in plan.binding_decisions],
                "cancellation_attempt_ids": [item.attempt_id for item in plan.cancellations],
                "reuse_proof_ids": [item.record_id for item in reuse_proofs],
            },
        )
        return TaskGraphStep("running", self.task_plan())

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
        failed_attempt = transition_attempt(
            attempt,
            "failed",
            failure=reason,
            lease=replace(attempt.lease, retiring=False),
        )
        failed_task = transition_task(
            _as_running_task(task) if task.status == "waiting" else task,
            "failed",
            active_attempt_id=None,
            failure=reason,
        )
        current_graph = self._require_graph(state)
        graph = self._replace_task(current_graph, failed_task)
        grouped = any(
            group.ref in set(current_graph.active_group_refs)
            and any(child.task_ref == task.ref for child in group.children)
            for group in current_graph.groups
        )
        if not grouped:
            graph = transition_graph(graph, "failed")
        self._commit(
            replace(
                state,
                graph=graph,
                attempts=self._replace_attempts(state, failed_attempt),
                resource_locks=tuple(
                    item
                    for item in state.resource_locks
                    if item.attempt_id != attempt.attempt_id
                ),
            ),
            root_revision,
            "task_failed",
            {"task_id": task.task_id, "attempt_id": attempt.attempt_id, "reason": reason},
        )
        return TaskGraphStep(
            "running" if grouped else "failed",
            self.task_plan(),
            error=None if grouped else reason,
        )

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

    def _resolve_task_component(
        self,
        binding: Any,
        field: str,
    ) -> PinnedComponent:
        component = self._component(field, binding.id)
        if binding.component_fingerprint != component.fingerprint:
            raise TaskGraphRuntimeError(
                f"Task binding fingerprint changed for component {binding.id!r}"
            )
        return component

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

    def _validate_task_graph(
        self,
        graph: TaskGraphRun,
        intent: IntentVersion,
        *,
        require_clean_seed: bool = True,
        verification_records: tuple[VerificationRecord, ...] = (),
        allow_incomplete_coverage: bool = False,
    ) -> None:
        validate_graph(
            graph,
            allow_incomplete_coverage=allow_incomplete_coverage,
        )
        allowed_operations = set(self._config.operation_adapters)
        allowed_children = set(self._config.child_templates)
        intent_hash = compute_fingerprint(json_value(intent))
        for task in self._active_tasks(graph):
            if require_clean_seed and (
                task.status != "pending"
                or task.active_attempt_id is not None
                or task.output_refs
                or task.failure is not None
            ):
                raise TaskGraphRuntimeError(
                    f"seed Task {task.task_id!r} must start as clean pending work"
                )
            if task.intent_binding_state == "retained":
                if not _has_rebase_verification(verification_records, task.ref):
                    raise TaskGraphRuntimeError(
                        f"Task {task.task_id!r} has no exact retained-binding proof"
                    )
            elif (
                task.intent_binding_state != "current"
                or task.intent_version != intent.version
                or task.intent_binding_hash != intent_hash
            ):
                raise TaskGraphRuntimeError(
                    f"Task {task.task_id!r} does not bind the confirmed Intent"
                )
            if not task.completion_contract.validator_ids or any(
                item not in self._config.task_validators
                for item in task.completion_contract.validator_ids
            ):
                raise TaskGraphRuntimeError(
                    f"Task {task.task_id!r} selects an unpinned Task verifier"
                )
            bindings = task.executor_policy.allowed_bindings
            if not bindings or any(
                item.mode not in {
                    "operation",
                    "child_agent",
                    "parent_inline",
                    "human",
                }
                for item in bindings
            ):
                raise TaskGraphRuntimeError(
                    "Task bindings select an unsupported executor mode"
                )
            if task.executor_policy.preferred_binding not in bindings:
                raise TaskGraphRuntimeError("preferred Task binding is not allowed")
            for binding in bindings:
                if binding.mode == "parent_inline":
                    if binding.id not in self._config.parent_inline_components:
                        raise TaskGraphRuntimeError(
                            f"Task selects unpinned parent-inline component {binding.id!r}"
                        )
                    self._resolve_task_component(
                        binding,
                        "parent_inline_components",
                    )
                    continue
                if binding.mode == "human":
                    if binding.id not in self._config.human_task_contracts:
                        raise TaskGraphRuntimeError(
                            f"Task selects unpinned human contract {binding.id!r}"
                        )
                    self._resolve_task_component(binding, "human_task_contracts")
                    continue
                if binding.mode == "child_agent":
                    if binding.id not in allowed_children:
                        raise TaskGraphRuntimeError(
                            f"Task selects unpinned child template {binding.id!r}"
                        )
                    self._child_template(binding)
                    continue
                if binding.id not in allowed_operations:
                    raise TaskGraphRuntimeError(
                        f"Task selects unpinned Operation adapter {binding.id!r}"
                    )
                adapter = self._adapters.resolve_node_adapter(binding.id)
                expected = compute_fingerprint(adapter.snapshot())
                if binding.component_fingerprint != expected:
                    raise TaskGraphRuntimeError(
                        f"Task binding fingerprint changed for adapter {binding.id!r}"
                    )
        active_group_refs = set(graph.active_group_refs)
        for group in graph.groups:
            if group.ref not in active_group_refs:
                continue
            if group.intent_binding_state == "retained":
                if not _has_rebase_verification(verification_records, group.ref):
                    raise TaskGraphRuntimeError(
                        f"Group {group.group_id!r} has no exact retained-binding proof"
                    )
            elif (
                group.intent_binding_state != "current"
                or group.intent_version != intent.version
                or group.intent_binding_hash != intent_hash
            ):
                raise TaskGraphRuntimeError(
                    f"Group {group.group_id!r} does not bind the confirmed Intent"
                )
            if not group.completion_contract.validator_ids or any(
                item not in self._config.group_validators
                for item in group.completion_contract.validator_ids
            ):
                raise TaskGraphRuntimeError(
                    f"Group {group.group_id!r} selects an unpinned Group verifier"
                )

    def _child_template(self, binding: Any) -> Mapping[str, Any]:
        templates = self._contract_child_templates()
        template = next(
            (
                item
                for item in templates
                if isinstance(item, Mapping) and item.get("id") == binding.id
            ),
            None,
        )
        if template is None:
            raise TaskGraphRuntimeError(
                f"Task Graph contract has no pinned child template {binding.id!r}"
            )
        if binding.component_fingerprint != template.get("fingerprint"):
            raise TaskGraphRuntimeError(
                f"Task binding fingerprint changed for child template {binding.id!r}"
            )
        return template

    def _contract_child_templates(self) -> tuple[Mapping[str, Any], ...]:
        templates = self._contract_node().get("child_templates")
        if not isinstance(templates, tuple | list):
            raise TaskGraphRuntimeError("Task Graph contract child templates are malformed")
        return tuple(item for item in templates if isinstance(item, Mapping))

    def _resolve_task_adapter(self, task: TaskRun) -> OperationAdapter:
        binding = task.executor_policy.preferred_binding
        return self._resolve_operation_binding(binding)

    def _resolve_operation_binding(self, binding: Any) -> OperationAdapter:
        if binding.mode != "operation" or binding.id not in self._config.operation_adapters:
            raise TaskGraphRuntimeError(
                f"Task selects unpinned Operation adapter {binding.id!r}"
            )
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
        if attempt.executor_binding not in task.executor_policy.allowed_bindings:
            raise TaskGraphRuntimeError("Attempt binding is outside persisted Task policy")
        return self._resolve_operation_binding(attempt.executor_binding)

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
        *,
        allow_cancelled_loser: bool = False,
    ) -> None:
        if attempt.executor_binding.mode != "child_agent":
            raise TaskGraphRuntimeError("CandidateSubmission requires a child Agent Attempt")
        active = attempt.status in {"running", "waiting"} and task.status in {
            "running",
            "waiting",
        }
        cancelled_loser = (
            allow_cancelled_loser
            and attempt.status == "cancelled"
            and task.status == "cancelled"
            and attempt.lease.retiring
        )
        if not active and not cancelled_loser:
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

    @staticmethod
    def _is_cancelled_group_loser(
        state: LongTaskState,
        task: TaskRun,
        attempt: TaskAttempt,
    ) -> bool:
        graph = state.graph
        if graph is None:
            return False
        group = next(
            (
                item
                for item in graph.groups
                if item.ref in set(graph.active_group_refs)
                and item.join_policy == "any_success"
                and item.status == "completed"
                and item.winner_task_ref != task.ref
                and any(child.task_ref == task.ref for child in item.children)
            ),
            None,
        )
        return bool(
            group is not None
            and task.status == "cancelled"
            and attempt.status == "cancelled"
            and attempt.lease.retiring
            and any(
                item.attempt_id == attempt.attempt_id
                and item.lease_epoch == attempt.lease.epoch
                and item.lease_token == attempt.lease.token
                for item in state.cancellation_requests
            )
        )

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
        active_groups = tuple(
            item for item in graph.groups if item.ref in set(graph.active_group_refs)
        )
        grouped_children = {
            child.task_ref for group in active_groups for child in group.children
        }
        for criterion in self._confirmed_intent(state).success_criteria:
            coverage = self._coverage(state, criterion.id)
            if coverage.status != "unsatisfied":
                continue
            supporting = [
                task
                for task in active
                if task.ref not in grouped_children and criterion.id in task.supports
            ]
            groups = [
                item
                for item in active_groups
                if criterion.id in item.supports
            ]
            if (supporting or groups) and all(
                task.status == "completed" for task in supporting
            ) and all(group.status == "completed" for group in groups):
                return criterion
        return None

    def _pending_planning(
        self,
        state: LongTaskState,
    ) -> tuple[
        PlanningTrigger,
        int,
        tuple[Mapping[str, Any], ...],
    ] | None:
        graph = self._require_graph(state)
        consumed = {
            str(event.payload.get("trigger_key"))
            for event in state.events
            if event.event_type == "graph_patch_applied"
        }
        for event in reversed(state.events):
            if event.event_type != "intent_rebased":
                continue
            if int(event.payload.get("graph_revision", -1)) != graph.revision:
                continue
            trigger = PlanningTrigger(
                "user_change",
                reason="confirmed Intent changed",
                details={
                    "request_id": event.payload.get("request_id"),
                    "intent_version": event.payload.get("intent_version"),
                },
            )
            if self._planning_trigger_key(trigger) not in consumed:
                return trigger, 0, ()
        for event in reversed(state.events):
            if event.event_type != "planner_patch_rejected":
                continue
            if int(event.payload.get("graph_revision", -1)) != graph.revision:
                continue
            trigger_raw = event.payload.get("trigger")
            if not isinstance(trigger_raw, Mapping):
                continue
            trigger = PlanningTrigger(
                kind=cast(Any, trigger_raw.get("kind")),
                target_ref=cast(str | None, trigger_raw.get("target_ref")),
                reason=cast(str | None, trigger_raw.get("reason")),
                details={
                    **dict(
                        cast(
                            Mapping[str, Any],
                            trigger_raw.get("details") or {},
                        )
                    ),
                    "repair_feedback": event.payload.get("feedback"),
                },
            )
            trigger_key = self._planning_trigger_key(trigger, include_details=False)
            recorded_key = str(event.payload.get("trigger_key"))
            if recorded_key in consumed or trigger_key != recorded_key:
                continue
            return (
                trigger,
                int(event.payload.get("repair_attempt", 0)) + 1,
                self._discovered_work_for_trigger(state, trigger),
            )
        for event in state.events:
            if event.event_type not in {
                "task_replan_requested",
                "goal_replan_requested",
            }:
                continue
            if int(event.payload.get("graph_revision", -1)) != graph.revision:
                continue
            trigger = PlanningTrigger(
                "verification_failed"
                if event.event_type == "task_replan_requested"
                else "goal_gap",
                target_ref=cast(str | None, event.payload.get("target_ref")),
                reason=cast(str | None, event.payload.get("reason")),
                details=cast(
                    Mapping[str, Any],
                    event.payload.get("details") or {},
                ),
            )
            if self._planning_trigger_key(trigger) not in consumed:
                return trigger, 0, ()
        for receipt in sorted(state.receipts, key=lambda item: item.submission_id):
            if receipt.status != "accepted" or not receipt.submission_snapshot:
                continue
            submission = CandidateSubmission.from_snapshot(receipt.submission_snapshot)
            if not submission.discovered_work:
                continue
            try:
                discovered = normalize_discovered_work(
                    submission.discovered_work,
                    source_submission_id=submission.submission_id,
                )
            except ValueError:
                continue
            trigger = PlanningTrigger(
                "discovered_work",
                target_ref=self._task_ref_token(submission.task_ref),
                reason="child suggested additional bounded work",
                details={"source_submission_id": submission.submission_id},
            )
            if self._planning_trigger_key(trigger) not in consumed:
                return trigger, 0, discovered
        for task in sorted(
            self._active_tasks(graph),
            key=lambda item: (-item.priority, item.task_id, item.task_revision),
        ):
            if (
                task.kind == "expandable"
                and task.status == "pending"
                and all(
                    self._dependency_is_completed(graph, dependency)
                    for dependency in task.depends_on
                )
            ):
                trigger = PlanningTrigger(
                    "expandable_ready",
                    target_ref=self._task_ref_token(task.ref),
                    reason="expandable Task reached the planning frontier",
                )
                if self._planning_trigger_key(trigger) not in consumed:
                    return trigger, 0, ()
        return None

    def _discovered_work_for_trigger(
        self,
        state: LongTaskState,
        trigger: PlanningTrigger,
    ) -> tuple[Mapping[str, Any], ...]:
        source_submission_id = trigger.details.get("source_submission_id")
        if not isinstance(source_submission_id, str):
            return ()
        receipt = next(
            (
                item
                for item in state.receipts
                if item.submission_id == source_submission_id
                and item.submission_snapshot
            ),
            None,
        )
        if receipt is None:
            return ()
        submission = CandidateSubmission.from_snapshot(receipt.submission_snapshot)
        try:
            return normalize_discovered_work(
                submission.discovered_work,
                source_submission_id=submission.submission_id,
            )
        except ValueError:
            return ()

    @staticmethod
    def _planning_trigger_key(
        trigger: PlanningTrigger,
        *,
        include_details: bool = True,
    ) -> str:
        snapshot = trigger.snapshot()
        if not include_details:
            details = dict(cast(Mapping[str, Any], snapshot.get("details") or {}))
            details.pop("repair_feedback", None)
            snapshot["details"] = details
        return compute_fingerprint(snapshot)

    def _validate_planning_trigger_resolution(
        self,
        before: TaskGraphRun,
        after: TaskGraphRun,
        trigger: PlanningTrigger,
        patch: GraphPatch,
    ) -> None:
        if trigger.kind in {"expandable_ready", "verification_failed"}:
            if trigger.target_ref is None:
                raise TaskGraphRuntimeError(
                    f"{trigger.kind} trigger requires an exact target"
                )
            remaining = {
                self._task_ref_token(task.ref)
                for task in self._active_tasks(after)
            }
            if trigger.target_ref in remaining:
                raise TaskGraphRuntimeError(
                    f"GraphPatch did not resolve exact {trigger.kind} target "
                    f"{trigger.target_ref!r}"
                )
            return
        if trigger.kind == "deadlock":
            if not ready_tasks(after):
                raise TaskGraphRuntimeError(
                    "deadlock GraphPatch did not create any ready executable work"
                )
            return
        if trigger.kind == "discovered_work":
            discovery_operations = {
                "add_task",
                "add_repair_task",
                "add_verification_task",
                "add_group",
                "expand_task",
            }
            if not any(
                item.op in discovery_operations for item in patch.operations
            ):
                raise TaskGraphRuntimeError(
                    "discovered-work GraphPatch did not add or expand any work"
                )
            return
        if trigger.kind == "goal_gap":
            repair_operations = {
                "add_repair_task",
                "add_verification_task",
                "replace_pending_task",
                "replace_pending_group",
                "supersede_completed_task",
                "expand_task",
            }
            if not any(item.op in repair_operations for item in patch.operations):
                raise TaskGraphRuntimeError(
                    "Goal-gap GraphPatch contains no repair or verification operation"
                )

    @staticmethod
    def _dependency_is_completed(
        graph: TaskGraphRun,
        dependency: DependencyRef,
    ) -> bool:
        items: tuple[Any, ...] = (
            graph.tasks if dependency.kind == "task" else graph.groups
        )
        return any(
            item.ref == dependency and item.status == "completed"
            for item in items
        )

    @classmethod
    def _rejected_group_task_refs(
        cls,
        state: LongTaskState,
        group: GroupRun,
    ) -> tuple[DependencyRef, ...]:
        target = f"group:{group.group_id}:{group.group_revision}"
        rejected_tokens = {
            token
            for record in state.verification_records
            if record.kind == "group"
            and record.target_ref == target
            and record.status != "passed"
            for token in record.artifact_refs
        }
        return tuple(
            sorted(
                (
                    child.task_ref
                    for child in group.children
                    if cls._task_ref_token(child.task_ref) in rejected_tokens
                ),
                key=lambda ref: (ref.id, ref.revision),
            )
        )

    @staticmethod
    def _task_ref_token(ref: DependencyRef) -> str:
        return f"task:{ref.id}:{ref.revision}"

    def _dispatchable_attempt(self, state: LongTaskState) -> TaskAttempt | None:
        for attempt in state.attempts:
            if attempt.status in {"created", "leased"}:
                return attempt
        return None

    @staticmethod
    def _active_child_attempts(state: LongTaskState) -> tuple[TaskAttempt, ...]:
        return tuple(
            sorted(
                (
                    attempt
                    for attempt in state.attempts
                    if attempt.executor_binding.mode == "child_agent"
                    and attempt.status in {"running", "waiting"}
                ),
                key=lambda item: (item.task_ref.id, item.attempt_id),
            )
        )

    @staticmethod
    def _pending_cancellation(state: LongTaskState) -> Any | None:
        return next(
            (
                item
                for item in state.cancellation_requests
                if item.status == "requested"
            ),
            None,
        )

    def _process_cancellation(
        self,
        state: LongTaskState,
        cancellation: Any,
        root_revision: int,
    ) -> TaskGraphStep:
        attempt = self._attempt(state, cancellation.attempt_id)
        if attempt.executor_binding.mode == "child_agent" and self._child_bridge is not None:
            late_submission = self._child_bridge.cancel_child(
                attempt,
                reason=cancellation.reason,
            )
            if late_submission is not None and not any(
                item.submission_id == late_submission.submission_id
                for item in state.receipts
            ):
                self.receive_child_submission(
                    late_submission,
                    root_revision=root_revision,
                )
                return TaskGraphStep("running", self.task_plan())
        acknowledged = replace(cancellation, status="acknowledged")
        attempts = self._replace_attempts(
            state,
            replace(attempt, lease=replace(attempt.lease, retiring=False)),
        )
        self._commit(
            replace(
                state,
                attempts=attempts,
                resource_locks=tuple(
                    item
                    for item in state.resource_locks
                    if item.attempt_id != attempt.attempt_id
                ),
                cancellation_requests=tuple(
                    acknowledged if item.cancellation_id == acknowledged.cancellation_id else item
                    for item in state.cancellation_requests
                ),
            ),
            root_revision,
            "cancellation_acknowledged",
            {"attempt_id": attempt.attempt_id},
        )
        return TaskGraphStep("running", self.task_plan())

    def _dependency_outputs(self, state: LongTaskState, task: TaskRun) -> list[str]:
        graph = self._require_graph(state)
        refs: list[str] = []
        for dependency in task.depends_on:
            if dependency.kind == "task":
                refs.extend(self._task_by_ref(graph, dependency).output_refs)
                continue
            group = next(
                (item for item in graph.groups if item.ref == dependency),
                None,
            )
            if group is None or group.status != "completed":
                raise TaskGraphRuntimeError("Group dependency is not completed")
            if group.join_policy == "any_success":
                if group.winner_task_ref is None:
                    raise TaskGraphRuntimeError(
                        "completed any_success Group has no winner"
                    )
                child_refs = {group.winner_task_ref}
            else:
                child_refs = {
                    item.task_ref for item in group.children if item.required
                }
            refs.extend(
                output
                for child in graph.tasks
                if child.ref in child_refs and child.status == "completed"
                for output in child.output_refs
            )
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
    def _resource_locks(attempt: TaskAttempt) -> tuple[Any, ...]:
        from .types import ResourceLock

        return tuple(
            ResourceLock(
                resource_key=key,
                attempt_id=attempt.attempt_id,
                fencing_token=attempt.lease.token,
            )
            for key in attempt.lease.resource_keys
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
        confirmation_proof_id=(
            str(raw["confirmation_proof_id"])
            if raw.get("confirmation_proof_id") is not None
            else None
        ),
    )
    if not intent.intent_id or intent.version < 1 or not intent.goal or not intent.desired_outcome:
        raise TaskGraphRuntimeError("Intent identity, version, goal, and desired outcome are required")
    if any(not item.id or not item.description for item in intent.success_criteria):
        raise TaskGraphRuntimeError("Intent criteria require id and description")
    return intent


def _parse_intent_patch(raw: Any) -> IntentPatch:
    if isinstance(raw, IntentPatch):
        return raw
    if not isinstance(raw, Mapping):
        raise TaskGraphRuntimeError("Intent rebase patch must be a mapping")
    changes_raw = raw.get("changes")
    if not isinstance(changes_raw, tuple | list):
        raise TaskGraphRuntimeError("Intent rebase patch changes must be an array")
    changes: list[IntentPatchChange] = []
    for item in changes_raw:
        if not isinstance(item, Mapping):
            raise TaskGraphRuntimeError("Intent rebase patch change must be a mapping")
        changes.append(
            IntentPatchChange(
                op=str(item.get("op") or ""),
                target=str(item.get("target") or ""),
                value=item.get("value"),
                impact=cast(Any, item.get("impact", "material")),
                authority_effect=cast(Any, item.get("authority_effect", "none")),
            )
        )
    return IntentPatch(
        base_version=int(raw.get("base_version") or 0),
        reason=str(raw.get("reason") or ""),
        changes=tuple(changes),
        patch_id=str(raw.get("patch_id") or ""),
    )


def _parse_intent_confirmation(raw: Any) -> IntentConfirmation:
    if isinstance(raw, IntentConfirmation):
        return raw
    if not isinstance(raw, Mapping):
        raise TaskGraphRuntimeError("Intent rebase confirmation must be a mapping")
    return IntentConfirmation(
        intent_id=str(raw.get("intent_id") or ""),
        intent_version=int(raw.get("intent_version") or 0),
        intent_fingerprint=str(raw.get("intent_fingerprint") or ""),
        confirmed_by=str(raw.get("confirmed_by") or "human"),
    )


def _parse_dependency_ref(raw: Any) -> DependencyRef:
    if isinstance(raw, DependencyRef):
        return raw
    if not isinstance(raw, Mapping):
        raise TaskGraphRuntimeError("rebase verifier target_ref must be a mapping")
    kind = raw.get("kind")
    object_id = raw.get("id")
    revision = raw.get("revision")
    if (
        kind not in {"task", "group"}
        or not isinstance(object_id, str)
        or not object_id.strip()
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
    ):
        raise TaskGraphRuntimeError("rebase verifier target_ref is invalid")
    return DependencyRef(cast(Any, kind), object_id, revision)


def _object_dependencies(item: TaskRun | GroupRun) -> tuple[DependencyRef, ...]:
    if isinstance(item, TaskRun):
        return item.depends_on
    return item.depends_on + tuple(child.task_ref for child in item.children)


def _ref_token(ref: DependencyRef) -> str:
    return f"{ref.kind}:{ref.id}:{ref.revision}"


def _has_rebase_verification(
    records: tuple[VerificationRecord, ...],
    ref: DependencyRef,
) -> bool:
    target = _ref_token(ref)
    return any(
        item.kind == "rebase"
        and item.target_ref == target
        and item.status == "passed"
        and item.outcome == "passed"
        and bool(item.component_fingerprint)
        and bool(item.input_hash)
        for item in records
    )


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
