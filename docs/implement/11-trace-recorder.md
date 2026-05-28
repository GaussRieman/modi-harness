# Trace Recorder

## Module

`modi_harness.trace`

## Purpose

Record run events for inspection and debugging.

## Design

Implement:

- `TraceRecorder`
- `record(event_type, payload)`
- `read_trace(run_id)`
- JSONL writer
- payload redaction helper

No LangChain or LangGraph dependency.

## V0.1 Storage

Default: write JSONL under the run workspace logs.

Optional: mirror to `MODI_TRACE_ROOT`.

## Events

- state transition
- context hash
- model call
- tool call/result
- policy decision
- interrupt
- approval
- denial
- hook feedback
- output validation
- error

## Rules

- Trace explains what happened.
- Trace does not decide, retry, enforce, or repair.
- Large or sensitive payloads are stored as references or redacted summaries.
- External sinks are future adapters.

## Tests

- JSONL append
- trace read
- redaction
- event ordering
- workspace reference payloads
