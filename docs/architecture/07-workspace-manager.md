# Workspace Manager

Workspace Manager owns run-scoped storage.

## Layout

```text
workspace/<run_id>/
├── input/
├── state/
├── references/
├── artifacts/
├── drafts/
└── logs/
```

## Reference

```python
class WorkspaceRef(TypedDict):
    run_id: str
    kind: str
    path: str
    artifact_id: str | None
    mime_type: str | None
    trust_level: str
    metadata: dict
```

## Rules

- All writes resolve under `workspace/<run_id>/`.
- Save task input, state snapshots, tool results, drafts, artifacts, and logs.
- Maintain a workspace index for Context Manager.
- Store source and trust metadata.
- Large or sensitive content stays in files; context and trace use references.
- Workspace Manager does not decide policy, prompt inclusion, or output validity.
