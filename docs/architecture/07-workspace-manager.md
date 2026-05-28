# Workspace Manager

Workspace Manager owns run-scoped storage.

See [`types-reference.md`](../types-reference.md) for `WorkspaceRef`.

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

`logs/trace.jsonl` is the authoritative trace location for the run.

## Concurrency

- A workspace subdirectory is owned by exactly one `run_id`.
- Two runs never share a subdirectory.
- A lock file `<run_id>/.lock` guards state writes within a run; concurrent processes operating on the same run yield to the lock holder.
- Cross-run reads are allowed via `WorkspaceRef`; cross-run writes are forbidden.

## State Persistence

- `state/state.json` holds the latest `AgentState` snapshot.
- `state/snapshots/<step>.json` holds historical snapshots after every behavior-changing transition.
- Resume reads the latest snapshot; replay reads the sequence.

## Rules

- All writes resolve under `workspace/<run_id>/`. Any path that resolves outside, after symlink and `..` normalization, is rejected.
- Save task input, state snapshots, tool results, drafts, artifacts, and logs.
- Maintain a workspace index for Context Manager.
- Store source and trust metadata in `WorkspaceRef.metadata`.
- Large or sensitive content stays in files; context and trace use references.
- Workspace Manager does not decide policy, prompt inclusion, or output validity.
- Workspace Manager has no LangChain or LangGraph dependency.

## Boundaries

- Trust annotation source: whichever module produced the file (Tool Gateway for tool output, Harness API for user input, etc.). Workspace stores the annotation; it does not assign it.
- Lifecycle (retention, cleanup): a separate housekeeper, not Workspace Manager's hot path.
