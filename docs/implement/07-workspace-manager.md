# Workspace Manager

## Module

`modi_harness.workspace`

## Purpose

Manage run-scoped files and indexes. The current module name is
`WorkspaceManager`, but V0.6.b terminology treats Workspace as the work
boundary; this module manages Harness run files inside that boundary.

Contract: see [`../architecture/07-workspace-manager.md`](../architecture/07-workspace-manager.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Design

Implement:

- `WorkspaceManager`
- `create_run(run_id)`
- `save_input(run_id, name, data, trust)`
- `save_state(run_id, state)`
- `snapshot_state(run_id, step, state)`
- `save_artifact(run_id, name, data, trust, mime_type)`
- `save_draft(run_id, name, data)`
- `append_log(run_id, kind, line)`
- `write_payload(run_id, blob) -> str` (for large trace payloads)
- `index_workspace(run_id) -> list[WorkspaceRef]`
- `acquire_run_lock(run_id)` / `release_run_lock(run_id)`

No LangChain or LangGraph dependency.

## Rules (impl-specific)

- Resolve all writes under `<workspace_root>/<run_id>/` in the current
  implementation. Conceptually this is the run-files root. Reject path traversal
  after symlink and `..` normalization.
- `create_run(run_id)` creates only the run root. Kind directories such as
  `input/`, `drafts/`, `artifacts/`, and `logs/` are created lazily on first
  write.
- Keep large and sensitive content in files; context and trace use `WorkspaceRef`.
- Store source and trust metadata in `WorkspaceRef.metadata`.
- State is written via atomic temp-file + rename.
- Snapshot directory caps total size per run; oldest snapshots are not deleted automatically.
- Workspace Manager does **not** assign trust; the caller passes trust along with content.

## Settings

```text
MODI_WORKSPACE_ROOT=.modi/workspace
MODI_WORKSPACE_SNAPSHOT_LIMIT=100
```

## Tests

- run creation creates only the run root
- lazy kind directory creation on first write
- state snapshot ordering
- artifact write with trust annotation
- path traversal rejection (`..`, symlink escape)
- workspace index correctness
- atomic state write under crash injection
- run lock acquired/released
- two runs do not contend on locks
