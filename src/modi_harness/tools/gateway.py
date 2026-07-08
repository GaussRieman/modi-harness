"""Tool Gateway implementation.

The execution chain:

    proposal
    -> registry lookup            (unknown -> closed)
    -> schema validation
    -> agent visibility check
    -> denied-retry guard
    -> pre_tool_use hook dispatch
    -> Policy Gate decision
    -> execute (or interrupt for governance proof / human judgment)
    -> post_tool_use hook dispatch
    -> normalize result with trust annotation
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Literal

from jsonschema import Draft202012Validator, ValidationError

from .._utils import compute_fingerprint, now_iso
from ..hooks import HookDispatcher
from ..policy import PolicyGate
from ..types import (
    AgentProfile,
    AgentState,
    DeniedAction,
    HookResult,
    PolicyDecision,
    ToolCallProposal,
    ToolCallRecord,
    ToolSpec,
    TrustAnnotation,
    WorkspaceRef,
)
from .errors import ToolError, ToolSchemaError, ToolUnknownError
from .registry import ToolRegistry, _Entry

Outcome = Literal["executed", "interrupt", "denied_retry", "hook_blocked", "error"]


@dataclass
class _Prepared:
    """The result of the pre-decision phase shared by Tool/Action gateways.

    Carries everything the decision step and the execute step need so the
    middle decision (PolicyGate, or AlignmentKernel + GovernanceGate) can be
    swapped without duplicating registry/schema/hook plumbing.
    """

    entry: _Entry
    spec: ToolSpec
    fingerprint: str
    pre_hook_results: list[HookResult]
    plan_dry_run: bool
    agent_name: str


@dataclass
class ToolDispatchResult:
    outcome: Outcome
    record: ToolCallRecord
    decision: PolicyDecision | None = None
    hook_results: list[HookResult] = field(default_factory=list)
    trust: TrustAnnotation = field(
        default_factory=lambda: TrustAnnotation(  # type: ignore[typeddict-item]
            trust_level="untrusted",
            source_kind="tool_result",
            source_id="",
            sanitizer=None,
        )
    )
    error: ToolError | None = None
    error_message: str | None = None
    # Subagent propagation: child denied_actions diff and workspace refs to splice
    # into the parent state. Empty for regular tools.
    propagated_denied_actions: list[DeniedAction] = field(default_factory=list)
    propagated_workspace_refs: list[WorkspaceRef] = field(default_factory=list)
    idempotency_cache_hit: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    # Intent lineage (set by ActionGateway). ``action_id`` is the ActionProposal
    # id; ``alignment_decision_id`` is the AlignmentDecision id. Both None when a
    # call ran through the legacy policy-only path. ``action_proposal`` and
    # ``alignment_decision`` carry the full records so the graph node can emit
    # lineage trace events (action_proposed / alignment_decision /
    # intent_lineage_recorded) without re-deriving them.
    action_id: str | None = None
    alignment_decision_id: str | None = None
    action_proposal: dict[str, Any] | None = None
    alignment_decision: dict[str, Any] | None = None


class ToolGateway:
    """Validates and governs model-requested tool calls."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyGate,
        hooks: HookDispatcher,
        result_inline_limit_bytes: int,
        interactive: bool | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._hooks = hooks
        self._inline_limit = result_inline_limit_bytes
        self._idempotency_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._interactive = (
            interactive if interactive is not None else _detect_interactive()
        )

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def execute_tool_call(
        self,
        proposal: ToolCallProposal,
        *,
        agent: AgentProfile,
        state: AgentState,
        subagent_dispatcher: Any | None = None,
        subagent_max_depth: int = 3,
        graph_deps: Any | None = None,
    ) -> ToolDispatchResult:
        started_at = now_iso()

        prepared = self._prepare(
            proposal,
            started_at=started_at,
            agent=agent,
            state=state,
            subagent_dispatcher=subagent_dispatcher,
            subagent_max_depth=subagent_max_depth,
            graph_deps=graph_deps,
        )
        # Early exit (unknown tool, subagent dispatch, hook block, denied retry).
        if isinstance(prepared, ToolDispatchResult):
            return prepared

        return self._decide_and_finish(
            proposal,
            started_at=started_at,
            prepared=prepared,
            agent=agent,
            state=state,
            graph_deps=graph_deps,
        )

    def _decide_and_finish(
        self,
        proposal: ToolCallProposal,
        *,
        started_at: str,
        prepared: _Prepared,
        agent: AgentProfile,
        state: AgentState,
        graph_deps: Any | None,
    ) -> ToolDispatchResult:
        """Legacy policy decision + execute. Reused as the no-intent fallback."""
        tool_name = proposal["tool_name"]
        args = proposal["arguments"]
        spec = prepared.spec
        fingerprint = prepared.fingerprint

        # Policy decision. Plan-mode + dry_run_supported bypasses the
        # plan-rewrite-to-review so the dry-run can execute side-effect-free.
        if prepared.plan_dry_run:
            decision = self._policy.decide(
                {
                    "agent": agent,
                    "skill": None,
                    "tool_spec": spec,
                    "state": state,
                    "requested_action": {
                        "kind": "tool_call",
                        "tool_name": tool_name,
                        "arguments": args,
                        "target": None,
                        "fingerprint": fingerprint,
                    },
                    "permission_mode": "auto",  # treat dry-run as a read for policy
                }
            )
        else:
            decision = self._policy.decide(
                {
                    "agent": agent,
                    "skill": None,
                    "tool_spec": spec,
                    "state": state,
                    "requested_action": {
                        "kind": "tool_call",
                        "tool_name": tool_name,
                        "arguments": args,
                        "target": None,
                        "fingerprint": fingerprint,
                    },
                    "permission_mode": state["permission_mode"],
                    "interactive": self._interactive,
                }
            )

        if decision["decision"] == "deny":
            record = _record(proposal, started_at, decision="deny", result=None)
            return ToolDispatchResult(outcome="error", record=record, decision=decision)

        if decision["decision"] in ("require_approval", "require_review"):
            record = _record(proposal, started_at, decision=decision["decision"], result=None)
            return ToolDispatchResult(outcome="interrupt", record=record, decision=decision)

        return self._finish(
            proposal,
            started_at=started_at,
            prepared=prepared,
            decision=decision,
            state=state,
            graph_deps=graph_deps,
        )

    # ------------------------------------------------------------------
    # phases (shared with ActionGateway)
    # ------------------------------------------------------------------

    def _prepare(
        self,
        proposal: ToolCallProposal,
        *,
        started_at: str,
        agent: AgentProfile,
        state: AgentState,
        subagent_dispatcher: Any | None,
        subagent_max_depth: int,
        graph_deps: Any | None,
    ) -> _Prepared | ToolDispatchResult:
        """Registry/visibility/schema/denied-retry/pre-hook — pre-decision.

        Returns a ``_Prepared`` when the call is ready for a decision, or a
        terminal ``ToolDispatchResult`` for an early exit (unknown tool,
        subagent dispatch, schema failure, denied retry, hook block).
        """
        tool_name = proposal["tool_name"]
        args = proposal["arguments"]

        # 1. Registry lookup.
        if not self._registry.has(tool_name):
            return _error(
                proposal,
                started_at,
                ToolUnknownError(f"unknown tool: {tool_name}"),
            )
        entry = self._registry.get_entry(tool_name)
        spec = entry.spec

        # 1b. Subagent branch.
        if spec["kind"] == "subagent":
            if subagent_dispatcher is None or graph_deps is None:
                return _error(
                    proposal,
                    started_at,
                    ToolError("subagent dispatch unavailable: deps not wired"),
                )
            return subagent_dispatcher(
                proposal=proposal,
                spec=spec,
                parent_agent=agent,
                parent_state=state,
                deps=graph_deps,
                subagent_max_depth=subagent_max_depth,
            )

        # 2. Visibility re-check (builtins bypass agent allowlist by design).
        if spec["kind"] != "builtin" and tool_name not in agent["default_tools"]:
            return _error(
                proposal,
                started_at,
                ToolError(f"tool {tool_name!r} not visible to agent {agent['name']!r}"),
            )

        # 3. Schema validation.
        try:
            Draft202012Validator(spec["input_schema"]).validate(args)
        except ValidationError as exc:
            return _error(
                proposal,
                started_at,
                ToolSchemaError(f"schema validation failed: {exc.message}"),
            )

        # 4. Denied-retry guard.
        fingerprint = compute_fingerprint({"tool": tool_name, "args": args})
        denied_fingerprints = {d["fingerprint"] for d in state["denied_actions"]}
        denied_signatures = {(d["tool_name"], compute_fingerprint(d["arguments"])) for d in state["denied_actions"]}
        if fingerprint in denied_fingerprints or (
            tool_name,
            compute_fingerprint(args),
        ) in denied_signatures:
            record = _record(proposal, started_at, decision="deny", result=None)
            return ToolDispatchResult(outcome="denied_retry", record=record)

        # 5. pre_tool_use hooks.
        pre_hook_results = self._hooks.dispatch(
            "pre_tool_use",
            {
                "tool_name": tool_name,
                "risk_level": spec["risk_level"],
                "arguments": args,
                "agent": agent["name"],
                "permission_mode": state["permission_mode"],
            },
        )
        if any(h["decision"] == "block" for h in pre_hook_results):
            record = _record(proposal, started_at, decision="deny", result=None)
            return ToolDispatchResult(
                outcome="hook_blocked",
                record=record,
                hook_results=pre_hook_results,
            )

        plan_dry_run = (
            state["permission_mode"] == "preview"
            and spec["dry_run_supported"]
            and entry.dry_run is not None
        )
        return _Prepared(
            entry=entry,
            spec=spec,
            fingerprint=fingerprint,
            pre_hook_results=pre_hook_results,
            plan_dry_run=plan_dry_run,
            agent_name=agent["name"],
        )

    def _finish(
        self,
        proposal: ToolCallProposal,
        *,
        started_at: str,
        prepared: _Prepared,
        decision: PolicyDecision,
        state: AgentState,
        graph_deps: Any | None,
    ) -> ToolDispatchResult:
        """Idempotency/execute/post-hook/wrap — post-decision.

        Reached only when the decision step cleared the call to run.
        """
        tool_name = proposal["tool_name"]
        args = proposal["arguments"]
        entry = prepared.entry
        spec = prepared.spec
        fingerprint = prepared.fingerprint
        pre_hook_results = prepared.pre_hook_results

        # 7. Idempotency cache.
        if spec["idempotent"]:
            cache_key = (tool_name, fingerprint)
            if cache_key in self._idempotency_cache:
                result_payload = self._idempotency_cache[cache_key]
                record = _record(proposal, started_at, decision="allow", result=result_payload)
                return _wrap_executed(
                    proposal,
                    record,
                    decision,
                    pre_hook_results,
                    self._inline_limit,
                    idempotency_cache_hit=True,
                )

        # 8. Execute (or dry-run when preview mode).
        attempts: list[dict[str, Any]] = []
        try:
            result_payload = self._execute_with_retry(
                entry,
                spec,
                args,
                state,
                graph_deps,
                attempts,
                tool_name=tool_name,
            )
        except Exception as exc:
            record = _record(proposal, started_at, decision="allow", error={"message": str(exc)})
            return ToolDispatchResult(
                outcome="error",
                record=record,
                decision=decision,
                error=ToolError(str(exc)),
                error_message=str(exc),
                attempts=attempts,
            )

        if not isinstance(result_payload, dict):
            result_payload = {"value": result_payload}

        # 9. Idempotency cache write.
        if spec["idempotent"]:
            self._idempotency_cache[(tool_name, fingerprint)] = result_payload

        # 10. post_tool_use hooks (advisory; non-blocking semantically).
        post_hook_results = self._hooks.dispatch(
            "post_tool_use",
            {
                "tool_name": tool_name,
                "result": result_payload,
                "agent": prepared.agent_name,
            },
        )

        record = _record(proposal, started_at, decision="allow", result=result_payload)
        return _wrap_executed(
            proposal,
            record,
            decision,
            pre_hook_results + post_hook_results,
            self._inline_limit,
            attempts=attempts,
        )

    def _execute_with_retry(
        self,
        entry: _Entry,
        spec: ToolSpec,
        args: dict[str, Any],
        state: AgentState,
        graph_deps: Any | None,
        attempts: list[dict[str, Any]],
        *,
        tool_name: str,
    ) -> Any:
        retry = spec.get("retry") or None
        max_attempts = max(1, int((retry or {}).get("max_attempts", 1)))
        backoff = float((retry or {}).get("backoff_seconds", 0.0))
        for attempt in range(1, max_attempts + 1):
            try:
                result = _execute_once_with_timeout(
                    entry,
                    spec,
                    args,
                    state,
                    graph_deps,
                    tool_name=tool_name,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "outcome": "success",
                        "error_code": None,
                        "timeout": False,
                    }
                )
                return result
            except Exception as exc:
                error_code = _classify_tool_exception(exc)
                terminal = attempt >= max_attempts or not _should_retry(exc, retry, spec)
                attempts.append(
                    {
                        "attempt": attempt,
                        "outcome": "error",
                        "error_code": error_code,
                        "timeout": error_code == "timeout",
                        "terminal": terminal,
                    }
                )
                if terminal:
                    raise
                if backoff > 0:
                    time.sleep(backoff * attempt)
        raise RuntimeError("unreachable retry state")


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _record(
    proposal: ToolCallProposal,
    started_at: str,
    *,
    decision: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> ToolCallRecord:
    return ToolCallRecord(  # type: ignore[typeddict-item]
        tool_call_id=proposal["tool_call_id"],
        tool_name=proposal["tool_name"],
        arguments=proposal["arguments"],
        decision=decision,  # type: ignore[arg-type]
        result=result,
        error=error,
        started_at=started_at,
        finished_at=now_iso() if (result is not None or error is not None) else None,
    )


def _error(
    proposal: ToolCallProposal,
    started_at: str,
    err: ToolError,
) -> ToolDispatchResult:
    record = _record(proposal, started_at, decision="deny", error={"message": str(err)})
    return ToolDispatchResult(
        outcome="error",
        record=record,
        error=err,
        error_message=str(err),
    )


def _wrap_executed(
    proposal: ToolCallProposal,
    record: ToolCallRecord,
    decision: PolicyDecision,
    hook_results: list[HookResult],
    inline_limit: int,
    *,
    idempotency_cache_hit: bool = False,
    attempts: list[dict[str, Any]] | None = None,
) -> ToolDispatchResult:
    trust = TrustAnnotation(  # type: ignore[typeddict-item]
        trust_level="untrusted",
        source_kind="tool_result",
        source_id=record["tool_call_id"],
        sanitizer="default",
    )
    # Large-result offload to workspace happens at a higher level (Runtime/
    # Context Manager invoke WorkspaceManager.write_payload); the gateway just
    # records the size and trust annotation. For now, we keep the dict but
    # callers should consult ``inline_limit`` to decide.
    return ToolDispatchResult(
        outcome="executed",
        record=record,
        decision=decision,
        hook_results=hook_results,
        trust=trust,
        idempotency_cache_hit=idempotency_cache_hit,
        attempts=list(attempts or []),
    )


def _execute_once(
    entry: _Entry,
    spec: ToolSpec,
    args: dict[str, Any],
    state: AgentState,
    graph_deps: Any | None,
    *,
    tool_name: str,
) -> Any:
    # Preview-mode intercept: in preview, only L0 tools may run live.
    # L1+ tools either run their dry_run handler (if declared) or are
    # intercepted with a synthetic success so the agent's plan can complete
    # end-to-end without touching the world.
    preview_intercept = (
        state["permission_mode"] == "preview"
        and spec["risk_level"] != "L0"
        and entry.dry_run is None
    )
    if preview_intercept:
        return {
            "ok": True,
            "dry_run": True,
            "simulated": True,
            "would_call": tool_name,
            "would_args": args,
        }
    if spec["kind"] == "builtin":
        return entry.handler(arguments=args, state=state, deps=graph_deps)
    if state["permission_mode"] == "preview" and entry.dry_run is not None:
        return entry.dry_run(**args)
    return entry.handler(**args)


def _execute_once_with_timeout(
    entry: _Entry,
    spec: ToolSpec,
    args: dict[str, Any],
    state: AgentState,
    graph_deps: Any | None,
    *,
    tool_name: str,
) -> Any:
    timeout_seconds = float(spec.get("timeout_seconds") or 0)
    if timeout_seconds <= 0:
        return _execute_once(entry, spec, args, state, graph_deps, tool_name=tool_name)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            _execute_once,
            entry,
            spec,
            args,
            state,
            graph_deps,
            tool_name=tool_name,
        )
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError(f"tool {tool_name!r} timed out after {timeout_seconds:g}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _classify_tool_exception(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection_error"
    if isinstance(exc, ValueError):
        return "value_error"
    return exc.__class__.__name__


def _should_retry(
    exc: BaseException, retry: dict[str, Any] | None, spec: ToolSpec
) -> bool:
    if not retry:
        return False
    if spec["side_effect"] and not spec["idempotent"]:
        return False
    retry_on = {str(item).lower() for item in retry.get("retry_on") or []}
    if not retry_on:
        return False
    code = _classify_tool_exception(exc).lower()
    cls_name = exc.__class__.__name__.lower()
    return code in retry_on or cls_name in retry_on or "exception" in retry_on


def _detect_interactive() -> bool:
    """Decide whether tool calls can prompt a human in this process.

    The user is the authority — we do **not** try to be clever with
    ``isatty()`` (which is wrong under nohup/screen/docker-logs/CI runners
    that pipe stdin). The rule is:

    - If ``MODI_INTERACTIVE`` is set to a falsey value (``0``, ``false``,
      ``no``, ``off``, empty string), this process is non-interactive.
    - Otherwise, this process is interactive.

    A CLI invocation that *knows* it can prompt (the rich streaming runner)
    overrides this by constructing ``ToolGateway`` with ``interactive=True``.
    """
    raw = os.environ.get("MODI_INTERACTIVE")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")
