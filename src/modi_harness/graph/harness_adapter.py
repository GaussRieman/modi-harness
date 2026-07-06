"""Harness Graph Adapter — thin wrapper over the V0.2 LangGraph runtime.

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

import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from .._utils import new_ulid, task_input_to_text
from ..intent import HumanIntentContext
from ..intent.extractor import extract_intent
from ..types import (
    AgentProfile,
    AgentState,
    Message,
    PermissionMode,
    RunTaskResponse,
    TraceEvent,
    WorkspaceRef,
)
from .builder import build_main_graph
from .deps import CONFIG_DEPS_KEY, GraphDeps
from .trace_middleware import TraceMiddleware


@dataclass
class RunInputFile:
    name: str
    data: bytes | str | dict[str, Any] | list[Any]
    mime_type: str | None = None
    trust: Literal["trusted", "untrusted"] = "trusted"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunTaskInput:
    agent: str
    input: dict[str, Any]
    inputs: list[RunInputFile | dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    permission_mode: PermissionMode | None = None
    thread_id: str | None = None
    parent_run_id: str | None = None
    parent_thread_id: str | None = None


class HarnessGraphAdapter:
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

    def stream(self, request: RunTaskInput) -> Iterable[dict[str, Any]]:
        """Yield Modi-normalized StreamEvent dicts.

        V0.2 uses LangGraph's ``stream(stream_mode="updates")`` and projects
        per-node updates into a small set of ``StreamEvent.event_type``s:
        - ``model_delta``    — assistant message added (whole-turn granularity).
        - ``tool_call_proposal`` — non-empty ``pending_tool_calls`` staged.
        - ``tool_call_result``  — new tool_calls entry committed.
        - ``approval_request``  — interrupt observed.
        - ``terminal``       — final state with full :class:`RunTaskResponse`.
        """
        state = self._seed_state(request)
        config = self._config(state["thread_id"])
        seq = 0
        last_state: dict[str, Any] = dict(state)
        for chunk in self._graph.stream(state, config=config, stream_mode="updates"):
            for _node_name, partial in (chunk or {}).items():
                if not isinstance(partial, dict):
                    continue
                # accumulate
                for key in (
                    "messages",
                    "tool_calls",
                    "denied_actions",
                    "workspace_refs",
                    "pending_trace_events",
                ):
                    if key in partial:
                        last_state[key] = list(last_state.get(key) or []) + list(partial[key])
                for key in (
                    "pending_tool_calls",
                    "pending_draft",
                    "pending_approval",
                    "task_plan",
                    "pending_task_plan",
                    "pending_interaction",
                    "status",
                    "step_count",
                    "draft_output",
                    "final_output",
                    "repair_used",
                ):
                    if key in partial:
                        last_state[key] = partial[key]
                if "messages" in partial:
                    for msg in partial["messages"]:
                        if msg.get("role") == "assistant":
                            seq += 1
                            yield self._stream_event("model_delta", seq, last_state, {"content": msg["content"]})
                        elif msg.get("role") == "tool":
                            seq += 1
                            yield self._stream_event(
                                "tool_call_result",
                                seq,
                                last_state,
                                {"tool_call_id": msg.get("tool_call_id"), "content": msg["content"]},
                            )
                if partial.get("pending_tool_calls"):
                    for proposal in partial["pending_tool_calls"]:
                        seq += 1
                        yield self._stream_event(
                            "tool_call_proposal", seq, last_state, dict(proposal)
                        )
                if partial.get("pending_approval"):
                    seq += 1
                    yield self._stream_event(
                        "approval_request", seq, last_state, dict(partial["pending_approval"])
                    )
                for event in _task_stream_events(partial):
                    seq += 1
                    yield self._stream_event(event["event_type"], seq, last_state, event["payload"])
        self._trace.flush(last_state)
        seq += 1
        terminal_response = self._response(last_state, state)
        yield self._stream_event(
            "terminal",
            seq,
            last_state,
            {"response": terminal_response},
            terminal_response=terminal_response,
        )

    async def astream(self, request: RunTaskInput) -> AsyncIterator[dict[str, Any]]:
        """Async variant of :meth:`stream`.

        Uses ``stream_mode="updates"`` (same as sync) but yields from an async
        generator. ``model_delta`` events carry a ``delta`` field with the token
        text (at whole-turn granularity when the underlying model does not
        support true token streaming).
        """
        state = self._seed_state(request)
        async for event in self._astream_graph(
            state,
            config=self._config(state["thread_id"]),
            initial_state=state,
            response_seed=state,
        ):
            yield event

    async def astream_resume(
        self,
        *,
        thread_id: str,
        payload: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Resume an interrupted thread while preserving normalized stream events."""
        config = self._config(thread_id)
        initial_state = self.get_state(thread_id) or {}
        async for event in self._astream_graph(
            Command(resume=payload or {}),
            config=config,
            initial_state=initial_state,
            response_seed=None,
            thread_id=thread_id,
        ):
            yield event

    async def _astream_graph(
        self,
        graph_input: dict[str, Any] | Command,
        *,
        config: dict[str, Any],
        initial_state: dict[str, Any],
        response_seed: dict[str, Any] | None,
        thread_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Project one initial or resumed graph stream into Modi stream events."""
        seq = 0
        last_state: dict[str, Any] = dict(initial_state)
        async for chunk in self._graph.astream(
            graph_input, config=config, stream_mode="updates"
        ):
            for _node_name, partial in (chunk or {}).items():
                if not isinstance(partial, dict):
                    continue
                # accumulate state
                for key in (
                    "messages",
                    "tool_calls",
                    "denied_actions",
                    "workspace_refs",
                    "pending_trace_events",
                ):
                    if key in partial:
                        last_state[key] = list(last_state.get(key) or []) + list(partial[key])
                for key in (
                    "pending_tool_calls",
                    "pending_draft",
                    "pending_approval",
                    "task_plan",
                    "pending_task_plan",
                    "pending_interaction",
                    "status",
                    "step_count",
                    "draft_output",
                    "final_output",
                    "repair_used",
                ):
                    if key in partial:
                        last_state[key] = partial[key]
                if "messages" in partial:
                    for msg in partial["messages"]:
                        if msg.get("role") == "assistant":
                            seq += 1
                            yield self._stream_event(
                                "model_delta",
                                seq,
                                last_state,
                                {"delta": msg["content"], "content": msg["content"]},
                            )
                        elif msg.get("role") == "tool":
                            seq += 1
                            yield self._stream_event(
                                "tool_call_result",
                                seq,
                                last_state,
                                {"tool_call_id": msg.get("tool_call_id"), "content": msg["content"]},
                            )
                if partial.get("pending_tool_calls"):
                    for proposal in partial["pending_tool_calls"]:
                        seq += 1
                        yield self._stream_event(
                            "tool_call_proposal", seq, last_state, dict(proposal)
                        )
                if partial.get("pending_approval"):
                    seq += 1
                    yield self._stream_event(
                        "approval_request", seq, last_state, dict(partial["pending_approval"])
                    )
                for event in _task_stream_events(partial):
                    seq += 1
                    yield self._stream_event(event["event_type"], seq, last_state, event["payload"])
        self._trace.flush(last_state)
        seq += 1
        terminal_response = self._response(
            last_state, response_seed, thread_id=thread_id
        )
        yield self._stream_event(
            "terminal",
            seq,
            last_state,
            {"response": terminal_response},
            terminal_response=terminal_response,
        )

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

    def respond_to_judgment(
        self,
        *,
        thread_id: str,
        judgment_id: str,
        kind: str,
        rationale: str | None = None,
        intent_updates: dict[str, Any] | None = None,
    ) -> RunTaskResponse:
        """Resume an interrupted run with a human judgment.

        ``kind`` is a ``HumanJudgmentKind`` (approve/reject/revise/redirect/
        constrain/clarify/cancel). ``intent_updates`` is an optional
        ``IntentPatch`` applied to the live intent field on resume.
        """
        payload: dict[str, Any] = {"judgment_id": judgment_id, "kind": kind}
        if rationale is not None:
            payload["rationale"] = rationale
        if intent_updates:
            payload["intent_updates"] = intent_updates
        return self.resume(thread_id=thread_id, payload=payload)

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

    def _stream_event(
        self,
        event_type: str,
        seq: int,
        state: dict[str, Any],
        payload: dict[str, Any],
        *,
        terminal_response: RunTaskResponse | None = None,
    ) -> dict[str, Any]:
        return {
            "event_type": event_type,
            "run_id": state.get("run_id", ""),
            "sequence": seq,
            "payload": payload,
            "terminal_response": terminal_response,
        }

    def _seed_state(self, request: RunTaskInput) -> dict[str, Any]:
        run_id = new_ulid()
        thread_id = request.thread_id or f"run_{run_id}"
        from ..policy.modes import enforce_trust_guard, normalize_mode
        permission_mode = normalize_mode(request.permission_mode or "auto")
        enforce_trust_guard(permission_mode)
        input_refs = self._materialize_inputs(run_id, request.inputs)
        task = dict(request.input)
        if input_refs:
            task["input_refs"] = [dict(ref) for ref in input_refs]
        interactive_startup = task.get("interactive_startup") is True
        human_intent = self._seed_intent(request, task)
        startup_content = "[interactive_startup] Begin the Agent's declared startup interaction."
        return {
            "run_id": run_id,
            "root_run_id": run_id,
            "parent_run_id": request.parent_run_id,
            "parent_thread_id": request.parent_thread_id,
            "thread_id": thread_id,
            "agent_name": request.agent,
            "permission_mode": permission_mode,
            "task": task,
            "messages": [
                Message(  # type: ignore[typeddict-item]
                    role="user",
                    content=startup_content if interactive_startup else task_input_to_text(task),
                    tool_call_id=None,
                    metadata={"kind": "interactive_startup"} if interactive_startup else {},
                )
            ],
            "loaded_skills": [],
            "tool_calls": [],
            "denied_actions": [],
            "workspace_refs": input_refs,
            "pending_approval": None,
            "task_plan": None,
            "pending_task_plan": None,
            "pending_interaction": None,
            "human_context": {"version": 0, "inputs": {}, "decisions": [], "feedback": []},
            "human_intent": human_intent,
            "intent_version": human_intent["version"],
            "stage_id": human_intent["current_stage"]["id"],
            "draft_output": None,
            "final_output": None,
            "step_count": 0,
            "status": "running",
            "pending_trace_events": [],
            "repair_used": 0,
            "max_steps": self._max_steps,
        }

    def _seed_intent(
        self, request: RunTaskInput, task: dict[str, Any]
    ) -> HumanIntentContext:
        """Build the authoritative HumanIntentContext for a fresh run.

        Seeds default boundaries from the agent's safety constraints when the
        profile is resolvable; a thin or absent profile still yields a valid
        context (extraction never blocks). An explicit caller-supplied partial
        intent (``input["human_intent"]``) overrides inferred fields (spec D1).
        """
        try:
            profile: AgentProfile | None = self._deps.agents.load_agent(request.agent)
        except Exception:
            profile = None
        override = request.input.get("human_intent")
        return extract_intent(
            task,
            agent=profile,
            override=override if isinstance(override, dict) else None,
        )

    def _materialize_inputs(
        self,
        run_id: str,
        inputs: Iterable[RunInputFile | dict[str, Any]],
    ) -> list[WorkspaceRef]:
        refs: list[WorkspaceRef] = []
        items = list(inputs or [])
        if not items:
            return refs
        self._deps.workspace.create_run(run_id)
        for item in items:
            name, data, mime_type, trust, metadata = _normalize_input_file(item)
            refs.append(
                self._deps.workspace.save_input(
                    run_id,
                    name,
                    data,
                    trust=trust,
                    mime_type=mime_type,
                    metadata=metadata,
                )
            )
        return refs

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
                pending_interaction=None,
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
                    if (
                        final.get("pending_approval") is None
                        and final.get("pending_interaction") is None
                    ):
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
                                    final["pending_judgment"] = {
                                        "judgment_id": v.get(
                                            "judgment_id", v.get("approval_id")
                                        ),
                                        "approval_id": v.get("approval_id"),
                                        "tool_call_id": v.get("tool_call_id"),
                                        "target_action_id": v.get("target_action_id"),
                                        "target_stage_id": v.get("target_stage_id"),
                                        "reviewed_action_hash": v.get("reviewed_action_hash"),
                                        "prompt": v.get("prompt", ""),
                                        "allowed_kinds": v.get(
                                            "allowed_kinds",
                                            ["approve", "reject"],
                                        ),
                                        "proposed_intent_patch": None,
                                        "summary": v.get("summary", ""),
                                        "rationale": None,
                                        "risk_level": v.get("risk_level", ""),
                                        "requested_at": "",
                                    }
                                    break
            except Exception:
                pass
        # Degradation: when validation rejects after repair budget, the
        # draft_output still holds the model's last attempt. Surface it as
        # ``output`` so callers retain visibility into what the model said,
        # even though it's not validated. ``status`` distinguishes pass/fail.
        output = final.get("final_output") or (
            final.get("draft_output")
            if status in ("blocked", "interrupted", "failed")
            else None
        )
        error = None
        exhausted = any(
            event.get("event_type") == "error"
            and (event.get("payload") or {}).get("code") == "max_steps_exceeded"
            for event in final.get("pending_trace_events") or []
        )
        if status == "failed" and exhausted:
            max_steps = final.get("max_steps", self._max_steps)
            error = {
                "code": "max_steps_exceeded",
                "message": f"run exceeded the {max_steps}-step limit",
            }
        return RunTaskResponse(  # type: ignore[typeddict-item]
            run_id=final.get("run_id", ""),
            thread_id=final.get("thread_id"),
            status=status,  # type: ignore[arg-type]
            output=output,
            pending_approval=final.get("pending_approval"),
            pending_judgment=final.get("pending_judgment"),
            pending_interaction=final.get("pending_interaction"),
            error=error,
        )


_TASK_STREAM_EVENT_TYPES = {
    "interaction_requested",
    "interaction_resolved",
    "task_plan_created",
    "task_plan_revised",
    "task_started",
    "task_resumed",
    "task_completed",
    "task_blocked",
    "finalization_started",
    "output_repair_started",
    "error",
}


def _task_stream_events(partial: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"event_type": event["event_type"], "payload": dict(event.get("payload") or {})}
        for event in partial.get("pending_trace_events") or []
        if event.get("event_type") in _TASK_STREAM_EVENT_TYPES
    ]


def _normalize_input_file(
    item: RunInputFile | dict[str, Any],
) -> tuple[str, bytes, str | None, Literal["trusted", "untrusted"], dict[str, Any]]:
    if isinstance(item, RunInputFile):
        name = item.name
        data = item.data
        mime_type = item.mime_type
        trust = item.trust
        metadata = item.metadata
    else:
        name = item["name"]
        data = item["data"]
        mime_type = item.get("mime_type")
        trust = item.get("trust", "trusted")
        metadata = item.get("metadata") or {}

    if trust not in ("trusted", "untrusted"):
        raise ValueError(f"invalid input trust: {trust!r}")
    if isinstance(data, bytes):
        payload = data
    elif isinstance(data, str):
        payload = data.encode("utf-8")
        mime_type = mime_type or "text/plain"
    else:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        mime_type = mime_type or "application/json"
    return str(name), payload, mime_type, trust, dict(metadata)
