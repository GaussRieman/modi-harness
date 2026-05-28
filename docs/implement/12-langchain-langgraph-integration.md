# LangChain/LangGraph Integration

## Purpose

Maximize reuse of LangChain and LangGraph while keeping Modi Harness focused on governance, workspace, policy, skills, output control, and traceability.

## Position

Simple agents should remain easy to build with raw LangChain and LangGraph.

Modi Harness is added when the user needs:

- Markdown-defined agents
- skill packages
- governed tool execution
- approval and denial tracking
- workspace persistence
- output validation
- trace and audit records

## Integration Points

- LangChain chat models through Model Adapter.
- LangChain tools through Tool Gateway.
- LangGraph state graphs through Runtime Adapter.
- LangGraph checkpoint and interrupt primitives where practical.
- LangChain message conversion from `ContextPack`.
- Raw LangChain/LangGraph handles exposed for advanced extension when safe.

## Boundaries

- Modi contracts remain stable even if framework internals change.
- Tool execution still passes through Policy Gate.
- Model-requested actions are proposals until Tool Gateway validates them.
- Workspace, policy, output, and trace are Modi concerns, not delegated to LangChain/LangGraph.

## Escape Hatch

Users can bypass Modi Harness and use LangChain/LangGraph directly for simple agents.

Users can also bring existing LangChain tools, chat models, and graph components into Modi Harness through adapters.
