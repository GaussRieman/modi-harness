# 17. Checkpointer

> **Status:** shipped in V0.2. Module: `modi_harness.checkpoint`.

## Why

V0.1 stored run state in process memory and dropped it on exit. V0.2 needs:

- Run state to survive process restarts.
- A run interrupted on host A to resume on host B.
- Test paths to skip persistence entirely.

LangGraph already ships a `BaseCheckpointSaver` interface with sqlite, postgres,
and in-memory implementations. Modi adopts it directly rather than defining a
parallel abstraction.

## API

```python
from modi_harness.checkpoint import build_checkpointer
from modi_harness.config import Settings

cp = build_checkpointer(Settings())  # returns a BaseCheckpointSaver
```

The factory is the only place in Modi that touches LangGraph backend modules;
callers depend on the abstract `BaseCheckpointSaver` returned.

## Backends

| `MODI_CHECKPOINT_BACKEND` | Class | Notes |
|---|---|---|
| `memory` (tests only) | `MemorySaver` | In-process; lost on exit. |
| `sqlite` (default) | `SqliteSaver` | File at `MODI_CHECKPOINT_SQLITE_PATH` (default `.modi/checkpoint.sqlite`). WAL mode. Single-host. |
| `postgres` (opt-in) | `PostgresSaver` | DSN from `MODI_CHECKPOINT_POSTGRES_DSN`. Lazy-imported. Multi-host. |

Postgres pool sizing and other connection parameters live in the DSN itself
(`?pool_min_size=...`); a dedicated Modi setting is deferred until we have a
real Postgres deployment to size against.

## Cross-Process Resume

A run interrupted on host A can be resumed on host B if both processes point
at the same checkpointer (sqlite file or postgres DB) and use the same
`thread_id`. The flow:

1. Process A: `harness.run_task(..., thread_id="t1")` → status `interrupted`.
   The graph saves a checkpoint at the `interrupt()` callsite.
2. Process A exits. Sqlite/Postgres holds the checkpoint.
3. Process B: instantiate `ModiHarness(..., checkpointer=<same>)`.
4. Process B: `harness.approve_action(thread_id="t1", approval_id=...)`. The
   graph loads the checkpoint, replays the `Command(resume=...)` payload, and
   continues to completion.

Tested end-to-end in `tests/runtime/test_cross_process_resume.py` (S7 smoke).

## Trace Reconciliation

Trace JSONL is independent storage. The `pending_trace_events` queue lives in
state; the `TraceMiddleware` drains it to disk between transitions. On
cross-process resume, the middleware in process B starts with an empty
in-memory cursor and rebuilds it from the existing `trace.jsonl` on disk,
deduplicating by `event_id`. This guarantees no duplicate writes regardless of
how many processes touch the same run.

See `modi_harness.graph.trace_middleware.TraceMiddleware` and its tests in
`tests/graph/test_trace_middleware.py`.

## State that does NOT go through the checkpointer

- `TraceRecorder` writes — independent JSONL on disk.
- Workspace files (artifacts, drafts, logs) — file system.
- Memory records — `MemoryStore` JSONL.
- Tool registry, hook registry — re-instantiated from settings on construction.

The checkpointer holds only the LangGraph state needed to resume execution.

## Open questions

- Compaction for very long-lived threads (V0.3+).
- Multi-host sqlite contention semantics — currently we document the
  single-host assumption and expect Postgres for true multi-host.
