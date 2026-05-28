# Runtime Adapter

## Module

`modi_harness.runtime`

## Purpose

Run the single-agent Modi Harness loop on LangGraph.

Contract: see [`../architecture/04-runtime-adapter.md`](../architecture/04-runtime-adapter.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Framework Choice

Use LangGraph for V0.1.

Runtime Adapter maps Modi `AgentState` to a LangGraph state graph and uses LangGraph for loop orchestration, checkpointing, interrupts, and resume.

Do not leak LangGraph types into public API responses. Internal runtime code can use LangGraph directly.

## Design

Implement:

- `RuntimeAdapter`
- LangGraph graph builder
- `run(request) -> RunTaskResponse`
- `resume(run_id, input) -> RunTaskResponse`
- checkpoint load/save through Workspace Manager
- step limit guard
- interrupt handling
- denied-action guard (pre-dispatch)
- repair-loop budget for malformed tool calls and output validation failures
- hook dispatch at all event points

## Identifiers

- `run_id` generated here via ULID.
- `root_run_id == run_id` for V0.1.
- `thread_id` accepted from request; passed through state and trace.

## Graph Nodes

```text
load_agent
load_skills
build_context
model_step
route_response
execute_tool
validate_output
persist_state
```

Conditional edges → continue, interrupt, review, failure, finish.

## Rules (impl-specific)

- State is persisted after every behavior-changing transition.
- Denied actions are stored in `AgentState.denied_actions` and consulted both here and in Tool Gateway.
- Hook feedback that blocks a tool is recorded as `DeniedAction`.
- Repair loop cap is configurable; on overflow the run fails with a `repair_budget_exhausted` error.
- A custom state machine is a fallback only when LangGraph blocks a required harness behavior.

## Settings

```text
MODI_MAX_STEPS=20
MODI_REPAIR_BUDGET=3
```

## Tests

- successful no-tool run
- tool-call run end-to-end
- approval interrupt
- denial prevents retry (same fingerprint blocked)
- max-step failure
- resume from checkpoint
- hook block converted to denial
- repair loop on malformed tool call within budget
- repair budget exhaustion fails cleanly
- plan mode never executes side effects
