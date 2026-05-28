# Workspace Manager

## Module

`modi_harness.workspace`

## Purpose

Manage run-scoped files and indexes.

## Design

Implement:

- `WorkspaceManager`
- `create_run(run_id)`
- `save_input`
- `save_state`
- `save_artifact`
- `save_draft`
- `append_log`
- `index_workspace`

No LangChain or LangGraph dependency.

## Layout

```text
<workspace_root>/<run_id>/
├── input/
├── state/
├── references/
├── artifacts/
├── drafts/
└── logs/
```

## Rules

- Resolve all writes under the run workspace.
- Reject path traversal.
- Keep large and sensitive content in files.
- Context and trace should use `WorkspaceRef`.
- Store source and trust metadata.

## Tests

- run creation
- state snapshots
- artifact write
- path traversal rejection
- workspace index
