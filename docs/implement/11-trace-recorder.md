# Trace Recorder

## Module

`modi_harness.trace`

## Purpose

Record run events for inspection and debugging.

Contract: see [`../architecture/11-trace-recorder.md`](../architecture/11-trace-recorder.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Design

Implement:

- `TraceRecorder`
- `record(event_type, payload)`
- `read_trace(run_id) -> Iterable[TraceEvent]`
- JSONL writer (append-only, line-buffered)
- payload redactor (regex + key-list)
- large-payload offload to `logs/payloads/` via Workspace Manager, with `payload_ref`
- optional async mirror to `MODI_TRACE_ROOT` (best-effort; mirror failure does not block run)

No LangChain or LangGraph dependency.

## Authoritative Location

```text
<workspace_root>/<run_id>/logs/trace.jsonl
```

Mirror is async, not dual-write.

## Rules (impl-specific)

- Trace explains what happened. It does not decide, retry, enforce, or repair.
- Trace ordering matches runtime ordering; events flushed before the next state transition begins.
- Large or sensitive payloads are stored as references or redacted summaries.
- Redactor is configurable via settings.
- Reader returns events lazily for large runs.

## Settings

```text
MODI_TRACE_ROOT=
MODI_TRACE_REDACT_KEYS=api_key,authorization,password,secret
MODI_TRACE_PAYLOAD_INLINE_LIMIT_BYTES=2048
```

## Tests

- JSONL append-only ordering
- trace read (lazy iterator)
- redaction on configured keys
- event ordering against runtime transitions
- workspace reference payloads for oversize
- mirror failure does not block (injected)
