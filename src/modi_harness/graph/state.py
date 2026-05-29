"""LangGraph state TypedDict for the V0.2 main graph.

``MainGraphState`` extends :class:`modi_harness.types.AgentState` with three
transient fields used by graph nodes to hand work to one another between
transitions:

- ``pending_tool_calls`` — ToolCallProposals produced by ``model_turn`` and
  consumed by ``execute_tool``.
- ``pending_draft`` — the assistant message content when the model declined
  to call a tool; consumed by ``validate_output``.
- ``max_steps`` — graph-local step cap so routing edges can compare without
  reaching into deps.

These three are *transient* by convention: nodes clear them on consumption.
They survive across checkpoints (so resume works after an interrupt), but
should never carry meaningful data once their consumer has run.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from ..types import AgentState, ToolCallProposal


class MainGraphState(AgentState, total=False):
    pending_tool_calls: list[ToolCallProposal]
    pending_draft: str | None
    max_steps: int


__all__ = ["MainGraphState"]
