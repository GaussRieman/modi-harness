# Input Router

Future module. Not required for V0.1.

Input Router classifies interactive input before it reaches Harness API.

## Responsibilities

- Classify command, task, approval response, resume request, or metadata query.
- Route approvals and denials to pending runs.
- Route status requests to state, artifact, trace, or denial APIs.
- Normalize interactive input into explicit Harness API calls.
- Treat hook feedback as routed user feedback.

## Deferred Because

V0.1 uses explicit API calls: `run_task`, `resume_task`, `approve_action`, `reject_action`, `get_state`, `get_artifacts`, `get_trace`, `get_denials`.
