"""Runtime Adapter — thin wrapper over the V0.2 LangGraph runtime.

V0.1 ran a hand-rolled state machine; V0.2 delegates to a compiled LangGraph
graph backed by a checkpointer. This module owns:

- Seeding the initial :class:`MainGraphState`.
- Calling ``graph.invoke`` / ``Command(resume=)`` with the right
  ``RunnableConfig``.
- Flushing accumulated trace events to disk via :class:`TraceMiddleware`.
- Translating the final state into :class:`RunTaskResponse`.

It does not own approval bookkeeping, run-id dictionaries, or repair loops —
those moved into the graph itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from .._utils import new_ulid
from ..graph import CONFIG_DEPS_KEY, GraphDeps, TraceMiddleware, build_main_graph
from ..types import (
    AgentState,
    Message,
    PermissionMode,
    RunTaskResponse,
    TraceEvent,
)


@dataclass
class RunTaskInput:
    agent: str
    input: dict[str, Any]
    options: dict[str, Any] = field(default_factory=dict)
    permission_mode: PermissionMode | None = None
    thread_id: str | None = None
    parent_run_id: str | None = None
    parent_thread_id: str | None = None


class RuntimeAdapter:
    """Wires a compiled LangGraph runtime and runs/resumes threads."""

    def __init__(
        self,
        *,
        deps: GraphDeps,
        checkpointer: BaseCheckpointSaver,
        max_steps: int = 20,
        repair_budget: int = 3,
    ) -> None:
        self._deps = deps
        self._deps.max_steps = max_steps
        self._deps.repair_budget = repair_budget
        self._checkpointer = checkpointer
        self._graph = build_main_graph(deps, checkpointer=checkpointer)
        self._trace = TraceMiddleware(deps.workspace)
        self._max_steps = max_steps

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    @property
    def graph(self) -> Any:
        return self._graph

    @property
    def trace_middleware(self) -> TraceMiddleware:
        return self._trace

    def run(self, request: RunTaskInput) -> RunTaskResponse:
        state = self._seed_state(request)
        config = self._config(state["thread_id"])
        final = self._graph.invoke(state, config=config)
        self._trace.flush(final)
        return self._response(final, state)

    def resume(
        self,
        *,
        thread_id: str,
        payload: dict[str, Any] | None = None,
    ) -> RunTaskResponse:
        config = self._config(thread_id)
        final = self._graph.invoke(Command(resume=payload or {}), config=config)
        self._trace.flush(final)
        return self._response(final, None, thread_id=thread_id)

    def approve(self, *, thread_id: str, approval_id: str, decision: str = "approved") -> RunTaskResponse:
        return self.resume(
            thread_id=thread_id,
            payload={"approval_id": approval_id, "decision": decision},
        )

    def reject(self, *, thread_id: str, approval_id: str, reason: str) -> RunTaskResponse:
        return self.resume(
            thread_id=thread_id,
            payload={"approval_id": approval_id, "decision": "rejected", "reason": reason},
        )

    def get_state(self, thread_id: str) -> AgentState | None:
        config = self._config(thread_id)
        try:
            snap = self._graph.get_state(config)
        except Exception:
            return None
        values = snap.values if snap is not None else None
        if not values:
            return None
        return values  # type: ignore[return-value]

    def read_trace(self, thread_id: str) -> Iterable[TraceEvent]:
        state = self.get_state(thread_id)
        if state is None:
            return iter(())
        run_id = state.get("root_run_id") or state.get("run_id")
        if not run_id:
            return iter(())
        # Always read from disk to capture historical events beyond what the
        # current state holds.
        trace_path = self._deps.workspace._run_dir(run_id) / "logs" / "trace.jsonl"
        if not trace_path.exists():
            return iter(())

        def _gen():
            import json

            with trace_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)

        return _gen()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _config(self, thread_id: str | None) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": thread_id or new_ulid(),
                CONFIG_DEPS_KEY: self._deps,
            }
        }

    def _seed_state(self, request: RunTaskInput) -> dict[str, Any]:
        run_id = new_ulid()
        thread_id = request.thread_id or f"run_{run_id}"
        permission_mode = request.permission_mode or "ask"
        return {
            "run_id": run_id,
            "root_run_id": run_id,
            "parent_run_id": request.parent_run_id,
            "parent_thread_id": request.parent_thread_id,
            "thread_id": thread_id,
            "agent_name": request.agent,
            "permission_mode": permission_mode,
            "task": request.input,
            "messages": [
                Message(  # type: ignore[typeddict-item]
                    role="user",
                    content=_input_to_user_text(request.input),
                    tool_call_id=None,
                    metadata={},
                )
            ],
            "loaded_skills": [],
            "tool_calls": [],
            "denied_actions": [],
            "workspace_refs": [],
            "pending_approval": None,
            "draft_output": None,
            "final_output": None,
            "step_count": 0,
            "status": "running",
            "pending_trace_events": [],
            "repair_used": 0,
            "max_steps": self._max_steps,
        }

    def _response(
        self,
        final: dict[str, Any] | None,
        seed: dict[str, Any] | None,
        *,
        thread_id: str | None = None,
    ) -> RunTaskResponse:
        if not final:
            return RunTaskResponse(  # type: ignore[typeddict-item]
                run_id="",
                thread_id=thread_id,
                status="failed",
                output=None,
                pending_approval=None,
                error={"code": "no_state", "message": "graph returned no state"},
            )
        status = final.get("status", "running")
        # If the graph paused on interrupt, status is still "running" but
        # ``next`` is non-empty. We surface that as "interrupted".
        if status == "running":
            try:
                snap = self._graph.get_state(self._config(final.get("thread_id")))
                if snap.next and snap.tasks and any(t.interrupts for t in snap.tasks):
                    status = "interrupted"
                    if final.get("pending_approval") is None:
                        for t in snap.tasks:
                            for itr in t.interrupts:
                                v = itr.value if hasattr(itr, "value") else None
                                if isinstance(v, dict):
                                    final["pending_approval"] = {
                                        "approval_id": v.get("approval_id"),
                                        "tool_call_id": v.get("tool_call_id"),
                                        "decision": v.get("decision_kind", "require_approval"),
                                        "summary": v.get("summary", ""),
                                        "risk_level": v.get("risk_level", ""),
                                        "requested_at": "",
                                    }
                                    break
            except Exception:
                pass
        output = final.get("final_output") or (
            final.get("draft_output") if status in ("blocked", "interrupted") else None
        )
        return RunTaskResponse(  # type: ignore[typeddict-item]
            run_id=final.get("run_id", ""),
            thread_id=final.get("thread_id"),
            status=status,  # type: ignore[arg-type]
            output=output,
            pending_approval=final.get("pending_approval"),
            error=None,
        )


def _input_to_user_text(payload: dict[str, Any]) -> str:
    if "messages" in payload and isinstance(payload["messages"], list):
        for msg in reversed(payload["messages"]):
            if isinstance(msg, dict) and msg.get("role") == "user":
                return str(msg.get("content", ""))
    for key in ("customer_message", "question", "goal"):
        if key in payload:
            return str(payload[key])
    return str(payload)
