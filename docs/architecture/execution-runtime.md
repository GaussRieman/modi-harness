# Execution Runtime

## Assembly

`ModiHarness` owns reusable capabilities: model adapters, Context Manager,
Policy Gate, Output Controller, Hook registry, and kernel Tool registry. It
does not own Agents, threads, workspace, memory, or checkpoint infrastructure.

`ModiSession` binds a Harness to Agents and caller-provided infrastructure. It
constructs `WorkspaceManager`, `MemoryStore`, `HookDispatcher`, `ToolGateway`,
Agent indexes, and `GraphDeps`, then creates one `HarnessGraphAdapter`.

## Graph

`graph.builder` compiles this LangGraph:

```text
START -> setup -> model_turn
                    |  \
                    |   -> validate_output -> END | model_turn
                    v
               execute_tool -> model_turn | await_interaction | END
                                      |
                                      -> model_turn | END
```

`max_steps_exceeded` is the bounded terminal path. Output repair uses the
Session repair budget. Task and interaction protocols are handled as native
graph Tools and state transitions, not arbitrary external handlers.

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

`ModelAdapter` is the only provider-message conversion boundary. It converts a
`ContextPack` to LangChain messages, binds visible Tools, calls the chat model,
and normalizes text, Tool calls, usage, and finish reason. `ModelAdapterCache`
holds per-Agent model overrides.

Subagents use the same graph and governance dependencies. The dispatcher
creates child run lineage, narrows permissions and depth, and propagates denied
actions and workspace references back to the parent.

## Source entry points

- `api/harness.py`, `api/session.py`
- `graph/builder.py`, `graph/nodes.py`, `graph/harness_adapter.py`
- `graph/state.py`, `graph/deps.py`
- `models/adapter.py`, `models/cache.py`
- `checkpoint/factory.py`, `subagent/dispatcher.py`

