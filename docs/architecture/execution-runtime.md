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
`RuntimeOperationProposal`. Fast Brain rules are best-effort known-known
shortcuts; misses, rule errors, or invalid fast decisions fall through to slow
Brain instead of becoming user interrupts. Slow Brain uses a model adapter /
normalizer boundary before Loop validation, so the model is not trusted to emit
runtime schema perfectly.

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
runtime it is called by the structured slow Brain planner, which exposes the
`submit_step_decision` protocol tool as the preferred model output path.
Business tools are not executed directly by the model; if the model proposes
one, the slow Brain adapter normalizes it into a `RuntimeOperationProposal` and
the Loop/Harness path executes it after validation. If slow normalization cannot
recover a safe step, the run enters `pending_judgment`, which CLI/API clients
must surface as an interactive human-judgment pause. `ModelAdapterCache` holds
per-Agent model overrides.

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
