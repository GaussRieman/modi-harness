"""Build the V0.2 LangGraph main runtime.

The graph is intentionally small:

::

    START -> setup -> brain_step -> route_after_brain_step -> execute_tool | validate_output
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
    sg.add_node("brain_step", nodes.brain_step_node)
    sg.add_node("execute_tool", nodes.execute_tool_node)
    sg.add_node("await_interaction", nodes.await_interaction_node)
    sg.add_node("await_judgment", nodes.await_judgment_node)
    sg.add_node("validate_output", nodes.validate_output_node)
    sg.add_node("max_steps_exceeded", nodes.max_steps_exceeded_node)

    sg.add_edge(START, "setup")
    sg.add_edge("setup", "brain_step")
    sg.add_conditional_edges(
        "brain_step",
        nodes.route_after_brain_step,
        {
            "execute_tool": "execute_tool",
            "await_interaction": "await_interaction",
            "await_judgment": "await_judgment",
            "validate_output": "validate_output",
            "__end__": END,
        },
    )
    sg.add_conditional_edges(
        "execute_tool",
        nodes.route_after_tool,
        {
            "brain_step": "brain_step",
            "await_interaction": "await_interaction",
            "await_judgment": "await_judgment",
            "max_steps_exceeded": "max_steps_exceeded",
            "__end__": END,
        },
    )
    sg.add_conditional_edges(
        "await_interaction",
        nodes.route_after_interaction,
        {"brain_step": "brain_step", "await_interaction": "await_interaction", "__end__": END},
    )
    sg.add_conditional_edges(
        "await_judgment",
        nodes.route_after_judgment,
        {"brain_step": "brain_step", "__end__": END},
    )
    sg.add_conditional_edges(
        "validate_output",
        nodes.route_after_validate,
        {
            "brain_step": "brain_step",
            "max_steps_exceeded": "max_steps_exceeded",
            "__end__": END,
        },
    )
    sg.add_edge("max_steps_exceeded", END)

    return sg.compile(checkpointer=checkpointer)


__all__ = ["CONFIG_DEPS_KEY", "GraphDeps", "TraceMiddleware", "build_main_graph"]
