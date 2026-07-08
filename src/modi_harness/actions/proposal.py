"""Normalize model-proposed actions into ``ActionProposal`` (plan N4.1).

Every consequential operation — a tool call, an output finalization, a stage
transition, a memory write — is normalized here *before* it reaches alignment
or governance. The proposal carries:

- **intent lineage** (``intent_version`` + ``stage_id``) so every later decision
  and trace event can be tied back to the human intent that was in force;
- a mechanically-derived **``ActionImpact``** describing the alignment-relevant
  consequences (external commitment, irreversibility, scope drift, …).

The impact computed here is *deterministic and descriptive* — it reports what a
tool call would do from its spec + args. It does **not** decide anything; the
``AlignmentKernel`` (N4.3) makes the model-first semantic judgment, and
governance proves safety beneath that. Keeping impact mechanical means the
kernel's model judgment always sees a stable, honest description of the action.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from .._utils import new_ulid
from ..types import RiskLevel, ToolSpec

ActionKind = Literal[
    "tool_call",
    "output_finalize",
    "stage_transition",
    "memory_write",
]

# Tool names that are not plain tool calls. The model still emits them as tool
# calls; the runtime knows they carry runtime-level meaning and routes them to a
# distinct action kind so alignment and governance can treat them specially.
_KIND_BY_TOOL: dict[str, ActionKind] = {
    "submit_output": "output_finalize",
    "finalize_output": "output_finalize",
    "stage_transition": "stage_transition",
    "transition_stage": "stage_transition",
    "memory_write": "memory_write",
    "write_memory": "memory_write",
    "propose_memory": "memory_write",
    "save_memory": "memory_write",
}


class ActionImpact(TypedDict):
    """Mechanically-derived, alignment-relevant consequences of an action.

    Descriptive, never a verdict. Field names line up with the impact signals
    named in ``AutonomyScope.requires_judgment_for`` so the kernel can match a
    scope's judgment triggers against an action without translation.
    """

    risk_level: RiskLevel
    side_effect: bool
    external_commitment: bool
    irreversible: bool
    user_visible_state_changes: bool
    changes_scope_or_goal: bool
    sensitive_data: bool
    cost_impact: Literal["none", "low", "medium", "high"]


class ActionProposal(TypedDict):
    """A normalized, lineage-carrying description of an action to be aligned."""

    id: str
    kind: ActionKind
    summary: str
    tool_name: str
    arguments: dict[str, Any]
    intent_version: int
    stage_id: str
    expected_outcome: str | None
    impact: ActionImpact


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


def from_tool_call(
    tool_call: dict[str, Any],
    *,
    spec: ToolSpec,
    intent_version: int,
    stage_id: str,
    expected_outcome: str | None = None,
) -> ActionProposal:
    """Normalize a ``ToolCallProposal``-shaped dict into an ``ActionProposal``."""
    tool_name = tool_call["tool_name"]
    args = dict(tool_call.get("arguments") or {})
    kind = _KIND_BY_TOOL.get(tool_name, "tool_call")
    impact = _impact_from(spec, args, kind)
    return ActionProposal(
        id=new_ulid(),
        kind=kind,
        summary=_summarize(kind, tool_name, args),
        tool_name=tool_name,
        arguments=args,
        intent_version=intent_version,
        stage_id=stage_id,
        expected_outcome=expected_outcome,
        impact=impact,
    )


# ---------------------------------------------------------------------------
# impact derivation
# ---------------------------------------------------------------------------


def _impact_from(spec: ToolSpec, args: dict[str, Any], kind: ActionKind) -> ActionImpact:
    tags = set(spec.get("tags") or [])
    risk: RiskLevel = spec["risk_level"]
    side_effect = bool(spec.get("side_effect")) or "side_effect" in tags

    # External *commitment* means committing to the outside world — which needs a
    # side effect. A read-only GET that merely reaches the network (a research
    # agent fetching a page) is NOT a commitment; treating it as one would force
    # every fetch through human judgment, against the model-first rule. So an
    # arg-derived external endpoint counts only when the call also has a side
    # effect; an explicit ``external_commitment`` tag always counts.
    external = "external_commitment" in tags or (
        side_effect and _args_reach_external(args)
    )
    irreversible = "irreversible" in tags
    sensitive = "sensitive_data" in tags
    # A stage transition reframes the work itself; an output finalization and any
    # explicitly side-effecting tool change user-visible state.
    changes_scope = kind == "stage_transition" or "changes_scope_or_goal" in tags
    user_visible = (
        kind in ("output_finalize", "stage_transition")
        or side_effect
        or "user_visible" in tags
    )

    return ActionImpact(
        risk_level=risk,
        side_effect=side_effect,
        external_commitment=external,
        irreversible=irreversible,
        user_visible_state_changes=user_visible,
        changes_scope_or_goal=changes_scope,
        sensitive_data=sensitive,
        cost_impact=_cost_impact(tags),
    )


# Schemes that talk to the world. ``file``/``data`` and loopback hosts do not.
_EXTERNAL_SCHEMES = ("http://", "https://", "ftp://", "ssh://", "smtp://")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def _args_reach_external(args: dict[str, Any]) -> bool:
    """Best-effort: does any string argument point at a remote endpoint?

    Arg-sensitive so the *same* tool yields different impact for a remote URL vs
    a local file — the kernel then judges the remote case more carefully.
    """
    for value in _iter_strings(args):
        low = value.strip().lower()
        if not low.startswith(_EXTERNAL_SCHEMES):
            continue
        rest = low.split("://", 1)[1]
        host = rest.split("/", 1)[0].split(":", 1)[0]
        if host in _LOCAL_HOSTS:
            continue
        return True
    return False


def _iter_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_iter_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_iter_strings(v))
    return out


def _cost_impact(tags: set[str]) -> Literal["none", "low", "medium", "high"]:
    for level in ("high", "medium", "low"):
        if f"cost:{level}" in tags:
            return level
    return "none"


def _summarize(kind: ActionKind, tool_name: str, args: dict[str, Any]) -> str:
    if kind == "output_finalize":
        status = args.get("status")
        return f"finalize output (status={status})" if status else "finalize output"
    if kind == "stage_transition":
        to = args.get("to") or args.get("stage")
        return f"transition stage -> {to}" if to else "transition stage"
    if kind == "memory_write":
        return "write memory"
    return f"call {tool_name}"


__all__ = ["ActionImpact", "ActionKind", "ActionProposal", "from_tool_call"]
