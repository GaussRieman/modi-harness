# Builtin Tools

Modi-harness registers six "builtin" tools at construction time. These are
implicitly visible to every agent — you do **not** need to list them in
`agent.md`'s `tools:` field.

## What's builtin

| Tool | risk | Description |
|---|---|---|
| `read_workspace_file` | L0 | Read a file at `<run>/<kind>/<name>` (kind ∈ input/state/reference/artifact/draft/log) |
| `list_workspace_dir` | L0 | List files under one workspace kind |
| `save_artifact` | L1 | Write to `<run>/artifacts/<name>`, returns artifact_id |
| `save_draft` | L1 | Write to `<run>/drafts/<name>` (overwrites) |
| `recall_memory` | L0 | Query MemoryStore (scope/type/tags filter) |
| `save_memory` | L1 | Write a memory record (scope: `conversation` or `agent` only) |

These are the only resources modi-harness's kernel directly manages.
Domain-specific tools (filesystem outside workspace, web, third-party APIs)
must still be registered explicitly via `harness.register_tool(...)` or
contributed by a plugin.

## Sandbox

Builtin handlers receive `state["run_id"]` from the runtime. The model
**cannot** specify a `run_id` in tool arguments — sandbox is structural.
Path traversal is rejected by `WorkspaceManager._safe_join`.

## Governance

Builtins are **not** a bypass for governance. Every call still goes through:

- JSON schema validation
- `denied_actions` retry guard
- `pre_tool_use` and `post_tool_use` hooks
- `PolicyGate.decide(...)` (risk × permission_mode)
- Idempotency cache
- Trace recording

What builtins skip is **only** the agent allowlist re-check — they're visible
to every agent without being listed.

## Configuration

```python
ModiHarness(
    enable_builtin_tools=True,   # default
    builtin_tools=None,          # default = all six; pass list to register a subset
)
```

To disable entirely:

```python
ModiHarness(enable_builtin_tools=False)
```

To register only a subset:

```python
ModiHarness(builtin_tools=["read_workspace_file", "save_draft"])
```

## Per-agent denial

Use `permission_profile.deny` in `agent.md` to deny a builtin to a specific
agent:

```yaml
---
name: read-only-reviewer
permission_profile:
  mode: ask
  deny:
    - save_artifact
    - save_memory
---
```

## Comparison with explicit registration

You can still register your own tools that overlap conceptually with
builtins. The `code-auditor` example registers `read_file` and
`list_python_files` — these read **project source code** outside the
workspace, so they cannot be builtins (sandbox boundary). Builtins are
strictly for kernel-managed resources.

If your agent only needs workspace IO, drop the explicit registration and
rely on `read_workspace_file` / `save_artifact`.
