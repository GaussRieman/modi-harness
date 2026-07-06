"""ActionGateway — the action-centered execution path (plan N5).

This replaces ``ToolGateway`` as the runtime's center. The decision flow is now

    tool_call
    -> registry lookup / schema validation / denied-retry / pre-hooks   (shared)
    -> ActionProposal normalization  (intent lineage + mechanical impact)
    -> AlignmentKernel               (model-first: does this fit the intent?)
    -> GovernanceGate                (prove safety beneath alignment)
    -> execute / dry-run / interrupt / deny
    -> post-hooks / normalized untrusted result                          (shared)

Alignment is the *first* decision point; governance is a downstream proof that
can only tighten. The pre-/post phases are reused verbatim from ``ToolGateway``
so hook dispatch and execution are never duplicated.

When the state carries no intent field (a cold subagent before it self-heals,
or a legacy caller), the gateway falls back to the policy-only path so the
runtime still moves — it never executes past a structural red line because the
fallback is the same governed policy decision as before.
"""
from __future__ import annotations

from typing import Any, Literal, cast

from .._utils import now_iso
from ..alignment import AlignmentDecision, align_action
from ..alignment.kernel import Judge
from ..autonomy.scope import AutonomyScope, derive_autonomy_scope
from ..governance import GovernanceGate, GovernanceProof
from ..hooks import HookDispatcher
from ..intent.types import HumanIntentContext, IntentClarity
from ..policy import PolicyGate
from ..tools.gateway import (
    ToolDispatchResult,
    ToolGateway,
    _Prepared,
    _record,
)
from ..types import (
    AgentProfile,
    AgentState,
    PolicyDecision,
    ToolCallProposal,
)
from .integrity import hash_action, hash_tool_call
from .proposal import ActionProposal, from_tool_call

# The policy verdicts a synthesized decision may carry (mirror of
# ``PolicyDecision['decision']``).
PolicyVerdict = Literal["allow", "deny", "require_approval", "require_review"]


class ActionGateway:
    """Aligns and governs model-requested actions, then executes them.

    Composes a ``ToolGateway`` for the shared registry/schema/hook/execute
    machinery and inserts alignment + governance as the middle decision. Exposes
    the same ``execute_tool_call`` signature as ``ToolGateway`` so it drops into
    the graph deps without touching the nodes.
    """

    def __init__(
        self,
        *,
        registry: Any,
        policy: PolicyGate,
        hooks: HookDispatcher,
        result_inline_limit_bytes: int,
        interactive: bool | None = None,
        judge: Judge | None = None,
    ) -> None:
        self._tools = ToolGateway(
            registry=registry,
            policy=policy,
            hooks=hooks,
            result_inline_limit_bytes=result_inline_limit_bytes,
            interactive=interactive,
        )
        self._governance = GovernanceGate(
            policy, interactive=self._tools._interactive
        )
        self._judge = judge
        # Reviewed action hashes, keyed by tool_call_id. An entry is written when
        # a call is routed to human judgment; the resumed (elevated) call must
        # hash the same or it is refused.
        self._reviewed: dict[str, str] = {}

    @property
    def _registry(self) -> Any:
        """Expose the composed gateway's registry (graph nodes read specs)."""
        return self._tools._registry

    # ------------------------------------------------------------------
    # public — mirrors ToolGateway.execute_tool_call
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

        prepared = self._tools._prepare(
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

        intent = state.get("human_intent")
        scope = self._scope_for(state, intent)
        if intent is None or scope is None:
            # No intent field yet: fall back to the governed policy-only path.
            return self._tools._decide_and_finish(
                proposal,
                started_at=started_at,
                prepared=prepared,
                agent=agent,
                state=state,
                graph_deps=graph_deps,
            )

        action = from_tool_call(
            cast("dict[str, Any]", proposal),
            spec=prepared.spec,
            intent_version=state.get("intent_version", intent["version"]),
            stage_id=state.get("stage_id", intent["current_stage"]["id"]),
        )

        # Integrity: a resumed (elevated) call must match what was reviewed.
        guard = self._integrity_guard(proposal, action, state, started_at)
        if guard is not None:
            return guard
        if self._is_approved_resume(proposal, state):
            result = self._tools._finish(
                proposal,
                started_at=started_at,
                prepared=prepared,
                decision=_approved_resume_policy_decision(),
                state=state,
                graph_deps=graph_deps,
            )
            decision = _approved_resume_alignment_decision(action)
            return self._stamp(result, action, decision)

        decision = align_action(
            proposal=action,
            intent=intent,
            scope=scope,
            judge=self._judge,
            agent=agent,
        )
        proof = self._governance.prove(
            decision,  # type: ignore[arg-type]
            agent=agent,  # type: ignore[arg-type]
            spec=prepared.spec,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            arguments=proposal["arguments"],
        )
        return self._dispatch_outcome(
            proposal,
            started_at=started_at,
            prepared=prepared,
            action=action,
            decision=decision,
            proof=proof,
            state=state,
            graph_deps=graph_deps,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _scope_for(
        self, state: AgentState, intent: HumanIntentContext | None
    ) -> AutonomyScope | None:
        scope = state.get("autonomy_scope")
        if scope is not None:
            return cast("AutonomyScope", scope)
        clarity = state.get("intent_clarity")
        if intent is None or clarity is None:
            return None
        return derive_autonomy_scope(cast("IntentClarity", clarity), intent)

    def _integrity_guard(
        self,
        proposal: ToolCallProposal,
        action: ActionProposal,
        state: AgentState,
        started_at: str,
    ) -> ToolDispatchResult | None:
        """Refuse a resumed elevated action that doesn't match the reviewed one.

        Only enforced when (a) the call runs under an elevated permission mode
        (a resume after human judgment) and (b) we recorded a reviewed hash for
        this tool_call_id. A tampered proposal hashes differently → hard deny.
        """
        if state["permission_mode"] != "trust":
            return None
        reviewed_hash = self._reviewed.get(proposal["tool_call_id"])
        if reviewed_hash is None:
            return None
        if reviewed_hash == hash_action(action):
            return None
        record = _record(proposal, started_at, decision="deny", result=None)
        result = ToolDispatchResult(outcome="error", record=record)
        result.action_id = action["id"]
        result.error_message = "integrity check failed: resumed action differs from reviewed action"
        return result

    def _is_approved_resume(
        self, proposal: ToolCallProposal, state: AgentState
    ) -> bool:
        """True only for the exact action a human already approved.

        This is narrower than ``permission_mode='trust'``. Trust mode alone
        still runs normal alignment/governance; an approved resume may skip
        re-asking alignment because the original reviewed proposal has already
        been bound mechanically by hash.
        """
        if state["permission_mode"] != "trust":
            return False
        approved_hash = state.get("approved_action_hash")
        if not isinstance(approved_hash, str) or not approved_hash:
            return False
        return approved_hash == hash_tool_call(cast("dict[str, Any]", proposal))

    def _dispatch_outcome(
        self,
        proposal: ToolCallProposal,
        *,
        started_at: str,
        prepared: _Prepared,
        action: ActionProposal,
        decision: AlignmentDecision,
        proof: GovernanceProof,
        state: AgentState,
        graph_deps: Any | None,
    ) -> ToolDispatchResult:
        outcome = proof["outcome"]

        if outcome == "deny" or outcome == "redirect":
            record = _record(proposal, started_at, decision="deny", result=None)
            result = ToolDispatchResult(
                outcome="error",
                record=record,
                decision=_synth_policy(proof, "deny"),
            )
            result.error_message = proof["reason"]
            return self._stamp(result, action, decision)

        if outcome == "ask_judgment":
            # Record the reviewed action so the resumed call can be verified.
            self._reviewed[proposal["tool_call_id"]] = hash_action(action)
            # The interrupt label (review vs approval) comes from policy via the
            # proof — never hardcoded here. Default to require_approval when
            # governance attached no policy decision.
            pd = proof["policy_decision"]
            label = pd["decision"] if pd is not None else "require_approval"
            record = _record(proposal, started_at, decision=label, result=None)
            result = ToolDispatchResult(
                outcome="interrupt",
                record=record,
                decision=_synth_policy(proof, label),
            )
            return self._stamp(result, action, decision)

        # execute — alignment allowed and governance proved safe.
        policy_decision = proof["policy_decision"] or _synth_policy(proof, "allow")
        result = self._tools._finish(
            proposal,
            started_at=started_at,
            prepared=prepared,
            decision=policy_decision,
            state=state,
            graph_deps=graph_deps,
        )
        return self._stamp(result, action, decision)

    @staticmethod
    def _stamp(
        result: ToolDispatchResult,
        action: ActionProposal,
        decision: AlignmentDecision,
    ) -> ToolDispatchResult:
        result.action_id = action["id"]
        result.alignment_decision_id = decision["id"]
        result.action_proposal = cast("dict[str, Any]", action)
        result.alignment_decision = cast("dict[str, Any]", decision)
        return result


def _synth_policy(proof: GovernanceProof, decision: PolicyVerdict) -> PolicyDecision:
    """Synthesize a PolicyDecision so the graph's resume path stays unchanged.

    When governance consulted the real PolicyGate, that decision is carried
    through; otherwise (alignment deny/redirect/judgment before policy) we build
    a minimal one that the interrupt/error handling in the nodes can read.
    """
    if proof["policy_decision"] is not None:
        return proof["policy_decision"]
    return PolicyDecision(
        decision=decision,
        reason=proof["reason"],
        approval_id=None,
        review_requirement=None,
        denied_retry=False,
        audit={"alignment_decision_id": proof["alignment_decision_id"]},
    )


def _approved_resume_policy_decision() -> PolicyDecision:
    return PolicyDecision(
        decision="allow",
        reason="human approved reviewed action",
        approval_id=None,
        review_requirement=None,
        denied_retry=False,
        audit={"approved_resume": True},
    )


def _approved_resume_alignment_decision(action: ActionProposal) -> AlignmentDecision:
    return AlignmentDecision(
        id=f"{action['id']}:approved",
        decision="allow",
        reason="human approved reviewed action",
        action_id=action["id"],
        intent_version=action["intent_version"],
        stage_id=action["stage_id"],
        boundary_hits=[],
        governance_requirements=[],
        model_judged=False,
    )


__all__ = ["ActionGateway"]
