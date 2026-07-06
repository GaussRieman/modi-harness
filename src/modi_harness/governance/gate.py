"""Governance gate: prove safety *beneath* the alignment decision (plan N4.4).

This wraps the existing :class:`~modi_harness.policy.PolicyGate`. The center of
the runtime is now alignment (does this fit the human's intent?); governance is
demoted to a proof layer that runs **after** alignment and only proves/enforces
safety (judgment proof, review, deny by risk/mode).

Key inversion vs the old flow: governance can only *tighten*. It can elevate an
alignment ``allow`` into a human judgment or a deny, but it can never overturn an
alignment ``deny``, ``redirect``, or ``constrain`` into execution.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from .._utils import compute_fingerprint
from ..policy import PolicyGate
from ..types import PolicyDecision

GovernanceOutcome = Literal["execute", "ask_judgment", "redirect", "deny"]


class GovernanceProof(TypedDict):
    """The proof governance attaches beneath an alignment decision."""

    outcome: GovernanceOutcome
    reason: str
    alignment_decision_id: str
    policy_decision: PolicyDecision | None


class GovernanceGate:
    """Run policy as a downstream proof of an already-aligned action."""

    def __init__(self, policy: PolicyGate, *, interactive: bool = True) -> None:
        self._policy = policy
        self._interactive = interactive

    def prove(
        self,
        alignment: dict[str, Any],
        *,
        agent: dict[str, Any],
        spec: dict[str, Any],
        state: dict[str, Any],
        arguments: dict[str, Any],
    ) -> GovernanceProof:
        verdict = alignment["decision"]
        ad_id = alignment["id"]

        # Alignment is primary. A deny or redirect never reaches policy.
        if verdict == "deny":
            return _proof("deny", "alignment denied: outside the intent field", ad_id, None)
        if verdict == "redirect":
            return _proof("redirect", "alignment redirected before governance", ad_id, None)
        if verdict == "ask_judgment":
            # Alignment demands judgment. Governance still consults policy — not
            # to overturn it (policy can only tighten to deny), but to label the
            # interrupt correctly: plan-mode/side-effect review vs approval
            # compatibility. A policy that would merely allow leaves the label
            # to default to the broad judgment path; an interrupt-class label is
            # carried through.
            decision = self._consult_policy(
                agent=agent, spec=spec, state=state, arguments=arguments
            )
            d = decision["decision"]
            if d == "deny":
                return _proof("deny", f"governance denied: {decision['reason']}", ad_id, decision)
            if d in ("require_approval", "require_review"):
                return _proof(
                    "ask_judgment",
                    f"alignment requires human judgment ({d}): {decision['reason']}",
                    ad_id,
                    decision,
                )
            return _proof("ask_judgment", "alignment requires human judgment", ad_id, None)

        if verdict == "constrain":
            return _proof(
                "ask_judgment",
                "alignment requires a constrained human judgment before execution",
                ad_id,
                None,
            )

        # allow — alignment lets it through; governance must still prove safety.
        # An explicit approval-named governance requirement from alignment
        # forces judgment; the name is a proof obligation, not the HITL model.
        if any(r.get("kind") == "approval" for r in alignment.get("governance_requirements", [])):
            return _proof("ask_judgment", "alignment attached an approval requirement", ad_id, None)

        decision = self._consult_policy(agent=agent, spec=spec, state=state, arguments=arguments)
        return self._from_policy(decision, ad_id)

    # ------------------------------------------------------------------

    def _consult_policy(
        self,
        *,
        agent: dict[str, Any],
        spec: dict[str, Any],
        state: dict[str, Any],
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        fingerprint = compute_fingerprint({"tool": spec["name"], "args": arguments})
        return self._policy.decide(
            {
                "agent": agent,  # type: ignore[typeddict-item]
                "skill": None,
                "tool_spec": spec,  # type: ignore[typeddict-item]
                "state": state,  # type: ignore[typeddict-item]
                "requested_action": {
                    "kind": "tool_call",
                    "tool_name": spec["name"],
                    "arguments": arguments,
                    "target": None,
                    "fingerprint": fingerprint,
                },
                "permission_mode": state["permission_mode"],
                "interactive": self._interactive,
            }
        )

    def _from_policy(self, decision: PolicyDecision, ad_id: str) -> GovernanceProof:
        d = decision["decision"]
        if d == "allow":
            return _proof("execute", "governance proved safe", ad_id, decision)
        if d in ("require_approval", "require_review"):
            return _proof(
                "ask_judgment", f"governance requires {d}: {decision['reason']}", ad_id, decision
            )
        return _proof("deny", f"governance denied: {decision['reason']}", ad_id, decision)


def _proof(
    outcome: GovernanceOutcome,
    reason: str,
    alignment_decision_id: str,
    policy_decision: PolicyDecision | None,
) -> GovernanceProof:
    return GovernanceProof(
        outcome=outcome,
        reason=reason,
        alignment_decision_id=alignment_decision_id,
        policy_decision=policy_decision,
    )


__all__ = ["GovernanceGate", "GovernanceOutcome", "GovernanceProof"]
