"""Build the V0.2 LangGraph main runtime.

The graph is intentionally small:

::

    START -> setup -> model_turn -> route_after_model -> execute_tool | validate_output
                          ^                                  |              |
                          |---- route_after_tool ------------+              |
                          |---- route_after_validate -----------------------+

The trace middleware is registered as a post-node callback that drains
``state["pending_trace_events"]`` to ``trace.jsonl`` after each transition.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from . import nodes
from .deps import CONFIG_DEPS_KEY, GraphDeps
from .state import MainGraphState
from .trace_middleware import TraceMiddleware


def build_main_graph(deps: GraphDeps, checkpointer: Any) -> Any:
    """Construct and compile the main LangGraph runtime."""
    sg = StateGraph(MainGraphState)
    sg.add_node("setup", nodes.setup_node)
    sg.add_node("model_turn", nodes.model_turn_node)
    sg.add_node("execute_tool", nodes.execute_tool_node)
    sg.add_node("validate_output", nodes.validate_output_node)

    sg.add_edge(START, "setup")
    sg.add_edge("setup", "model_turn")
    sg.add_conditional_edges(
        "model_turn",
        nodes.route_after_model,
        {"execute_tool": "execute_tool", "validate_output": "validate_output"},
    )
    sg.add_conditional_edges(
        "execute_tool",
        nodes.route_after_tool,
        {"model_turn": "model_turn", "__end__": END},
    )
    sg.add_conditional_edges(
        "validate_output",
        nodes.route_after_validate,
        {"model_turn": "model_turn", "__end__": END},
    )

    return sg.compile(checkpointer=checkpointer)


__all__ = ["build_main_graph", "CONFIG_DEPS_KEY", "GraphDeps", "TraceMiddleware"]
