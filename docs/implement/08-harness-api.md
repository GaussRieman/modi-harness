# Harness API

## Module

`modi_harness.api`

## Purpose

Expose the public Python interface.

Contract: see [`../architecture/08-harness-api.md`](../architecture/08-harness-api.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Design

Implement:

- `ModiHarness`
- `run_task(agent, input, options=None)`
- `run_task_stream(agent, input, options=None)`
- `resume_task(run_id, input)`
- `approve_action(run_id, approval_id, decision)`
- `reject_action(run_id, approval_id, reason)`
- `get_state(run_id)`
- `get_artifacts(run_id)`
- `get_trace(run_id)`
- `get_denials(run_id)`
- `add_memory(record)` / `list_memory(...)` / `forget_memory(id)`
- `start_thread(agent, options)` / `end_thread(thread_id)` / `list_threads()`
- `list_hooks(run_id=None)` / `get_hook_result(run_id, hook_dispatch_id)`

## User Path

```text
create harness
-> (optional) start_thread
-> run_task / run_task_stream
-> receive final output or pending approval
-> approve / reject if interrupted
-> inspect state, artifacts, trace, denials
-> add/forget memory as needed
-> (optional) end_thread
```

## Rules (impl-specific)

- API is thin. Runtime Adapter owns execution.
- API never invents `run_id`; Runtime Adapter assigns.
- Approval, denial, resume, state, artifact, trace, denial, and memory are explicit calls. Side effects are not hidden behind `run_task`.
- Responses are structured enough for CLI, notebook, and service callers.
- Python API first; HTTP and CLI wrap this API later.

## Tests

- run task
- streaming run terminal event matches non-streaming
- interrupted response
- approval flow
- rejection flow
- state/artifact/trace read APIs
- memory CRUD round trip
- thread lifecycle
- multi-turn within a thread shares conversation memory
- denial guard prevents same-fingerprint retry across calls
