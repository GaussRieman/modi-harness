"""MainGraphState alias sanity."""

from __future__ import annotations

from modi_harness.graph import MainGraphState
from modi_harness.types import AgentState


def test_main_graph_state_extends_agent_state() -> None:
    # MainGraphState adds transient fields on top of AgentState; the AgentState
    # keys must be present in the resolved annotations.
    main_keys = set(MainGraphState.__annotations__) | set(
        getattr(MainGraphState, "__optional_keys__", set())
    )
    agent_keys = set(AgentState.__annotations__)
    assert agent_keys.issubset(main_keys)


def test_main_graph_state_has_transient_fields() -> None:
    assert "pending_tool_calls" in MainGraphState.__annotations__
    assert "pending_draft" in MainGraphState.__annotations__
    assert "max_steps" in MainGraphState.__annotations__
