# Workspace Manager

Workspace is the application-defined work boundary. See
[Core Concepts](./00-core-concepts.md).

The current `WorkspaceManager` implementation owns Harness-managed run files
inside that boundary. Conceptually it is closer to a run-files manager than to
the full workspace abstraction. The public name remains for compatibility.

See [`types-reference.md`](../types-reference.md) for `WorkspaceRef`.

## Conceptual Layout

Preferred conceptual layout:

```text
<workspace>/
└── .modi/
    └── runs/<run_id>/
        ├── input/
        ├── state/
        ├── refs/
        ├── artifacts/
        ├── drafts/
        └── logs/
```

Current implementation layout:

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

- A run-files subdirectory is owned by exactly one `run_id`.
- Two runs never share a subdirectory.
- A lock file `<run_id>/.lock` guards state writes within a run; concurrent processes operating on the same run yield to the lock holder.
- Cross-run reads are allowed via `WorkspaceRef`; cross-run writes are forbidden.

## State Persistence

- `state/state.json` holds the latest `AgentState` snapshot.
- `state/snapshots/<step>.json` holds historical snapshots after every behavior-changing transition.
- Resume reads the latest snapshot; replay reads the sequence.

## Rules

- All run-file writes resolve under the run directory. Any path that resolves outside, after symlink and `..` normalization, is rejected.
- Save task input, state snapshots, tool results, drafts, artifacts, and logs.
- Maintain a run-files index for Context Manager.
- Store source and trust metadata in `WorkspaceRef.metadata`.
- Large or sensitive content stays in run files; context and trace use references.
- Workspace Manager does not decide policy, prompt inclusion, memory, or output validity.
- Workspace Manager has no LangChain or LangGraph dependency.

## Boundaries

- Trust annotation source: whichever module produced the file (Tool Gateway for tool output, Harness API for user input, etc.). Run-file storage stores the annotation; it does not assign it.
- Lifecycle (retention, cleanup): a separate housekeeper, not Workspace Manager's hot path.
