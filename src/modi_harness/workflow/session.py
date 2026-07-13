"""Public-session adapter for the mandatory Workflow runtime."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType, SimpleNamespace
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint

from .._utils import compute_fingerprint, new_ulid, now_iso
from ..actions import ActionGateway
from ..api._session_helpers import agent_to_profile
from ..api.agent import ModiAgent
from ..brain import DefaultBrain
from ..brain.model import ModelStructuredPlanner
from ..memory import MemoryScopeKeys, MemoryStore
from ..tools.registry import ToolRegistry
from ..types import (
    AgentProfile,
    AgentState,
    PermissionMode,
    RunTaskResponse,
    StreamEvent,
    ToolCallProposal,
    TraceEvent,
    WorkspaceRef,
)
from ..workspace import WorkspaceManager
from .contract import (
    CompletionValidatorRegistry,
    ExecutionContract,
    OperationAdapter,
    OperationAdapterRegistry,
    build_execution_contract,
)
from .router import select_workflow
from .runtime import (
    InMemoryWorkflowStore,
    InvocationRecord,
    OperationDispatchResult,
    PendingOperation,
    TransitionRecord,
    WorkflowRuntime,
    WorkflowState,
)
from .types import Workflow

_CHECKPOINT_CHANNEL = "modi_workflow_session"


@dataclass(frozen=True, slots=True)
class RunInputFile:
    name: str
    data: bytes | str | dict[str, Any] | list[Any]
    mime_type: str | None = None
    trust: Literal["trusted", "untrusted"] = "trusted"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunTaskInput:
    agent: str
    input: dict[str, Any]
    workflow_id: str | None = None
    inputs: list[RunInputFile | dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    permission_mode: PermissionMode | None = None
    thread_id: str | None = None
    parent_run_id: str | None = None
    parent_thread_id: str | None = None


@dataclass(slots=True)
class _RunContext:
    thread_id: str
    agent: ModiAgent
    workflow: Workflow
    contract: ExecutionContract
    runtime: WorkflowRuntime
    dispatcher: _GatewayDispatcher
    traces: list[TraceEvent]
    sequence: int = 0


class _GatewayDispatcher:
    """Bridge trusted Workflow adapters through Policy, hooks and ToolGateway."""

    def __init__(
        self,
        *,
        gateway: ActionGateway,
        profile: AgentProfile,
        permission_mode: PermissionMode,
        run_id: str,
        thread_id: str,
        deps: Any,
    ) -> None:
        self._gateway = gateway
        self._profile = profile
        self._permission_mode = permission_mode
        self._run_id = run_id
        self._thread_id = thread_id
        self._deps = deps
        self.records: list[dict[str, Any]] = []
        self.denied_actions: list[dict[str, Any]] = []

    def dispatch(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
    ) -> OperationDispatchResult:
        proposal = ToolCallProposal(
            tool_call_id=new_ulid(),
            tool_name=adapter.target,
            arguments=arguments,
            malformed=False,
            parse_error=None,
        )
        result = self._gateway.execute_tool_call(
            proposal,
            agent=self._profile,
            state=self._state(),
            runtime_deps=self._deps,
            max_attempts=self._max_attempts(adapter),
        )
        return self._normalize_result(adapter, proposal, result)

    def resume_approved(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        proposal: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> OperationDispatchResult:
        if proposal.get("tool_name") != adapter.target or proposal.get("arguments") != arguments:
            raise ValueError("reviewed proposal does not match the pending Operation")
        exact = cast(ToolCallProposal, dict(proposal))
        result = self._gateway.execute_approved_tool_call(
            exact,
            decision=decision,
            agent=self._profile,
            state=self._state(),
            runtime_deps=self._deps,
            max_attempts=self._max_attempts(adapter),
        )
        return self._normalize_result(adapter, exact, result)

    def record_rejection(
        self,
        adapter: OperationAdapter,
        arguments: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        self.denied_actions.append(
            {
                "fingerprint": compute_fingerprint(
                    {"tool": adapter.target, "args": arguments}
                ),
                "tool_name": adapter.target,
                "arguments": dict(arguments),
                "reason": reason,
                "decided_at": now_iso(),
            }
        )

    def _state(self) -> AgentState:
        return cast(
            AgentState,
            {
                "run_id": self._run_id,
                "root_run_id": self._run_id,
                "parent_run_id": None,
                "parent_thread_id": None,
                "thread_id": self._thread_id,
                "agent_name": self._profile["name"],
                "permission_mode": self._permission_mode,
                "task": {},
                "messages": [],
                "loaded_skills": [],
                "tool_calls": [],
                "denied_actions": self.denied_actions,
                "workspace_refs": [],
                "pending_approval": None,
                "draft_output": None,
                "final_output": None,
                "step_count": 0,
                "status": "running",
                "pending_trace_events": [],
                "step_records": [],
                "repair_used": 0,
            },
        )

    def _max_attempts(self, adapter: OperationAdapter) -> int:
        retry: Mapping[str, Any] = (
            self._gateway.registry.get(adapter.target).get("retry") or {}
        )
        configured = int(retry.get("max_attempts", 1))
        return adapter.effective_max_attempts(tool_retry_attempts=configured)

    def _normalize_result(
        self,
        adapter: OperationAdapter,
        proposal: ToolCallProposal,
        result: Any,
    ) -> OperationDispatchResult:
        dispatch_record = dict(result.record)
        dispatch_record["outcome"] = result.outcome
        dispatch_record["attempts"] = list(getattr(result, "attempts", ()))
        self.records.append(dispatch_record)
        if result.outcome == "executed":
            return OperationDispatchResult(outcome="completed", output=result.record["result"])
        if result.outcome == "interrupt":
            return OperationDispatchResult(
                outcome="waiting",
                output={"decision": result.decision, "proposal": dict(proposal)},
                error=result.decision["reason"] if result.decision else "human judgment required",
            )
        if (
            adapter.side_effect
            and result.error_message
            and "timeout" in result.error_message.lower()
        ):
            return OperationDispatchResult(outcome="uncertain", error=result.error_message)
        return OperationDispatchResult(
            outcome="failed",
            error=result.error_message or result.outcome,
        )


class WorkflowSessionAdapter:
    """Run and resume Agent-local Workflows without the historical Graph runtime."""

    def __init__(
        self,
        *,
        agents: Mapping[str, ModiAgent],
        tools: ToolRegistry,
        policy: Any,
        hooks: Any,
        model: Any,
        output: Any,
        checkpointer: BaseCheckpointSaver[Any],
        workspace: WorkspaceManager,
        memory: MemoryStore,
        memory_scope_keys: MemoryScopeKeys,
        max_steps: int,
    ) -> None:
        self._agents = dict(agents)
        self._tools = tools
        self._model = model
        self._output = output
        self._checkpointer = checkpointer
        self._workspace = workspace
        self._memory = memory
        self._scope_keys = memory_scope_keys
        self._max_steps = max_steps
        self._store = InMemoryWorkflowStore()
        self._gateway = ActionGateway(
            registry=tools,
            policy=policy,
            hooks=hooks,
            result_inline_limit_bytes=8192,
        )
        self._runs: dict[str, _RunContext] = {}
        self._threads: dict[str, str] = {}

    def run(self, request: RunTaskInput) -> RunTaskResponse:
        context, state = self._begin(request)
        final = self._advance(context, state)
        return self._response(context, final)

    def _begin(self, request: RunTaskInput) -> tuple[_RunContext, WorkflowState]:
        agent = self._agents[request.agent]
        workflow = select_workflow(agent.workflows, request.workflow_id)
        thread_id = request.thread_id or new_ulid()
        adapters = self._adapter_registry()
        validators = self._validator_registry(agent)
        contract = build_execution_contract(
            workflow=workflow,
            adapters=adapters,
            validators=validators,
            output_contract=cast(Mapping[str, Any], agent.output_contract or {"free_form": True}),
            capability_ceiling=set(self._tools.names()),
            limits={
                "max_transitions": max(1, len(workflow.nodes) * 4),
                "max_steps": self._max_steps,
            },
            protocol_version="workflow-v1",
        )
        profile = cast(AgentProfile, agent_to_profile(agent))
        planner = ModelStructuredPlanner(
            model=self._model,
            instruction=agent.instruction,
            tool_catalog={name: self._tools.get(name) for name in self._tools.names()},
            skill_instructions=[skill.profile["instruction"] for skill in agent.skills],
        )
        runtime = WorkflowRuntime(
            adapters=adapters,
            validators=validators,
            dispatcher=cast(Any, None),
            store=self._store,
            brain=DefaultBrain(planner),
            agent_profile=profile,
        )
        state = runtime.start(
            workflow=workflow,
            contract=contract,
            workflow_input=request.input,
        )
        self._workspace.create_run(state.run_id)
        self._materialize_inputs(state.run_id, request.inputs)
        deps = SimpleNamespace(
            workspace=self._workspace,
            memory=self._memory,
            memory_scope_keys=self._scope_keys.for_run(
                agent_name=agent.name,
                thread_id=thread_id,
            ),
        )
        configured_mode = agent.permission_profile.get("mode") if agent.permission_profile else None
        dispatcher = _GatewayDispatcher(
            gateway=self._gateway,
            profile=profile,
            permission_mode=request.permission_mode or configured_mode or "auto",
            run_id=state.run_id,
            thread_id=thread_id,
            deps=deps,
        )
        runtime.bind_dispatcher(dispatcher)
        context = _RunContext(
            thread_id=thread_id,
            agent=agent,
            workflow=workflow,
            contract=contract,
            runtime=runtime,
            dispatcher=dispatcher,
            traces=[],
        )
        self._runs[state.run_id] = context
        self._threads[thread_id] = state.run_id
        self._trace(
            context,
            state,
            "workflow_started",
            {
                "workflow_id": workflow.id,
                "start_node": state.current_node_id,
                "revision": state.revision,
            },
        )
        self._persist(context, state)
        return context, state

    def resume(self, *, thread_id: str, payload: dict[str, Any] | None = None) -> RunTaskResponse:
        context, state = self._load_thread(thread_id)
        if state.status == "waiting":
            previous = state
            pending = state.pending_operation
            state = context.runtime.resume_waiting(
                state.run_id,
                payload=payload or {},
                workflow=context.workflow,
                contract=context.contract,
            )
            self._record_execution_events(
                context,
                state,
                self._resume_progress_events(previous, state, pending),
            )
            self._persist(context, state)
        final = self._advance(context, state)
        return self._response(context, final)

    def respond_to_judgment(
        self,
        *,
        thread_id: str,
        judgment_id: str,
        kind: str,
        rationale: str | None = None,
        intent_updates: dict[str, Any] | None = None,
    ) -> RunTaskResponse:
        return self.resume(
            thread_id=thread_id,
            payload={
                "judgment_id": judgment_id,
                "kind": kind,
                "rationale": rationale,
                "intent_updates": intent_updates or {},
            },
        )

    def stream(self, request: RunTaskInput) -> Iterable[StreamEvent]:
        context, state = self._begin(request)
        yield self._event(
            context,
            state,
            "workflow_started",
            {"workflow_id": context.workflow.id, "start_node": state.current_node_id},
        )
        yield from self._stream_advance(context, state)

    async def astream(self, request: RunTaskInput) -> AsyncIterator[StreamEvent]:
        for event in self.stream(request):
            yield event

    async def astream_resume(
        self, *, thread_id: str, payload: dict[str, Any] | None = None
    ) -> AsyncIterator[StreamEvent]:
        context, state = self._load_thread(thread_id)
        if state.status == "waiting":
            previous = state
            pending = state.pending_operation
            state = context.runtime.resume_waiting(
                state.run_id,
                payload=payload or {},
                workflow=context.workflow,
                contract=context.contract,
            )
            events = self._resume_progress_events(previous, state, pending)
            self._record_execution_events(context, state, events)
            self._persist(context, state)
            for event_type, event_payload in events:
                yield self._event(context, state, event_type, event_payload)
        for event in self._stream_advance(context, state):
            yield event

    def get_state(self, thread_id: str) -> dict[str, Any] | None:
        try:
            _context, state = self._load_thread(thread_id)
        except KeyError:
            return None
        return self._state_snapshot(state)

    def read_trace(self, thread_id: str) -> Iterable[TraceEvent]:
        try:
            context, _state = self._load_thread(thread_id)
        except KeyError:
            return ()
        return tuple(context.traces)

    def _advance(self, context: _RunContext, state: WorkflowState) -> WorkflowState:
        final = state
        for pair in self._advance_states(context, state):
            final = pair[1]
        return final

    def _advance_states(
        self,
        context: _RunContext,
        state: WorkflowState,
    ) -> Iterable[tuple[WorkflowState, WorkflowState]]:
        ceiling = max(self._max_steps, len(context.workflow.nodes) * 4)
        iterations = 0
        while state.status == "running" and iterations < ceiling:
            previous = state
            pre_events = self._pre_execution_events(context, previous)
            self._record_execution_events(context, previous, pre_events)
            invocation_count = len(context.runtime.store.invocations(previous.run_id))
            state = context.runtime.advance(
                state.run_id,
                workflow=context.workflow,
                contract=context.contract,
            )
            iterations += 1
            post_events = self._post_execution_events(
                context,
                previous,
                state,
                invocation_count=invocation_count,
            )
            self._record_execution_events(context, state, post_events)
            self._persist(context, state)
            yield previous, state
        if state.status == "running":
            previous = state
            state = context.runtime.cancel(
                state.run_id, reason="session execution budget exhausted"
            )
            self._persist(context, state)
            yield previous, state
        self._record_terminal_trace(context, state)
        self._persist(context, state)

    def _stream_advance(
        self,
        context: _RunContext,
        state: WorkflowState,
    ) -> Iterable[StreamEvent]:
        final = state
        ceiling = max(self._max_steps, len(context.workflow.nodes) * 4)
        iterations = 0
        while final.status == "running" and iterations < ceiling:
            previous = final
            invocation_count = len(context.runtime.store.invocations(previous.run_id))
            pre_events = self._pre_execution_events(context, previous)
            self._record_execution_events(context, previous, pre_events)
            for event_type, event_payload in pre_events:
                yield self._event(context, previous, event_type, event_payload)
            final = context.runtime.advance(
                previous.run_id,
                workflow=context.workflow,
                contract=context.contract,
            )
            iterations += 1
            post_events = self._post_execution_events(
                context,
                previous,
                final,
                invocation_count=invocation_count,
            )
            self._record_execution_events(context, final, post_events)
            self._persist(context, final)
            for event_type, event_payload in post_events:
                yield self._event(context, final, event_type, event_payload)
        if final.status == "running":
            final = context.runtime.cancel(
                final.run_id, reason="session execution budget exhausted"
            )
        self._record_terminal_trace(context, final)
        self._persist(context, final)
        response = self._response(context, final)
        yield self._event(
            context,
            final,
            "terminal",
            {"response": response},
            terminal=response,
        )

    def _pre_execution_events(
        self,
        context: _RunContext,
        state: WorkflowState,
    ) -> list[tuple[str, dict[str, Any]]]:
        node = context.workflow.node(state.current_node_id)
        prior_steps = any(
            item["node_id"] == node.id and item["node_attempt"] == state.node_attempt
            for item in state.step_records
        )
        prior_invocations = any(
            item.node_id == node.id and item.node_attempt == state.node_attempt
            for item in context.runtime.store.invocations(state.run_id)
        )
        if prior_steps or prior_invocations:
            return []
        events = [
            (
                "node_started",
                {
                    "workflow_id": state.workflow_id,
                    "node_id": node.id,
                    "node_attempt": state.node_attempt,
                    "execution": node.execution,
                    "revision": state.revision,
                },
            )
        ]
        if node.execution == "operation":
            events.append(
                (
                    "operation_started",
                    {
                        "node_id": node.id,
                        "node_attempt": state.node_attempt,
                        "adapter_id": node.operation,
                    },
                )
            )
        return events

    def _post_execution_events(
        self,
        context: _RunContext,
        previous: WorkflowState,
        current: WorkflowState,
        *,
        invocation_count: int,
    ) -> list[tuple[str, dict[str, Any]]]:
        events: list[tuple[str, dict[str, Any]]] = []
        node = context.workflow.node(previous.current_node_id)
        invocations = context.runtime.store.invocations(current.run_id)
        new_invocations = invocations[invocation_count:]
        dispatch_record = context.dispatcher.records[-1] if new_invocations else {}
        for invocation in new_invocations:
            if node.execution == "autonomous":
                events.append(
                    (
                        "operation_started",
                        {
                            "node_id": invocation.node_id,
                            "node_attempt": invocation.node_attempt,
                            "invocation_id": invocation.id,
                            "adapter_id": invocation.adapter_id,
                        },
                    )
                )
            events.append(
                (
                    "operation_completed",
                    {
                        "node_id": invocation.node_id,
                        "node_attempt": invocation.node_attempt,
                        "invocation_id": invocation.id,
                        "adapter_id": invocation.adapter_id,
                        "status": invocation.status,
                        "tool_call_id": dispatch_record.get("tool_call_id"),
                        "decision": dispatch_record.get("decision"),
                        "outcome": dispatch_record.get("outcome"),
                        "attempts": dispatch_record.get("attempts", []),
                        "error": invocation.error,
                    },
                )
            )
        if len(current.step_records) > len(previous.step_records):
            record = current.step_records[-1]
            operation = record["decision"].get("operation")
            events.append(
                (
                    "step_completed",
                    {
                        "node_id": record["node_id"],
                        "node_attempt": record["node_attempt"],
                        "step_id": record["step_id"],
                        "step_index": record["index"],
                        "step_kind": record["step_kind"],
                        "status": record["status"],
                        "operation": operation.get("target") if operation else None,
                        "started_at": record["started_at"],
                        "finished_at": record["finished_at"],
                    },
                )
            )
            if operation is not None and operation.get("target") == "complete_node":
                feedback = record["state_delta"].get("completion_feedback")
                events.append(
                    (
                        "completion_rejected" if feedback else "completion_accepted",
                        {
                            "node_id": record["node_id"],
                            "node_attempt": record["node_attempt"],
                            "step_id": record["step_id"],
                            "feedback": feedback,
                        },
                    )
                )
        if current.transition_count > previous.transition_count:
            transition = current.transitions[-1]
            events.append(
                (
                    "node_completed",
                    {
                        "node_id": transition.source_node_id,
                        "node_attempt": transition.source_attempt,
                        "event": transition.event,
                        "target": transition.target,
                        "revision": current.revision,
                    },
                )
            )
        if current.status == "waiting" and current.pending_operation is not None:
            response = self._response(context, current)
            if current.pending_operation.kind == "judgment":
                events.append(
                    ("approval_request", dict(response.get("pending_judgment") or {}))
                )
            else:
                events.append(
                    (
                        "interaction_requested",
                        dict(response.get("pending_interaction") or {}),
                    )
                )
        return events

    @staticmethod
    def _resume_progress_events(
        previous: WorkflowState,
        current: WorkflowState,
        pending: PendingOperation | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        events: list[tuple[str, dict[str, Any]]] = [
            (
                "interaction_resolved",
                {
                    "request_id": pending.request_id if pending else None,
                    "node_id": pending.node_id if pending else previous.current_node_id,
                    "node_attempt": pending.node_attempt if pending else previous.node_attempt,
                    "status": current.status,
                },
            )
        ]
        if pending is not None and pending.adapter_id is not None:
            events.append(
                (
                    "operation_completed",
                    {
                        "node_id": pending.node_id,
                        "node_attempt": pending.node_attempt,
                        "invocation_id": pending.invocation_id,
                        "adapter_id": pending.adapter_id,
                        "status": current.status,
                    },
                )
            )
        if previous.step_records != current.step_records and pending is not None:
            resolved = next(
                (item for item in current.step_records if item["step_id"] == pending.step_id),
                None,
            )
            if resolved is not None:
                events.append(
                    (
                        "step_completed",
                        {
                            "node_id": resolved["node_id"],
                            "node_attempt": resolved["node_attempt"],
                            "step_id": resolved["step_id"],
                            "step_index": resolved["index"],
                            "step_kind": resolved["step_kind"],
                            "status": resolved["status"],
                        },
                    )
                )
        if current.transition_count > previous.transition_count:
            transition = current.transitions[-1]
            events.append(
                (
                    "node_completed",
                    {
                        "node_id": transition.source_node_id,
                        "node_attempt": transition.source_attempt,
                        "event": transition.event,
                        "target": transition.target,
                        "revision": current.revision,
                    },
                )
            )
        return events

    def _record_execution_events(
        self,
        context: _RunContext,
        state: WorkflowState,
        events: Iterable[tuple[str, dict[str, Any]]],
    ) -> None:
        for event_type, payload in events:
            self._trace(context, state, event_type, payload)

    def _record_terminal_trace(self, context: _RunContext, state: WorkflowState) -> None:
        event_types = {
            "completed": "workflow_completed",
            "failed": "workflow_failed",
            "cancelled": "workflow_cancelled",
            "reconciliation_required": "workflow_reconciliation_required",
        }
        event_type = event_types.get(state.status)
        if event_type is None:
            return
        if any(
            item["event_type"] == event_type
            and item["payload"].get("revision") == state.revision
            for item in context.traces
        ):
            return
        self._trace(
            context,
            state,
            event_type,
            {
                "workflow_id": state.workflow_id,
                "status": state.status,
                "revision": state.revision,
                "failure": state.failure,
            },
        )

    def _event(
        self,
        context: _RunContext,
        state: WorkflowState,
        event_type: str,
        payload: dict[str, Any],
        *,
        terminal: RunTaskResponse | None = None,
    ) -> StreamEvent:
        context.sequence += 1
        return cast(
            StreamEvent,
            {
                "event_type": event_type,
                "sequence": context.sequence,
                "run_id": state.run_id,
                "thread_id": context.thread_id,
                "payload": payload,
                "terminal_response": terminal,
            },
        )

    def _adapter_registry(self) -> OperationAdapterRegistry:
        registry = OperationAdapterRegistry()
        for name in self._tools.names():
            spec = self._tools.get(name)
            side_effect = bool(spec.get("side_effect", False))
            registry.register(
                OperationAdapter(
                    id=name,
                    version="1",
                    kind=("memory_write" if name in {"save_memory", "propose_memory"} else "tool"),
                    target=name,
                    node_selectable=True,
                    required_capabilities=(),
                    side_effect=side_effect,
                    recovery_mode=(
                        "provider_idempotent"
                        if spec.get("idempotent")
                        else "manual_reconciliation"
                        if side_effect
                        else "pure"
                    ),
                    input_schema=dict(spec.get("input_schema") or {"type": "object"}),
                    output_schema=dict(spec.get("output_schema") or {}),
                    max_calls_per_node=spec.get("max_calls_per_node"),
                )
            )
        return registry

    @staticmethod
    def _validator_registry(agent: ModiAgent) -> CompletionValidatorRegistry:
        registry = CompletionValidatorRegistry()
        for validator in agent.completion_validators:
            registry.register(validator)
        return registry

    def _response(self, context: _RunContext, state: WorkflowState) -> RunTaskResponse:
        status_map = {
            "running": "failed",
            "waiting": "interrupted",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "reconciliation_required": "blocked",
        }
        output = state.output
        if output is not None and not isinstance(output, Mapping):
            output = {"value": output}
        error = {"message": state.failure} if state.failure else None
        pending_approval = None
        pending_judgment = None
        pending_interaction = None
        pending = state.pending_operation
        if pending is not None and pending.kind == "judgment":
            pending_judgment = {
                "judgment_id": pending.request_id,
                "approval_id": pending.request_id,
                "tool_call_id": pending.proposal.get("tool_call_id"),
                "target_action_id": pending.id,
                "reviewed_action_hash": compute_fingerprint(
                    {"adapter": pending.adapter_id, "arguments": _plain(pending.arguments)}
                ),
                "prompt": str(
                    pending.decision.get("reason")
                    or pending.proposal.get("prompt")
                    or "Review the pending Operation"
                ),
                "allowed_kinds": ["approve", "reject", "cancel"],
                "proposed_intent_patch": None,
                "summary": str(
                    pending.proposal.get("summary")
                    or pending.proposal.get("prompt")
                    or pending.adapter_id
                    or "human judgment"
                ),
                "rationale": None,
                "risk_level": str(pending.decision.get("risk_level") or "unknown"),
                "trigger": "operation_risk",
                "requested_at": str(pending.decision.get("requested_at") or now_iso()),
            }
            pending_approval = {
                "approval_id": pending.request_id,
                "tool_call_id": str(pending.proposal.get("tool_call_id") or ""),
                "decision": str(pending.decision.get("decision") or "require_review"),
                "summary": pending_judgment["summary"],
                "risk_level": pending_judgment["risk_level"],
                "requested_at": pending_judgment["requested_at"],
            }
        elif pending is not None:
            pending_interaction = {
                "interaction_id": pending.request_id,
                "kind": "user_input",
                "prompt": str(pending.proposal.get("prompt") or "Input required"),
                "payload": _plain(pending.proposal),
                "tool_call_id": None,
            }
        return cast(
            RunTaskResponse,
            {
                "run_id": state.run_id,
                "thread_id": context.thread_id,
                "status": status_map[state.status],
                "output": dict(output) if isinstance(output, Mapping) else None,
                "pending_approval": pending_approval,
                "pending_judgment": pending_judgment,
                "pending_interaction": pending_interaction,
                "error": error,
            },
        )

    def _trace(
        self,
        context: _RunContext,
        state: WorkflowState,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = TraceEvent(
            event_id=new_ulid(),
            run_id=state.run_id,
            root_run_id=state.run_id,
            parent_run_id=None,
            thread_id=context.thread_id,
            timestamp=now_iso(),
            event_type=event_type,
            payload=payload,
            payload_ref=None,
        )
        context.traces.append(event)
        self._workspace.append_log(state.run_id, "trace", json.dumps(event, ensure_ascii=False))

    def _materialize_inputs(
        self,
        run_id: str,
        inputs: list[RunInputFile | dict[str, Any]],
    ) -> list[WorkspaceRef]:
        refs: list[WorkspaceRef] = []
        for raw in inputs:
            item = raw if isinstance(raw, RunInputFile) else RunInputFile(**raw)
            data = item.data
            if isinstance(data, bytes):
                payload = data
            elif isinstance(data, str):
                payload = data.encode("utf-8")
            else:
                payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            refs.append(
                self._workspace.save_input(
                    run_id,
                    item.name,
                    payload,
                    trust=item.trust,
                    mime_type=item.mime_type,
                    metadata=item.metadata,
                )
            )
        return refs

    def _load_thread(self, thread_id: str) -> tuple[_RunContext, WorkflowState]:
        run_id = self._threads.get(thread_id)
        if run_id is not None:
            context = self._runs[run_id]
            return context, self._store.get(run_id)
        checkpoint = self._checkpointer.get_tuple(self._config(thread_id))
        if checkpoint is None:
            raise KeyError(thread_id)
        raw = checkpoint.checkpoint["channel_values"].get(_CHECKPOINT_CHANNEL)
        if not isinstance(raw, Mapping):
            raise KeyError(thread_id)
        agent_name = str(raw["agent_name"])
        agent = self._agents[agent_name]
        workflow = select_workflow(agent.workflows, str(raw["workflow_id"]))
        adapters = self._adapter_registry()
        validators = self._validator_registry(agent)
        contract = build_execution_contract(
            workflow=workflow,
            adapters=adapters,
            validators=validators,
            output_contract=cast(Mapping[str, Any], agent.output_contract or {"free_form": True}),
            capability_ceiling=set(self._tools.names()),
            limits={
                "max_transitions": max(1, len(workflow.nodes) * 4),
                "max_steps": self._max_steps,
            },
            protocol_version="workflow-v1",
        )
        state = self._restore_state(cast(Mapping[str, Any], raw["state"]))
        self._store.create(state)
        for item in raw.get("invocations") or ():
            self._store.restore_invocation(
                InvocationRecord(
                    id=str(item["id"]),
                    run_id=str(item["run_id"]),
                    node_id=str(item["node_id"]),
                    node_attempt=int(item["node_attempt"]),
                    adapter_id=str(item["adapter_id"]),
                    arguments=MappingProxyType(dict(item.get("arguments") or {})),
                    workflow_revision=int(item["workflow_revision"]),
                    status=cast(Any, item["status"]),
                    output=item.get("output"),
                    error=cast(str | None, item.get("error")),
                )
            )
        profile = cast(AgentProfile, agent_to_profile(agent))
        dispatcher = _GatewayDispatcher(
            gateway=self._gateway,
            profile=profile,
            permission_mode=cast(PermissionMode, raw.get("permission_mode") or "auto"),
            run_id=state.run_id,
            thread_id=thread_id,
            deps=SimpleNamespace(
                workspace=self._workspace,
                memory=self._memory,
                memory_scope_keys=self._scope_keys.for_run(
                    agent_name=agent.name, thread_id=thread_id
                ),
            ),
        )
        dispatcher.records.extend(
            dict(item) for item in raw.get("operation_records") or ()
        )
        dispatcher.denied_actions.extend(
            dict(item) for item in raw.get("denied_actions") or ()
        )
        runtime = WorkflowRuntime(
            adapters=adapters,
            validators=validators,
            dispatcher=dispatcher,
            store=self._store,
            brain=DefaultBrain(
                ModelStructuredPlanner(
                    model=self._model,
                    instruction=agent.instruction,
                    tool_catalog={name: self._tools.get(name) for name in self._tools.names()},
                    skill_instructions=[
                        skill.profile["instruction"] for skill in agent.skills
                    ],
                )
            ),
            agent_profile=profile,
        )
        context = _RunContext(
            thread_id=thread_id,
            agent=agent,
            workflow=workflow,
            contract=contract,
            runtime=runtime,
            dispatcher=dispatcher,
            traces=list(raw.get("traces") or []),
            sequence=int(raw.get("sequence") or 0),
        )
        self._runs[state.run_id] = context
        self._threads[thread_id] = state.run_id
        return context, state

    def _persist(self, context: _RunContext, state: WorkflowState) -> None:
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {
            _CHECKPOINT_CHANNEL: {
                "agent_name": context.agent.name,
                "workflow_id": context.workflow.id,
                "permission_mode": context.dispatcher._permission_mode,
                "state": self._state_snapshot(state),
                "invocations": [
                    {
                        "id": item.id,
                        "run_id": item.run_id,
                        "node_id": item.node_id,
                        "node_attempt": item.node_attempt,
                        "adapter_id": item.adapter_id,
                        "arguments": _plain(item.arguments),
                        "workflow_revision": item.workflow_revision,
                        "status": item.status,
                        "output": _plain(item.output),
                        "error": item.error,
                    }
                    for item in context.runtime.store.invocations(state.run_id)
                ],
                "operation_records": _plain(context.dispatcher.records),
                "denied_actions": _plain(context.dispatcher.denied_actions),
                "traces": _plain(context.traces),
                "sequence": context.sequence,
            }
        }
        checkpoint["channel_versions"] = {_CHECKPOINT_CHANNEL: str(state.revision)}
        self._checkpointer.put(
            self._config(context.thread_id),
            checkpoint,
            {"source": "update", "step": state.revision, "parents": {}},
            {_CHECKPOINT_CHANNEL: str(state.revision)},
        )

    @staticmethod
    def _config(thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": "workflow"}}

    @staticmethod
    def _state_snapshot(state: WorkflowState) -> dict[str, Any]:
        return {
            "run_id": state.run_id,
            "workflow_id": state.workflow_id,
            "definition_fingerprint": state.definition_fingerprint,
            "execution_contract_fingerprint": state.execution_contract_fingerprint,
            "workflow_input": _plain(state.workflow_input),
            "status": state.status,
            "current_node_id": state.current_node_id,
            "node_attempt": state.node_attempt,
            "revision": state.revision,
            "transition_count": state.transition_count,
            "node_outputs": _plain(state.node_outputs),
            "transitions": [
                {
                    "source_node_id": item.source_node_id,
                    "source_attempt": item.source_attempt,
                    "event": item.event,
                    "target": item.target,
                }
                for item in state.transitions
            ],
            "output": _plain(state.output),
            "failure": state.failure,
            "cancellation_requested": state.cancellation_requested,
            "loop_state": _plain(state.loop_state),
            "step_records": _plain(state.step_records),
            "task_plan": _plain(state.task_plan),
            "pending_operation": (
                {
                    "id": state.pending_operation.id,
                    "kind": state.pending_operation.kind,
                    "source": state.pending_operation.source,
                    "node_id": state.pending_operation.node_id,
                    "node_attempt": state.pending_operation.node_attempt,
                    "request_id": state.pending_operation.request_id,
                    "step_id": state.pending_operation.step_id,
                    "invocation_id": state.pending_operation.invocation_id,
                    "adapter_id": state.pending_operation.adapter_id,
                    "arguments": _plain(state.pending_operation.arguments),
                    "proposal": _plain(state.pending_operation.proposal),
                    "decision": _plain(state.pending_operation.decision),
                }
                if state.pending_operation is not None
                else None
            ),
            "human_inputs": _plain(state.human_inputs),
        }

    @staticmethod
    def _restore_state(raw: Mapping[str, Any]) -> WorkflowState:
        return WorkflowState(
            run_id=str(raw["run_id"]),
            workflow_id=str(raw["workflow_id"]),
            definition_fingerprint=str(raw["definition_fingerprint"]),
            execution_contract_fingerprint=str(raw["execution_contract_fingerprint"]),
            workflow_input=MappingProxyType(dict(raw["workflow_input"])),
            status=cast(Any, raw["status"]),
            current_node_id=str(raw["current_node_id"]),
            node_attempt=int(raw["node_attempt"]),
            revision=int(raw["revision"]),
            transition_count=int(raw["transition_count"]),
            node_outputs=MappingProxyType(dict(raw["node_outputs"])),
            transitions=tuple(TransitionRecord(**item) for item in raw["transitions"]),
            output=raw.get("output"),
            failure=cast(str | None, raw.get("failure")),
            cancellation_requested=bool(raw.get("cancellation_requested", False)),
            loop_state=cast(Any, raw.get("loop_state")),
            step_records=tuple(cast(Any, item) for item in raw.get("step_records", ())),
            task_plan=cast(Any, raw.get("task_plan")),
            pending_operation=(
                PendingOperation(
                    id=str(raw["pending_operation"]["id"]),
                    kind=cast(Any, raw["pending_operation"]["kind"]),
                    source=cast(Any, raw["pending_operation"]["source"]),
                    node_id=str(raw["pending_operation"]["node_id"]),
                    node_attempt=int(raw["pending_operation"]["node_attempt"]),
                    request_id=str(raw["pending_operation"]["request_id"]),
                    step_id=cast(str | None, raw["pending_operation"].get("step_id")),
                    invocation_id=cast(
                        str | None, raw["pending_operation"].get("invocation_id")
                    ),
                    adapter_id=cast(
                        str | None, raw["pending_operation"].get("adapter_id")
                    ),
                    arguments=MappingProxyType(
                        dict(raw["pending_operation"].get("arguments") or {})
                    ),
                    proposal=MappingProxyType(
                        dict(raw["pending_operation"].get("proposal") or {})
                    ),
                    decision=MappingProxyType(
                        dict(raw["pending_operation"].get("decision") or {})
                    ),
                )
                if isinstance(raw.get("pending_operation"), Mapping)
                else None
            ),
            human_inputs=MappingProxyType(dict(raw.get("human_inputs") or {})),
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


__all__ = ["RunInputFile", "RunTaskInput", "WorkflowSessionAdapter"]
