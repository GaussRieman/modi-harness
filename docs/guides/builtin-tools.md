# Builtin Tools

Modi-harness registers eight "builtin" tools at construction time. These are
implicitly visible to every agent — you do **not** need to list them in
`agent.md`'s `tools:` field.

## What's builtin

| Tool | risk | Description |
|---|---|---|
| `read_workspace_file` | L0 | Read a file at `<run>/<kind>/<name>` (kind ∈ input/state/reference/artifact/draft/log) |
| `list_workspace_dir` | L0 | List files under one workspace kind |
| `save_artifact` | L1 | Write to `<run>/artifacts/<name>`, returns artifact_id |
| `save_draft` | L1 | Write to `<run>/drafts/<name>` (overwrites) |
| `recall_memory` | L0 | Model-initiated MemoryStore search (scope/type/tags/query filter) |
| `propose_memory` | L1 | Propose a governed memory write; durable scopes may require approval |
| `save_memory` | L1 | Direct memory write for `thread` or `agent` scope |
| `transition_stage` | L0 | Propose moving the run to a new stage (clarify/explore/plan/execute/verify/deliver); alignment decides whether it is allowed, redirected, or paused for judgment |

These are the only resources modi-harness's kernel directly manages.
Domain-specific tools (filesystem outside workspace, web, third-party APIs)
must be attached to a `ModiAgent` as `ToolBinding` values or contributed by a
plugin.

`transition_stage` is the agent-facing entry to the intent-aligned runtime's
stage layer: a stage is the *phase* of work (not a micro-task), so the runtime
treats a transition as alignment-relevant. A read-only L0 signal — the
`AlignmentKernel`, not a side effect, decides the outcome. Entering a committing
stage such as `deliver` before the human's success bar exists pauses for
judgment. Work *inside* a stage still uses the task protocol.


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

## Memory Builtins vs Selected Memory

The Memory builtins are model-facing tools. They are separate from the
runtime's automatic memory selection for context:

- `model_turn_node` may select small user/workspace/agent/thread
  records and render them as `memory_blocks` before the model responds.
- `recall_memory` is an explicit search chosen by the model during a turn.
- `propose_memory` is an explicit model proposal to persist a reusable record.

Automatic selection is selected memory in context, not autonomous model recall.
`propose_memory` is the preferred model-facing write path; `save_memory` remains
for compatibility and can be denied per agent.

## Configuration

```python
ModiHarness(
    builtin_tools=None,          # default = all eight; pass list to register a subset
)
```

To disable entirely:

```python
ModiHarness(chat_model=model, builtin_tools=[])
```

To register only a subset:

```python
ModiHarness(chat_model=model, builtin_tools=["read_workspace_file", "save_draft"])
```

## Per-agent denial

Use `permission_profile.deny` in `agent.md` to deny a builtin to a specific
agent:

```yaml
---
name: read-only-reviewer
permission_profile:
  mode: auto
  deny:
    - save_artifact
    - propose_memory
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
