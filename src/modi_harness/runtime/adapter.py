"""Runtime Adapter implementation.

Orchestrates the single-agent loop. V0.1 uses a hand-rolled state machine
that mirrors the LangGraph node structure described in
``docs/architecture/04-runtime-adapter.md``. A LangGraph wiring lives in
``modi_harness.graph`` and shares the same step functions; tests today drive
the hand-rolled loop because LangGraph's checkpointer adds complexity
unnecessary for V0.1 acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .._utils import compute_fingerprint, new_ulid, now_iso
from ..agents import AgentLoader
from ..context import ContextManager
from ..hooks import HookDispatcher
from ..memory import MemoryStore
from ..models import ModelAdapter
from ..output import OutputController
from ..policy import PolicyGate
from ..skills import SkillLoader
from ..tools import ToolDispatchResult, ToolGateway
from ..trace import TraceRecorder
from ..types import (
    AgentProfile,
    AgentState,
    DeniedAction,
    LoadedSkill,
    Message,
    PendingApproval,
    PermissionMode,
    RunTaskResponse,
    ToolCallProposal,
    ToolCallRecord,
)
from ..workspace import WorkspaceManager


@dataclass
class RunTaskInput:
    agent: str
    input: dict[str, Any]
    options: dict[str, Any] = field(default_factory=dict)
    permission_mode: PermissionMode | None = None
    thread_id: str | None = None
    parent_run_id: str | None = None


class RuntimeAdapter:
    """Single-agent loop coordinator."""

    def __init__(
        self,
        *,
        agent_loader: AgentLoader,
        skill_loader: SkillLoader | None,
        memory_store: MemoryStore,
        workspace: WorkspaceManager,
        context_manager: ContextManager,
        model_adapter: ModelAdapter,
        tool_gateway: ToolGateway,
        policy: PolicyGate,
        output_controller: OutputController,
        hooks: HookDispatcher,
        max_steps: int = 20,
        repair_budget: int = 3,
        trace_redact_keys: Iterable[str] = ("api_key", "authorization", "password", "secret"),
        trace_payload_inline_limit_bytes: int = 2048,
    ) -> None:
        self._agents = agent_loader
        self._skills = skill_loader
        self._memory = memory_store
        self._workspace = workspace
        self._context = context_manager
        self._model = model_adapter
        self._tools = tool_gateway
        self._policy = policy
        self._output = output_controller
        self._hooks = hooks
        self._max_steps = max_steps
        self._repair_budget = repair_budget
        self._trace_redact_keys = set(trace_redact_keys)
        self._trace_payload_limit = trace_payload_inline_limit_bytes
        # Persisted state per active run id (in-memory; workspace holds durable copy).
        self._runs: dict[str, _RunContext] = {}

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def run(self, request: RunTaskInput) -> RunTaskResponse:
        run_id = new_ulid()
        ctx = self._init_run(run_id, request)
        self._runs[run_id] = ctx
        ctx.trace.record("run_start", {"agent": request.agent, "input": request.input})
        return self._loop(ctx)

    def approve(self, *, run_id: str, approval_id: str, decision: str) -> RunTaskResponse:
        ctx = self._runs.get(run_id)
        if ctx is None:
            return _failed_response(run_id, "unknown_run", "no active run for approval")
        if ctx.state["pending_approval"] is None or ctx.state["pending_approval"]["approval_id"] != approval_id:
            return _failed_response(run_id, "no_pending_approval", "approval id does not match")
        if decision != "approved":
            return self.reject(run_id=run_id, approval_id=approval_id, reason=f"decision={decision}")

        # Re-execute the pending tool call now that policy authorization is granted.
        pending = ctx.state["pending_approval"]
        ctx.trace.record("approval_granted", {"approval_id": approval_id})
        proposal = ctx.pending_proposal
        ctx.state["pending_approval"] = None
        ctx.pending_proposal = None
        if proposal is None:
            return _failed_response(run_id, "lost_proposal", "approved without recoverable proposal")
        # Force the tool call by injecting a synthetic state where the call passes Policy.
        # Simpler: bypass policy by routing into a mode-elevated execution.
        ctx.state["permission_mode"] = "bypass"
        try:
            result = self._tools.execute_tool_call(
                proposal,
                agent=ctx.agent,
                state=ctx.state,
            )
        finally:
            ctx.state["permission_mode"] = ctx.original_mode
        self._handle_tool_result(ctx, proposal, result)
        return self._loop(ctx)

    def reject(self, *, run_id: str, approval_id: str, reason: str) -> RunTaskResponse:
        ctx = self._runs.get(run_id)
        if ctx is None:
            return _failed_response(run_id, "unknown_run", "no active run for rejection")
        if ctx.state["pending_approval"] is None or ctx.state["pending_approval"]["approval_id"] != approval_id:
            return _failed_response(run_id, "no_pending_approval", "approval id does not match")
        proposal = ctx.pending_proposal
        ctx.state["pending_approval"] = None
        ctx.pending_proposal = None
        if proposal is not None:
            denied = DeniedAction(
                fingerprint=compute_fingerprint({"tool": proposal["tool_name"], "args": proposal["arguments"]}),
                tool_name=proposal["tool_name"],
                arguments=proposal["arguments"],
                reason=reason,
                decided_at=now_iso(),
            )
            ctx.state["denied_actions"].append(denied)
            ctx.trace.record("denial", {"approval_id": approval_id, "reason": reason, "fingerprint": denied["fingerprint"]})
        return self._loop(ctx)

    def read_trace(self, run_id: str) -> Iterable[dict[str, Any]]:
        ctx = self._runs.get(run_id)
        if ctx is None:
            return []
        return ctx.trace.read_trace()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _init_run(self, run_id: str, request: RunTaskInput) -> "_RunContext":
        self._workspace.create_run(run_id)
        agent = self._agents.load_agent(request.agent)
        skills = self._load_active_skills(agent)
        permission_mode = (
            request.permission_mode
            or (agent["permission_profile"] or {}).get("mode")
            or "ask"
        )
        state: AgentState = {
            "run_id": run_id,
            "root_run_id": run_id,
            "parent_run_id": request.parent_run_id,
            "thread_id": request.thread_id,
            "agent_name": agent["name"],
            "permission_mode": permission_mode,
            "task": request.input,
            "messages": [
                Message(role="user", content=_input_to_user_text(request.input), tool_call_id=None, metadata={})
            ],
            "loaded_skills": [s["name"] for s in skills],
            "tool_calls": [],
            "denied_actions": [],
            "workspace_refs": [],
            "pending_approval": None,
            "draft_output": None,
            "final_output": None,
            "step_count": 0,
            "status": "running",
        }
        trace = TraceRecorder(
            workspace=self._workspace,
            run_id=run_id,
            root_run_id=run_id,
            parent_run_id=None,
            thread_id=request.thread_id,
            redact_keys=self._trace_redact_keys,
            payload_inline_limit_bytes=self._trace_payload_limit,
        )
        return _RunContext(
            run_id=run_id,
            state=state,
            agent=agent,
            skills=skills,
            trace=trace,
            original_mode=permission_mode,
            pending_proposal=None,
        )

    def _load_active_skills(self, agent: AgentProfile) -> list[LoadedSkill]:
        if not self._skills or not agent["default_skills"]:
            return []
        return self._skills.load_skills(agent["default_skills"])

    def _loop(self, ctx: "_RunContext") -> RunTaskResponse:
        while True:
            if ctx.state["step_count"] >= self._max_steps:
                ctx.state["status"] = "failed"
                ctx.trace.record("error", {"code": "max_steps_exhausted"})
                return _response(ctx, error={"code": "max_steps_exhausted", "message": "step limit reached"})

            ctx.state["step_count"] += 1
            self._workspace.snapshot_state(ctx.run_id, ctx.state["step_count"], ctx.state)

            # Build context.
            workspace_index = self._workspace.index_workspace(ctx.run_id)
            memory_index = self._memory.load_index(["user", "agent", "project", "conversation"])
            tool_catalog = {
                name: self._tools._registry.get(name)
                for name in ctx.agent["default_tools"]
                if self._tools._registry.has(name)
            }
            pack = self._context.build_context(
                state=ctx.state,
                agent=ctx.agent,
                skills=ctx.skills,
                memory_index=memory_index,
                workspace_index=workspace_index,
                tool_catalog=tool_catalog,
                output_contract=ctx.agent["output_contract"],
            )
            ctx.trace.record("context_built", {"context_hash": pack["context_hash"]})

            # Model step.
            ctx.trace.record("model_call", {"step": ctx.state["step_count"]})
            result = self._model.call(pack)
            ctx.trace.record("model_result", {"finish_reason": result["finish_reason"]})

            # Append assistant message.
            ctx.state["messages"].append(result["message"])

            # Route: tool call or final output?
            if result["tool_calls"]:
                # Pick the first call (V0.1 single tool per turn).
                proposal = result["tool_calls"][0]
                if proposal["malformed"]:
                    if not self._consume_repair(ctx):
                        ctx.state["status"] = "failed"
                        ctx.trace.record("error", {"code": "repair_budget_exhausted"})
                        return _response(ctx, error={"code": "repair_budget_exhausted", "message": "malformed tool calls"})
                    continue
                tool_result = self._tools.execute_tool_call(
                    proposal,
                    agent=ctx.agent,
                    state=ctx.state,
                )
                outcome = self._handle_tool_result(ctx, proposal, tool_result)
                if outcome == "interrupt":
                    return _response(ctx)
                # else continue loop
                continue

            # No tool calls → treat content as draft output.
            draft = result["message"]["content"]
            ctx.state["draft_output"] = {"value": draft}
            validation = self._output.validate(draft, _free_form_or(ctx.agent["output_contract"]), ctx.state)
            ctx.trace.record("output_validation", {"status": validation["status"], "issues": validation["issues"]})
            if validation["status"] in ("validated", "final"):
                ctx.state["final_output"] = validation["output"]
                ctx.state["status"] = "completed"
                ctx.trace.record("run_end", {"status": "completed"})
                return _response(ctx)
            if validation["status"] == "needs_review":
                ctx.state["status"] = "blocked"
                ctx.trace.record("run_end", {"status": "blocked"})
                return _response(ctx)
            # rejected → repair
            if not self._consume_repair(ctx):
                ctx.state["status"] = "failed"
                ctx.trace.record("error", {"code": "repair_budget_exhausted"})
                return _response(ctx, error={"code": "repair_budget_exhausted", "message": "output validation failed"})

    def _handle_tool_result(
        self,
        ctx: "_RunContext",
        proposal: ToolCallProposal,
        result: ToolDispatchResult,
    ) -> str:
        ctx.state["tool_calls"].append(result.record)
        ctx.trace.record(
            "tool_result",
            {
                "tool_call_id": result.record["tool_call_id"],
                "tool_name": result.record["tool_name"],
                "decision": result.record["decision"],
                "outcome": result.outcome,
            },
        )

        if result.outcome == "interrupt" and result.decision is not None:
            decision = result.decision
            ctx.state["pending_approval"] = PendingApproval(
                approval_id=decision["approval_id"] or new_ulid(),
                tool_call_id=result.record["tool_call_id"],
                decision=decision["decision"],  # type: ignore[arg-type]
                summary=f"{result.record['tool_name']}({result.record['arguments']})",
                risk_level=(self._tools._registry.get(result.record["tool_name"]) or {}).get("risk_level", ""),
                requested_at=now_iso(),
            )
            ctx.pending_proposal = proposal
            ctx.state["status"] = "interrupted"
            ctx.trace.record("approval_request", {"approval_id": ctx.state["pending_approval"]["approval_id"]})
            return "interrupt"

        if result.outcome == "denied_retry":
            ctx.trace.record("denial", {"reason": "denied_retry", "tool_name": result.record["tool_name"]})

        if result.outcome == "executed":
            # Append a tool message so the next model step sees the result.
            ctx.state["messages"].append(
                Message(
                    role="tool",
                    content=str(result.record["result"]),
                    tool_call_id=result.record["tool_call_id"],
                    metadata={},
                )
            )
        elif result.outcome in ("error", "hook_blocked", "denied_retry"):
            err_text = result.error_message or f"tool {result.record['tool_name']} {result.outcome}"
            ctx.state["messages"].append(
                Message(role="tool", content=err_text, tool_call_id=result.record["tool_call_id"], metadata={})
            )

        return "continue"

    def _consume_repair(self, ctx: "_RunContext") -> bool:
        ctx.repair_used += 1
        return ctx.repair_used <= self._repair_budget


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


@dataclass
class _RunContext:
    run_id: str
    state: AgentState
    agent: AgentProfile
    skills: list[LoadedSkill]
    trace: TraceRecorder
    original_mode: PermissionMode
    pending_proposal: ToolCallProposal | None
    repair_used: int = 0


def _input_to_user_text(payload: dict[str, Any]) -> str:
    if "messages" in payload and isinstance(payload["messages"], list):
        # Conversation-style input: take the last user message.
        for msg in reversed(payload["messages"]):
            if isinstance(msg, dict) and msg.get("role") == "user":
                return str(msg.get("content", ""))
    if "customer_message" in payload:
        return str(payload["customer_message"])
    if "question" in payload:
        return str(payload["question"])
    if "goal" in payload:
        return str(payload["goal"])
    return str(payload)


def _free_form_or(contract: Any) -> Any:
    if contract is None:
        return {
            "schema": None,
            "required_fields": [],
            "citation_required": False,
            "risk_label_required": False,
            "forbidden_patterns": [],
            "review_required": False,
            "free_form": True,
        }
    return contract


def _response(ctx: _RunContext, *, error: dict[str, Any] | None = None) -> RunTaskResponse:
    return RunTaskResponse(  # type: ignore[typeddict-item]
        run_id=ctx.run_id,
        thread_id=ctx.state["thread_id"],
        status=ctx.state["status"],  # type: ignore[arg-type]
        output=ctx.state["final_output"] or (ctx.state["draft_output"] if ctx.state["status"] in ("blocked", "interrupted") else None),
        pending_approval=ctx.state["pending_approval"],
        error=error,
    )


def _failed_response(run_id: str, code: str, message: str) -> RunTaskResponse:
    return RunTaskResponse(  # type: ignore[typeddict-item]
        run_id=run_id,
        thread_id=None,
        status="failed",
        output=None,
        pending_approval=None,
        error={"code": code, "message": message},
    )
