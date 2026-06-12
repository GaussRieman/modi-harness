# ModiSession (V0.5)

> **V0.5 status:** `ModiSession` is the binding object introduced when the
> God-Object `ModiHarness` was split into three. It is the **sole execution
> entry point**. Full contract:
> [`docs/superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md`](../superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md) §3.3.
> See [`08-harness-api.md`](08-harness-api.md) for the public API summary and
> [`04-runtime-adapter.md`](04-runtime-adapter.md) for the adapter it owns.

## Role

`ModiSession` = `ModiHarness × list[ModiAgent] × infra`. It is the runtime
container and the only object that can execute threads. It binds a capability
suite (the harness), a set of agent declarations, workspace/run-file storage,
memory storage, and a checkpointer, then compiles a LangGraph graph once at
construction.

```python
ModiSession(
    harness, *, agents, checkpointer, workspace_root, memory_root,
    project_root=None, hook_pass_env=None, max_steps=20, repair_budget=3,
)
ModiSession.from_discovery(harness, *, checkpointer, workspace_root, memory_root,
    plugins=None, agents_dir=None, extra_agents=None, ...)
```

## Owns

- A reference to its `ModiHarness` (capabilities are shared, not copied).
- An indexed `dict[str, ModiAgent]` registry, built by flattening every
  top-level agent's `subagents` tree (`flatten_and_validate`). Top-level names
  come from the `agents=` argument.
- `WorkspaceManager` (current implementation name; conceptually run-file
  storage under a workspace).
- `MemoryStore` (bound to `memory_root`, split into user/agent/project/workspace
  and conversation/thread compatibility scopes).
- `HookDispatcher` (bound to `project_root` + `hook_pass_env`; runs the harness's
  declaration-only `HookRegistry`).
- `ToolGateway` — merges harness builtin tools with each agent's scoped tools,
  and generates `delegate_to_<name>` tools for **nested subagents only**.
- `HarnessGraphAdapter` + the compiled graph (built once, immutable).
- A transitional in-memory thread-metadata dict.

## Does not create infra

Checkpointer, model client, and file roots are all injected. One `ModiHarness`
may back many sessions (`MemorySaver` for tests, a durable saver for
production). Adding agents/tools requires constructing a **new** session — the
graph is immutable after construction.

## Registration semantics

- `list_agents()` returns top-level (runnable) names; `list_all_agents()`
  includes nested subagents.
- `run_task(agent=...)` accepts only top-level names; a subagent-only name
  raises `AgentNotRegistered`.
- Two non-equal agents with the same `name` after the merge raise
  `AgentNameConflict`; equal agents are deduplicated silently.
- `from_discovery` concatenates `plugins[*].agents` + `load_dir(agents_dir)` +
  `extra_agents` into one list, then applies the same rules.

## Lifecycle

Constructed per `(harness, agents, infra)` tuple; rebuilt when agents or infra
change (callers swap the reference). `close()` releases modi-owned resources
(dispatcher subprocesses, trace handles); caller-owned infra is the caller's
responsibility. The session instance is in-process only — after a restart a new
session reads back the same threads, run files, memory, and trace by ID.
