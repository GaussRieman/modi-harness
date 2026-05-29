# Harness API

> **V0.2/V0.3 status:** `ModiHarness` is the single public entry point. It wraps
> Runtime Adapter and all governance modules behind a unified Python API.
> Threads are implicit (created on first `run_task`), and `thread_id` is the
> primary persistence/introspection key — `run_id` is internal to Runtime Adapter.

See [`types-reference.md`](../types-reference.md) for `RunTaskResponse`,
`PendingApproval`, `AgentState`, `ThreadInfo`, `StreamEvent`, `HookSpec`,
`HookResult`, `TraceEvent`, `DeniedAction`, `WorkspaceRef`, `MemoryRecord`.

## Position

ModiHarness is the **only** public class that external callers import. It:

- constructs Runtime Adapter, governance modules, and the compiled LangGraph graph
- auto-registers subagent tools (`delegate_to_<name>`) at construction time
- exposes run lifecycle, introspection, memory, thread, hook, and tool-registration APIs
- owns no execution logic itself — delegates to Runtime Adapter and governance modules

## Construction

```python
harness = ModiHarness(
    agents_dir,             # Path — agent YAML directory
    skills_dir,             # Path — skill package directory
    workspace_root,         # Path — workspace base
    memory_root,            # Path — memory store base
    rule_packs,             # list[str] — active policy rule packs
    chat_model,             # str — model identifier (e.g. "claude-sonnet-4-20250514")
    checkpointer,          # BaseCheckpointSaver | None — LangGraph checkpointer
    max_steps,             # int — step limit per run
    repair_budget,         # int — max repair attempts per malformed tool call
    hook_user_settings,    # Path | None — user-level hook settings
    hook_project_settings, # Path | None — project-level hook settings
    hook_pass_env,         # list[str] — env vars forwarded to hook subprocesses
)
```

Subagent tools are discovered from `agents_dir` and registered automatically.
Missing model settings fail at construction, not at first call.

## Run Lifecycle

```text
run_task(agent, input, options, permission_mode, thread_id) -> RunTaskResponse
resume_task(thread_id, payload) -> RunTaskResponse
approve_action(thread_id, approval_id, decision) -> RunTaskResponse
reject_action(thread_id, approval_id, reason) -> RunTaskResponse
```

- `run_task` creates a thread implicitly if `thread_id` is not supplied.
- `resume_task` continues a paused run (e.g. after human-in-the-loop input).
- `approve_action` / `reject_action` resolve a `PendingApproval`.

## Streaming

```text
stream(agent, input, options, permission_mode, thread_id) -> Iterable[dict]
astream(agent, input, options, permission_mode, thread_id) -> AsyncIterator[dict]
```

- `stream` is synchronous, whole-turn granularity.
- `astream` is async, per-token granularity.

Both emit `StreamEvent` dicts. Event types:

| type | payload |
|------|---------|
| `model_delta` | incremental model text |
| `tool_call_proposal` | proposed tool call before policy check |
| `tool_call_result` | tool execution result |
| `approval_request` | pending approval requiring human decision |
| `terminal` | final `RunTaskResponse` |

The non-streaming `run_task` is a thin wrapper that consumes the stream internally
and returns the terminal payload.

## Introspection

All introspection methods are keyed by `thread_id`:

```text
get_state(thread_id) -> AgentState | None
get_artifacts(thread_id) -> list[WorkspaceRef]
get_trace(thread_id) -> Iterable[TraceEvent]
get_denials(thread_id) -> list[DeniedAction]
```

## Memory

```text
add_memory(record) -> MemoryRecord
list_memory(scopes, types, tags) -> list[MemoryRecord]
forget_memory(record_id)
```

Memory operations are explicit. `run_task` never implicitly writes memory.

## Threads

```text
end_thread(thread_id)
list_threads() -> list[ThreadInfo]
```

Threads are implicit — created on first `run_task` call when no `thread_id` is
provided. `end_thread` closes the thread and drops `conversation`-scoped memory.
There is no `start_thread`; the thread lifecycle begins with the first run.

## Hooks

```text
list_hooks(thread_id) -> list[HookSpec]
get_hook_results(thread_id, event_id) -> list[HookResult]
```

## Tool Registration

```text
register_tool(spec, handler, dry_run)
```

Registers a custom tool at runtime. `dry_run` controls whether the tool is
available for proposal only (no execution). Subagent tools are auto-registered
at construction and do not need explicit registration.

## Rules

- API is thin. Runtime Adapter owns execution.
- `thread_id` is the primary persistence/introspection key. `run_id` is internal to Runtime Adapter; API never exposes it to callers.
- Threads are implicit — created on first `run_task`, not via a separate `start_thread` call.
- Approval, denial, resume, state, artifact, trace, and memory operations are explicit. Side effects are never hidden behind `run_task`.
- Denials are persisted for denied-retry checks.
- Subagent tools (`delegate_to_<name>`) are auto-registered at construction from `agents_dir`.
- Python API is primary. HTTP and CLI wrap this API later.
- Responses are structured enough for CLI, notebook, and service callers.

## Boundaries

- Execution: Runtime Adapter.
- Memory persistence: Memory Store.
- Trace: Trace Recorder.
- Workspace: Workspace Manager.
- Policy decisions: Policy Gate.
- Hook dispatch: Hook System.
