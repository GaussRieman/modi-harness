# Execution Runtime

## Assembly

`ModiHarness` owns reusable capabilities: model adapters, Context Manager,
Alignment Kernel, Policy Gate, Output Controller, Hook registry, and kernel Tool
registry. It does not own Agents, threads, workspace, memory, or checkpoint
infrastructure.

`ModiSession` binds a Harness to Agents and caller-provided infrastructure. It
constructs `WorkspaceManager`, `MemoryStore`, `HookDispatcher`, `ActionGateway`
(the action-centered execution path that wraps `ToolGateway`), Agent indexes,
and `GraphDeps`, then creates one `HarnessGraphAdapter`.

## Graph

`graph.builder` compiles this LangGraph:

```text
START -> setup -> brain_step
                    |  \
                    |   -> validate_output -> END | brain_step
                    v
               execute_tool -> brain_step | await_interaction | END
                                      |
                                      -> brain_step | END
```

`max_steps_exceeded` is the bounded terminal path. Output repair uses the
Session repair budget. Task and interaction protocols are handled as native
graph Tools and state transitions, not arbitrary external handlers.
`brain_step` is the semantic control node: it asks Brain for one
`StepDecision`, records a `StepRecord`, and stages at most one
`RuntimeOperationProposal`.

## Adapter responsibilities

`HarnessGraphAdapter` is a thin boundary around the compiled graph. It:

- seeds `MainGraphState` and stable run/thread identifiers;
- attaches `GraphDeps` through `RunnableConfig`;
- invokes, streams, or resumes via LangGraph `Command`;
- flushes pending trace events;
- projects final state into Modi response and stream types.

Checkpoint state is owned by the injected LangGraph `BaseCheckpointSaver`.
`checkpoint.factory` builds configured memory, SQLite, or Postgres backends.

## Model and subagents

`ModelAdapter` is the only provider-message conversion boundary. In the main
runtime it is called by the structured slow Brain planner, which exposes only
the `submit_step_decision` protocol tool to the model. Business tools are not
called directly by the model; Brain requests them as runtime operations and the
Loop/Harness path executes them. `ModelAdapterCache` holds per-Agent model
overrides.

Subagents use the same graph and alignment dependencies. The dispatcher
creates child run lineage, narrows permissions and depth, and propagates denied
actions and workspace references back to the parent.

## Source entry points

- `api/harness.py`, `api/session.py`
- `graph/builder.py`, `graph/nodes.py`, `graph/harness_adapter.py`
- `graph/state.py`, `graph/deps.py`
- `actions/gateway.py`, `alignment/kernel.py`
- `models/adapter.py`, `models/cache.py`
- `checkpoint/factory.py`, `subagent/dispatcher.py`
