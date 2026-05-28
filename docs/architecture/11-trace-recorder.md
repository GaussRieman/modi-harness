# Trace Recorder

Trace Recorder records the run timeline.

## Event

```python
class TraceEvent(TypedDict):
    event_id: str
    run_id: str
    timestamp: str
    event_type: str
    payload: dict
```

## Fields

- `run_id`, `root_run_id`, `parent_run_id`
- `agent`, `skill`, `model`
- `context_hash`
- `tool_call`, `tool_result`
- `policy_decision`
- `interrupt`, `approval`, `denial`, `hook_feedback`
- `output`, `token_usage`, `latency`, `error`

## Rules

- V0.1 writes local JSONL traces.
- Record state transitions, model usage, tool calls, policy decisions, output validation, approvals, denials, hook blocks, and prompt-injection findings.
- Store references or redacted summaries for large or sensitive payloads.
- Trace explains what happened; it does not decide, retry, or enforce.

## Future Sinks

LangSmith, Langfuse, Phoenix, OpenTelemetry, or a custom trace store.
