# Trace Recorder

Trace Recorder records append-only event history for a run. Trace answers:
what happened?

See [`types-reference.md`](../types-reference.md) for `TraceEvent` and the standard `event_type` list.

## Storage

V0.1 has one authoritative trace location per run: under the run files
directory.

```text
<workspace_root>/<run_id>/logs/trace.jsonl
```

In V0.6.b vocabulary, `workspace_root` here is the current implementation's
run-file storage root, not necessarily the full Workspace work boundary.

Optional mirroring to `MODI_TRACE_ROOT` is an **async sink**, not a synchronous second write. The mirror is best-effort and recoverable from the authoritative copy; mirror failure never blocks the run.

## Writer

- One writer per run, append-only JSONL.
- Each line is one `TraceEvent`.
- Large payloads are written as files under `logs/payloads/` and referenced via `payload_ref`; only refs go on the event line.
- Sensitive fields are redacted by a configurable redactor before serialization.

## Rules

- Trace explains what happened; it does not decide, retry, enforce, or repair.
- Trace is not Memory and does not enter Context by default.
- Trace ordering matches runtime ordering; events are written before the next state transition begins.
- A trace must be replayable into a human-readable summary by trace tools without consulting run state.
- `context_hash` per model step ties trace events to a reproducible `ContextPack`.
- `fingerprint` on tool calls ties denied-retry checks across runs.
- Trace Recorder has no LangChain or LangGraph dependency.

## Future Sinks

LangSmith, Langfuse, Phoenix, OpenTelemetry, or a custom store. All future sinks consume the same JSONL as the source of truth.

## Boundaries

- Decision authority: Policy Gate, Runtime Adapter.
- Storage of large payloads: Workspace Manager.
- Mirror to remote sink: a separate async exporter, not Trace Recorder's hot path.
