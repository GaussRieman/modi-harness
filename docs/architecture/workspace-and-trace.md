# Workspace and Trace

## Workspace

`WorkspaceManager` owns run-scoped files under
`<workspace_root>/<run_id>/`. Kind directories are created lazily:

```text
input/  state/  references/  artifacts/  drafts/  logs/
```

All paths are resolved beneath the run root. Parent traversal and symlink
escape are rejected. Files are represented by `WorkspaceRef`, including trust,
kind, path, MIME type, and optional artifact identity.

Child runs live below the parent run's `sub/` directory and retain their own
run identity. Workspace is for inputs, intermediate files, drafts, artifacts,
state, and logs; it is distinct from reusable Memory.

## Trace

Graph nodes append `TraceEvent` values to pending state. `TraceMiddleware`
drains them after transitions, and `TraceRecorder` writes append-only
`logs/trace.jsonl`.

Every event carries run, root-run, parent-run, thread, type, timestamp, and
payload identity. Sensitive keys are recursively redacted. Oversized payloads
spill into `logs/payloads/` and the event retains a reference.

Trace captures model timing and usage, context-size estimates, Tool decisions,
hooks, interactions, task transitions, Memory operations, validation, and
submission. It records what happened; it is not fed back as Memory.

## Runtime caches

Loader caches use file modification times. Model adapters cache per model
specification. Memory recall caches are per Session/run and invalidate on
committed writes. Caches change cost, not architectural authority.

## Source entry points

- `workspace/manager.py`
- `trace/recorder.py`, `graph/trace_middleware.py`
- `agents/loader.py`, `skills/loader.py`
- `models/cache.py`, `memory/recall_cache.py`

