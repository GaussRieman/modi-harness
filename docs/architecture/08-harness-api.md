# Harness API

Harness API is the public interface to Modi Harness.

## Operations

```text
run_task(agent, input, options)
resume_task(run_id, input)
approve_action(run_id, approval_id, decision)
reject_action(run_id, approval_id, reason)
get_state(run_id)
get_artifacts(run_id)
get_trace(run_id)
get_denials(run_id)
```

## Types

```python
class RunTaskRequest(TypedDict):
    agent: str
    input: dict
    options: dict
    permission_mode: str

class RunTaskResponse(TypedDict):
    run_id: str
    status: Literal["completed", "interrupted", "blocked", "failed"]
    output: dict | None
    pending_approval: dict | None
    error: dict | None
```

## Rules

- API is thin; Runtime Adapter owns execution.
- Approval, denial, resume, state, artifact, and trace operations are explicit.
- Side effects are never hidden behind a generic task request.
- Denials are persisted for denied-retry checks.
- Python API first; HTTP and CLI are adapters.
