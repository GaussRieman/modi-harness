"""Policy Gate implementation.

Pure function from ``PolicyContext`` to ``PolicyDecision``. Decisions are
anchored to risk level and permission mode; rule packs can elevate.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from .._utils import new_ulid
from ..types import (
    ActionMatcher,
    AgentProfile,
    AgentState,
    PermissionMode,
    PolicyContext,
    PolicyDecision,
)
from .rule_packs import load_packs


_RISK_ORDER: dict[str, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


class PolicyGate:
    """The single decider for tool calls, memory writes, and output finalization."""

    def __init__(self, rule_packs: list[str] | None = None) -> None:
        self._matchers: list[tuple[str, ActionMatcher]] = load_packs(rule_packs or [])

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def decide(self, ctx: PolicyContext) -> PolicyDecision:
        kind = ctx["requested_action"]["kind"]

        # Denied-retry is universal: same fingerprint blocked, regardless of mode.
        if _is_denied_retry(ctx):
            return _decision(
                "deny",
                reason="denied-retry: same fingerprint as a prior denial",
                denied_retry=True,
                audit={"check": "denied_retry"},
            )

        if kind == "memory_write":
            return self._decide_memory_write(ctx)
        if kind == "output_finalize":
            return _decide_output_finalize(ctx)
        return self._decide_tool_call(ctx)

    def visible_tools(
        self,
        agent: AgentProfile,
        mode: PermissionMode,
        state: AgentState,
    ) -> list[str]:
        del state  # reserved for future stateful filtering
        del mode
        deny = set()
        pp = agent.get("permission_profile")
        if pp:
            deny = set(pp.get("deny", []) or [])
        return [t for t in agent["default_tools"] if t not in deny]

    # ------------------------------------------------------------------
    # tool_call
    # ------------------------------------------------------------------

    def _decide_tool_call(self, ctx: PolicyContext) -> PolicyDecision:
        spec = ctx["tool_spec"]
        if spec is None:
            return _decision("deny", reason="missing tool_spec", audit={"check": "missing_spec"})

        tool_name = spec["name"]
        agent = ctx["agent"]
        pp = agent.get("permission_profile") or {}
        mode = ctx["permission_mode"]
        risk = spec["risk_level"]

        # Hard denies first.
        if tool_name in (pp.get("deny") or []):
            return _decision("deny", reason="tool on agent deny-list", audit={"check": "deny_list"})

        # Per-agent review_required wins over risk-driven decisions.
        if tool_name in (pp.get("review_required") or []):
            return _decision(
                "require_review",
                reason="tool listed in permission_profile.review_required",
                audit={"check": "review_required_list"},
            )

        # Base decision from risk + mode.
        base = _base_tool_decision(risk, mode, ctx)

        # Apply rule pack matchers — may elevate only.
        pack_hits: list[str] = []
        for pack_name, matcher in self._matchers:
            if matcher["kind"] != "tool_call":
                continue
            if not _matcher_applies(matcher, spec, risk):
                continue
            base = _elevate(base, matcher["elevate_to"])
            pack_hits.append(pack_name)

        audit: dict[str, Any] = {"risk": risk, "mode": mode}
        if pack_hits:
            audit["rule_pack_hits"] = pack_hits

        approval_id = new_ulid() if base == "require_approval" else None
        review_requirement = {"reason": "policy"} if base == "require_review" else None

        if risk == "L4" and base == "require_approval":
            audit["requires_audit"] = True

        return _decision(
            base,
            reason=f"{risk} under mode={mode}",
            approval_id=approval_id,
            review_requirement=review_requirement,
            audit=audit,
        )

    # ------------------------------------------------------------------
    # memory_write
    # ------------------------------------------------------------------

    def _decide_memory_write(self, ctx: PolicyContext) -> PolicyDecision:
        target = ctx["requested_action"].get("target") or {}
        scope = target.get("scope")
        source_kind = target.get("source_kind")

        if source_kind == "tool_result":
            return _decision(
                "deny",
                reason="memory write derived from untrusted tool result requires user round-trip",
                audit={"check": "memory_untrusted_source"},
            )

        if scope in ("conversation", "agent"):
            return _decision("allow", reason="memory write to harness-scoped storage", audit={"scope": scope})

        if scope in ("user", "project"):
            return _decision(
                "require_approval",
                reason="memory write to durable user/project scope requires approval",
                approval_id=new_ulid(),
                audit={"scope": scope},
            )

        return _decision("deny", reason=f"unknown memory scope: {scope!r}", audit={})


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _is_denied_retry(ctx: PolicyContext) -> bool:
    fp = ctx["requested_action"]["fingerprint"]
    return any(d["fingerprint"] == fp for d in ctx["state"]["denied_actions"])


def _decide_output_finalize(ctx: PolicyContext) -> PolicyDecision:
    target = ctx["requested_action"].get("target") or {}
    status = target.get("status")
    if status in ("validated", "final"):
        return _decision("allow", reason=f"output status={status}", audit={"status": status})
    if status == "needs_review":
        return _decision(
            "require_review",
            reason="output needs human review",
            audit={"status": status},
        )
    if status == "rejected":
        return _decision("deny", reason="output validation rejected", audit={"status": status})
    return _decision("deny", reason=f"unknown output status: {status!r}", audit={"status": status})


def _base_tool_decision(risk: str, mode: PermissionMode, ctx: PolicyContext) -> str:
    pp = ctx["agent"].get("permission_profile") or {}
    tool_name = ctx["tool_spec"]["name"] if ctx["tool_spec"] else ""
    target = ctx["requested_action"].get("target") or {}

    if mode == "bypass":
        return "allow"

    if mode == "plan":
        if risk in ("L0", "L1"):
            return "allow"
        return "require_review"

    if risk in ("L0", "L1"):
        return "allow"

    if risk == "L2":
        scope = target.get("scope")
        if scope in ("workspace", "draft", None):
            # default to allow when no scope specified; tests opt-in to "external" to assert otherwise
            if scope is None:
                return "allow"
            return "allow"
        return "require_approval"

    if risk == "L3":
        if mode == "auto" and tool_name in (pp.get("preauthorized") or []):
            return "allow"
        return "require_approval"

    if risk == "L4":
        # L4 always approval regardless of preauthorized in auto mode.
        return "require_approval"

    return "deny"


def _elevate(current: str, target: str) -> str:
    """Decision rank: allow < require_review < require_approval < deny."""
    rank = {"allow": 0, "require_review": 1, "require_approval": 2, "deny": 3}
    if rank.get(target, 0) > rank.get(current, 0):
        return target
    return current


def _matcher_applies(matcher: ActionMatcher, spec: Any, risk: str) -> bool:
    if matcher["tool_name_pattern"] is not None:
        if not fnmatch.fnmatch(spec["name"], matcher["tool_name_pattern"]):
            return False
    if matcher["risk_floor"] is not None:
        if _RISK_ORDER.get(risk, 0) < _RISK_ORDER.get(matcher["risk_floor"], 0):
            return False
    if matcher["tag_any"]:
        tags = set(spec.get("tags") or [])
        if not (tags & set(matcher["tag_any"])):
            return False
    return True


def _decision(
    decision: str,
    *,
    reason: str,
    approval_id: str | None = None,
    review_requirement: dict[str, Any] | None = None,
    denied_retry: bool = False,
    audit: dict[str, Any] | None = None,
) -> PolicyDecision:
    return PolicyDecision(  # type: ignore[typeddict-item]
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
        approval_id=approval_id,
        review_requirement=review_requirement,
        denied_retry=denied_retry,
        audit=audit or {},
    )
