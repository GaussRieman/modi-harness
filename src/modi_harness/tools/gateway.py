"""Tool Gateway implementation.

The execution chain:

    proposal
    -> registry lookup            (unknown -> closed)
    -> schema validation
    -> agent visibility check
    -> denied-retry guard
    -> pre_tool_use hook dispatch
    -> Policy Gate decision
    -> execute (or interrupt for approval / review)
    -> post_tool_use hook dispatch
    -> normalize result with trust annotation
"""

from __future__ import annotations

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


class ToolGateway:
    """Validates and governs model-requested tool calls."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyGate,
        hooks: HookDispatcher,
        result_inline_limit_bytes: int,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._hooks = hooks
        self._inline_limit = result_inline_limit_bytes
        self._idempotency_cache: dict[tuple[str, str], dict[str, Any]] = {}

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
        tool_name = proposal["tool_name"]
        args = proposal["arguments"]
        started_at = now_iso()

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

        # 6. Policy decision. Plan-mode + dry_run_supported bypasses the
        # plan-rewrite-to-review so the dry-run can execute side-effect-free.
        plan_dry_run = (
            state["permission_mode"] == "plan"
            and spec["dry_run_supported"]
            and entry.dry_run is not None
        )
        if plan_dry_run:
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
                    "permission_mode": "ask",  # treat dry-run as a read for policy
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
                }
            )

        if decision["decision"] == "deny":
            record = _record(proposal, started_at, decision="deny", result=None)
            return ToolDispatchResult(outcome="error", record=record, decision=decision)

        if decision["decision"] in ("require_approval", "require_review"):
            record = _record(proposal, started_at, decision=decision["decision"], result=None)
            return ToolDispatchResult(outcome="interrupt", record=record, decision=decision)

        # 7. Idempotency cache.
        if spec["idempotent"]:
            cache_key = (tool_name, fingerprint)
            if cache_key in self._idempotency_cache:
                result_payload = self._idempotency_cache[cache_key]
                record = _record(proposal, started_at, decision="allow", result=result_payload)
                return _wrap_executed(proposal, record, decision, pre_hook_results, self._inline_limit)

        # 8. Execute (or dry-run when plan mode).
        try:
            if spec["kind"] == "builtin":
                result_payload = entry.handler(
                    arguments=args, state=state, deps=graph_deps,
                )
            elif state["permission_mode"] == "plan" and entry.dry_run is not None:
                result_payload = entry.dry_run(**args)
            else:
                result_payload = entry.handler(**args)
        except Exception as exc:  # noqa: BLE001
            record = _record(proposal, started_at, decision="allow", error={"message": str(exc)})
            return ToolDispatchResult(
                outcome="error",
                record=record,
                decision=decision,
                error=ToolError(str(exc)),
                error_message=str(exc),
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
                "agent": agent["name"],
            },
        )

        record = _record(proposal, started_at, decision="allow", result=result_payload)
        return _wrap_executed(
            proposal,
            record,
            decision,
            pre_hook_results + post_hook_results,
            self._inline_limit,
        )


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
    )
