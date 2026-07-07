"""LangGraph state TypedDict for the V0.2 main graph.

``MainGraphState`` extends :class:`modi_harness.types.AgentState` with three
transient fields used by graph nodes to hand work to one another between
transitions:

- ``pending_tool_calls`` — ToolCallProposals produced by ``model_turn`` and
  consumed by ``execute_tool``.
- ``pending_draft`` — the assistant's final answer awaiting validation.
  Carries a ``dict`` when the model called ``submit_output`` (SDK-parsed
  args), or a ``str`` when the model emitted JSON-as-text in the assistant
  message; the OutputController handles both.
- ``max_steps`` — graph-local step cap so routing edges can compare without
  reaching into deps.

These three are *transient* by convention: nodes clear them on consumption.
They survive across checkpoints (so resume works after an interrupt), but
should never carry meaningful data once their consumer has run.
"""

from __future__ import annotations

from typing import Any

from ..autonomy.scope import AutonomyScope
from ..intent.types import IntentClarity
from ..loop.types import LoopContinuationDecision, LoopState, StepRecord
from ..types import AgentState, ToolCallProposal


class MainGraphState(AgentState, total=False):
    pending_tool_calls: list[ToolCallProposal]
    pending_draft: str | dict[str, Any] | None
    max_steps: int
    # Intent-aligned runtime: derived during setup, before the first model turn.
    # ``human_intent`` (with intent_version / stage_id) lives on AgentState;
    # these two are the derived clarity estimate and the enforced autonomy scope.
    intent_clarity: IntentClarity
    autonomy_scope: AutonomyScope
    # Brain-Agent Loop runtime: optional during migration, durable in
    # checkpoints once setup initializes the loop.
    loop_state: LoopState
    step_records: list[StepRecord]
    current_step: StepRecord | None
    last_continuation_decision: LoopContinuationDecision | None


__all__ = ["MainGraphState"]
