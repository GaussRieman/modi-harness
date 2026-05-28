# Harness API

## Module

`modi_harness.api`

## Purpose

Expose the public Python interface.

## User Path

The primary user path is:

```text
create harness
-> run task
-> receive final output or pending approval
-> approve/reject if interrupted
-> inspect state, artifacts, trace, denials
```

## Design

Implement:

- `ModiHarness`
- `run_task(agent, input, options=None)`
- `resume_task(run_id, input)`
- `approve_action(run_id, approval_id, decision)`
- `reject_action(run_id, approval_id, reason)`
- `get_state(run_id)`
- `get_artifacts(run_id)`
- `get_trace(run_id)`
- `get_denials(run_id)`

## Rules

- API is thin.
- Runtime Adapter owns execution.
- Approval and denial are explicit calls.
- Side effects are not hidden behind `run_task`.
- Python API comes first.
- HTTP and CLI can wrap this API later.
- Responses should be structured enough for CLI, notebook, and service callers.

## Tests

- run task
- interrupted response
- approval flow
- rejection flow
- state/artifact/trace read APIs
