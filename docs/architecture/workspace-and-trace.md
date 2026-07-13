# Workspace and Trace

`WorkspaceManager` owns run-scoped `input`, `state`, `reference`, `artifact`,
`draft`, and `log` files. All paths remain beneath the run root.

Workflow session Trace uses the same vocabulary as streaming: Workflow and
Node lifecycle, AgentLoop Steps, governed Operations, completion acceptance or
rejection, human waits/resolution, committed transitions, and terminal status.
Events carry run/thread identity, timestamp, type, payload, and optional payload
reference. Node attempts, Steps, and Operations are joined by `node_id`,
`node_attempt`, `step_id`, and `invocation_id`.

Call `ModiSession.get_trace(thread_id)` to read checkpointed events. They are
also appended to `<workspace_root>/<run_id>/logs/trace.jsonl`. Trace is
append-only execution evidence and is never treated as reusable Memory.

The injected checkpointer stores the selected Workflow and durable Workflow
state. Workspace stores files; Memory stores reusable context; checkpoints
store control state. These authorities remain separate.
