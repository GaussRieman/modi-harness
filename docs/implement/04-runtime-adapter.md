# Runtime Adapter

## Module

`modi_harness.runtime`

## Purpose

Run the single-agent Modi Harness loop.

## Framework Choice

Use LangGraph for V0.1.

Runtime Adapter should map Modi `AgentState` to a LangGraph state graph and use LangGraph for loop orchestration, checkpointing, interrupts, and resume.

Do not leak LangGraph types into public API responses. Internal runtime code can use LangGraph directly.

## Design

Implement:

- `RuntimeAdapter`
- LangGraph graph builder
- `run(request) -> RunTaskResponse`
- `resume(run_id, input) -> RunTaskResponse`
- checkpoint load/save through Workspace Manager
- step limit
- interrupt handling
- denied-action guard

## Loop

```text
load agent
-> load skills
-> build context
-> model step
-> route tool call or output
-> persist state
-> continue / interrupt / finish
```

## Graph Nodes

V0.1 LangGraph nodes:

- `load_agent`
- `load_skills`
- `build_context`
- `model_step`
- `route_response`
- `execute_tool`
- `validate_output`
- `persist_state`

Conditional edges route to continue, interrupt, review, failure, or finish.

## Rules

- V0.1 is single-agent only.
- LangGraph is the default runtime engine.
- State is persisted after every transition that changes behavior.
- Denied actions are not retried unchanged.
- Hook feedback is stored as user feedback.
- Runtime coordinates modules; it does not implement policy, tools, model calls, or output validation inline.
- A custom state machine is only a fallback if LangGraph blocks a required harness behavior.
- Graph construction should stay readable enough that developers can inspect the runtime path from trace events.

## Tests

- successful no-tool run
- tool-call run
- approval interrupt
- denial prevents retry
- max-step failure
- resume from checkpoint
