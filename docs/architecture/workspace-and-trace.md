# Workspace and Trace

`WorkspaceManager` owns run-scoped `input`, `state`, `reference`, `artifact`,
`draft`, and `log` files. All paths remain beneath the run root.

Workflow session trace records run start, state transitions, terminal status,
and governed Operation activity. Events carry run/thread identity, timestamp,
type, payload, and optional payload reference. They are append-only execution
evidence and are never treated as reusable Memory.

The injected checkpointer stores the selected Workflow and durable Workflow
state. Workspace stores files; Memory stores reusable context; checkpoints
store control state. These authorities remain separate.
