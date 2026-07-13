"""Public-session adapter for the mandatory Workflow runtime."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType, SimpleNamespace
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint

from .._utils import new_ulid, now_iso
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
    CompletionValidator,
    CompletionValidatorRegistry,
    ExecutionContract,
    OperationAdapter,
    OperationAdapterRegistry,
    build_execution_contract,
)
from .router import select_workflow
from .runtime import (
    InMemoryWorkflowStore,
    OperationDispatchResult,
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
        state = cast(
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
        result = self._gateway.execute_tool_call(
            proposal,
            agent=self._profile,
            state=state,
            runtime_deps=self._deps,
        )
        self.records.append(dict(result.record))
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
        agent = self._agents[request.agent]
        workflow = select_workflow(agent.workflows, request.workflow_id)
        thread_id = request.thread_id or new_ulid()
        adapters = self._adapter_registry()
        validators = self._validator_registry(workflow)
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
        self._trace(context, state, "run_start", {"workflow_id": workflow.id})
        final = self._advance(context, state)
        return self._response(context, final)

    def resume(self, *, thread_id: str, payload: dict[str, Any] | None = None) -> RunTaskResponse:
        context, state = self._load_thread(thread_id)
        if state.status == "waiting":
            state = context.runtime.resume_waiting(state.run_id, payload=payload or {})
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
        response = self.run(request)
        yield self._terminal_event(response)

    async def astream(self, request: RunTaskInput) -> AsyncIterator[StreamEvent]:
        for event in self.stream(request):
            yield event

    async def astream_resume(
        self, *, thread_id: str, payload: dict[str, Any] | None = None
    ) -> AsyncIterator[StreamEvent]:
        yield self._terminal_event(self.resume(thread_id=thread_id, payload=payload))

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
        ceiling = max(self._max_steps, len(context.workflow.nodes) * 4)
        iterations = 0
        while state.status == "running" and iterations < ceiling:
            previous = state
            state = context.runtime.advance(
                state.run_id,
                workflow=context.workflow,
                contract=context.contract,
            )
            iterations += 1
            self._trace(
                context,
                state,
                "state_transition",
                {
                    "from_node": previous.current_node_id,
                    "to_node": state.current_node_id,
                    "status": state.status,
                    "revision": state.revision,
                },
            )
        if state.status == "running":
            state = context.runtime.cancel(
                state.run_id, reason="session execution budget exhausted"
            )
        if state.status in {"completed", "failed", "cancelled", "reconciliation_required"}:
            self._trace(context, state, "run_end", {"status": state.status})
        self._persist(context, state)
        return state

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
                )
            )
        return registry

    @staticmethod
    def _validator_registry(workflow: Workflow) -> CompletionValidatorRegistry:
        registry = CompletionValidatorRegistry()
        for validator_id in sorted(
            {node.completion_validator for node in workflow.nodes if node.completion_validator}
        ):
            registry.register(
                CompletionValidator(
                    id=validator_id,
                    version="1",
                    validate=lambda _value: True,
                )
            )
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
        return cast(
            RunTaskResponse,
            {
                "run_id": state.run_id,
                "thread_id": context.thread_id,
                "status": status_map[state.status],
                "output": dict(output) if isinstance(output, Mapping) else None,
                "pending_approval": None,
                "pending_judgment": None,
                "pending_interaction": None,
                "error": error,
            },
        )

    @staticmethod
    def _terminal_event(response: RunTaskResponse) -> StreamEvent:
        return cast(
            StreamEvent,
            {
                "event_type": "terminal",
                "sequence": 1,
                "run_id": response["run_id"],
                "thread_id": response["thread_id"],
                "payload": {"response": response},
                "terminal_response": response,
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
        validators = self._validator_registry(workflow)
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
                "traces": _plain(context.traces),
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
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


__all__ = ["RunInputFile", "RunTaskInput", "WorkflowSessionAdapter"]
