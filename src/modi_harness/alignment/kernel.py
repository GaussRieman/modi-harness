"""AlignmentKernel — the runtime's primary decision (plan N4.3).

Order of authority, per the model-first rule:

1. **Model first.** A semantic ``judge`` answers "is this action inside the
   intent field?" — returning a verdict, which boundaries it matched, and
   whether it sees drift. When a model is available, its reasoning is the base
   decision.
2. **Deterministic floor, escalate-only.** A narrow set of structural rules can
   only *raise* severity, never lower it: a hard/deny boundary the model flagged
   denies outright; an action kind outside the autonomy scope denies; an impact
   signal the scope marks judgment-worthy (external commitment, irreversibility,
   scope drift, …) forces at least ``ask_judgment``; risk above the scope's
   no-judgment ceiling forces at least ``ask_judgment``.

The floor proves safety; it does not replace the model. With no model (cold
start / gateway down) the base is ``allow`` and only the structural floor runs —
so the runtime still moves, but never past a structural red line.

Governance is *downstream*: the decision attaches ``GovernanceRequirement``s
(what governance must still prove) rather than final policy verdicts.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from .._utils import new_ulid
from ..actions.proposal import ActionProposal
from ..autonomy.scope import AutonomyScope
from ..intent.types import HumanIntentContext, IntentBoundary
from .types import AlignmentDecision, AlignmentVerdict, BoundaryHit, GovernanceRequirement

# A judge receives (proposal, intent, scope) and returns a dict with keys:
# ``verdict`` (AlignmentVerdict), ``matched_boundary_ids`` (list[str]),
# ``drift`` (bool), ``reason`` (str). May be None (no model) and may raise.
Judge = Callable[..., dict[str, Any]]

_VERDICT_RANK: dict[AlignmentVerdict, int] = {
    "allow": 0,
    "redirect": 1,
    "constrain": 2,
    "ask_judgment": 3,
    "deny": 4,
}
_RANK_TO_VERDICT = {rank: v for v, rank in _VERDICT_RANK.items()}

_RISK_ORDER: dict[str, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}

# How a ``requires_judgment_for`` token maps onto a proposal. Impact-field tokens
# read the boolean impact signal; ``*_kind`` tokens match the action kind.
_IMPACT_TOKENS = {
    "external_commitment",
    "irreversible",
    "changes_scope_or_goal",
    "user_visible_state_changes",
}
_KIND_TOKENS = {"tool_call", "output_finalize", "stage_transition", "memory_write"}


def align_action(
    *,
    proposal: ActionProposal,
    intent: HumanIntentContext,
    scope: AutonomyScope,
    judge: Judge | None = None,
    agent: Any | None = None,
) -> AlignmentDecision:
    """Decide whether a proposed action fits the human intent field."""
    del agent  # reserved: agent profile may refine semantic judgment later

    model_result = _run_judge(judge, proposal, intent, scope)
    model_judged = model_result is not None

    base: AlignmentVerdict = "allow"
    reason = "no structural concern"
    matched_ids: list[str] = []
    if model_result is not None:
        base = _coerce_verdict(model_result.get("verdict"))
        reason = str(model_result.get("reason") or "model judgment")
        matched_ids = list(model_result.get("matched_boundary_ids") or [])

    boundary_hits = _resolve_boundary_hits(matched_ids, intent["boundaries"])
    verdict = base
    reasons: list[str] = [reason]
    requirements: list[GovernanceRequirement] = []

    # Floor 1 — a matched hard/deny boundary is a true red line.
    for hit in boundary_hits:
        if hit["severity"] == "hard" and hit["escalation"] == "deny":
            verdict = _max(verdict, "deny")
            reasons.append(f"hard/deny boundary hit: {hit['statement']}")
        elif hit["severity"] == "hard" and hit["escalation"] == "ask":
            verdict = _max(verdict, "ask_judgment")
            reasons.append(f"hard/ask boundary hit: {hit['statement']}")

    # Floor 2 — the action kind must be inside the current autonomy scope.
    if proposal["kind"] not in scope["allowed_action_kinds"]:
        verdict = _max(verdict, "deny")
        reasons.append(
            f"action kind {proposal['kind']!r} not allowed under {scope['mode']} autonomy"
        )

    # Floor 3 — impact signals the scope marks judgment-worthy.
    for token in scope["requires_judgment_for"]:
        if _proposal_triggers(token, proposal):
            verdict = _max(verdict, "ask_judgment")
            reasons.append(f"impact requires judgment: {token}")

    # Floor 4 — risk above the no-judgment ceiling for this scope.
    ceiling = scope["max_tool_risk_without_judgment"]
    if _RISK_ORDER.get(proposal["impact"]["risk_level"], 0) > _RISK_ORDER.get(ceiling, 0):
        verdict = _max(verdict, "ask_judgment")
        reasons.append(
            f"risk {proposal['impact']['risk_level']} exceeds no-judgment ceiling {ceiling}"
        )

    if verdict == "ask_judgment":
        requirements.append(
            GovernanceRequirement(kind="approval", reason="alignment requires human judgment")
        )

    return AlignmentDecision(
        id=new_ulid(),
        decision=verdict,
        reason="; ".join(reasons),
        action_id=proposal["id"],
        intent_version=proposal["intent_version"],
        stage_id=proposal["stage_id"],
        boundary_hits=boundary_hits,
        governance_requirements=requirements,
        model_judged=model_judged,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_judge(
    judge: Judge | None,
    proposal: ActionProposal,
    intent: HumanIntentContext,
    scope: AutonomyScope,
) -> dict[str, Any] | None:
    if judge is None:
        return None
    try:
        result = judge(proposal=proposal, intent=intent, scope=scope)
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    return result


def _coerce_verdict(value: Any) -> AlignmentVerdict:
    if value in _VERDICT_RANK:
        return cast(AlignmentVerdict, value)
    return "ask_judgment"  # unknown model output is treated cautiously


def _max(current: AlignmentVerdict, target: AlignmentVerdict) -> AlignmentVerdict:
    rank = max(_VERDICT_RANK[current], _VERDICT_RANK[target])
    return _RANK_TO_VERDICT[rank]


def _resolve_boundary_hits(
    matched_ids: list[str], boundaries: list[IntentBoundary]
) -> list[BoundaryHit]:
    by_id = {b["id"]: b for b in boundaries}
    hits: list[BoundaryHit] = []
    for bid in matched_ids:
        b = by_id.get(bid)
        if b is None:
            continue
        hits.append(
            BoundaryHit(
                boundary_id=b["id"],
                severity=b["severity"],
                escalation=b["escalation"],
                statement=b["statement"],
            )
        )
    return hits


def _proposal_triggers(token: str, proposal: ActionProposal) -> bool:
    if token in _KIND_TOKENS:
        return proposal["kind"] == token
    if token in _IMPACT_TOKENS:
        return bool(proposal["impact"].get(token))
    return False

__all__ = ["Judge", "align_action"]
