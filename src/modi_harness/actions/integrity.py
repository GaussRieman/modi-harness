"""Action integrity: bind a reviewed action to its resume (plan N5).

When alignment/governance routes an action to human judgment, the runtime
interrupts and the human reviews *that specific action*. On resume the action
runs with elevated permission — so we must prove the action that runs is the
same one the human reviewed. Otherwise a model could propose a benign call, get
it approved, then swap the arguments on resume.

The guarantee is mechanical: hash the alignment-relevant shape of the proposal
(tool name + arguments + kind) at review time, carry the hash across the
interrupt, and verify the resumed proposal hashes the same. A mismatch is a
hard stop — the approval does not transfer to a different action.
"""
from __future__ import annotations

from typing import Any

from .._utils import compute_fingerprint
from .proposal import ActionProposal


def hash_action(proposal: ActionProposal) -> str:
    """Hash the alignment-relevant shape of an action.

    Covers what a human actually judged: *what* runs (tool name) and *with what*
    (arguments). ``kind`` is derived deterministically from the tool name, and
    lineage/impact are derived not reviewed, so all are excluded — a re-derived
    field must never invalidate a valid resume.
    """
    return compute_fingerprint(
        {
            "tool_name": proposal["tool_name"],
            "arguments": proposal["arguments"],
        }
    )


def hash_tool_call(tool_call: dict[str, Any]) -> str:
    """Hash a raw ``ToolCallProposal``-shaped dict the same way as a proposal.

    Lets the resume path verify integrity without re-normalizing into an
    ``ActionProposal`` first. Hashes the same fields as :func:`hash_action`
    (tool name + arguments) so the two always agree for the same action.
    """
    return compute_fingerprint(
        {
            "tool_name": tool_call["tool_name"],
            "arguments": dict(tool_call.get("arguments") or {}),
        }
    )


def verify_resumed_action(reviewed_hash: str, resumed: ActionProposal) -> bool:
    """True iff the resumed action matches the reviewed one.

    The match is on tool name + arguments — the bytes the human actually judged.
    A mismatch means the approval does not transfer to this action.
    """
    return reviewed_hash == hash_action(resumed)


__all__ = ["hash_action", "hash_tool_call", "verify_resumed_action"]
