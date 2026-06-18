# Agent Discovery and Task Protocol

## Discovery

`modi` walks upward from the current directory to the nearest `modi.toml`.
The `[agents]` table can configure `dirs`, conventional-directory discovery,
plugins, the user Agent directory, and trusted project factories. Relative
directories resolve from the config file.

Every Agent has a qualified source name such as `project:research-assistant`.
Use `modi agents list`, `show`, and `which` to inspect resolution. Ambiguous
unqualified names fail with candidates instead of silently selecting one.

Project factories (`agent.toml` with `factory = "module:function"`) execute only
when trusted by project configuration or supplied through `--agents-dir`.
Factories under `~/.modi/agents` are not imported automatically.

Discovered names are also dynamic commands. Static names such as `agents`,
`info`, `plugins`, `resume`, and `run` remain reserved; every other first token
is resolved by the registry:

```bash
modi research-assistant
modi project:research-assistant
```

Agents can opt into Agent-driven startup with:

```yaml
interaction_protocol:
  startup: agent
```

The native `request_user_input` protocol checkpoints text, multiline, URL-list,
or confirmation requests. Clients collect and resume those interactions; no
Agent-specific input wizard belongs in the CLI.

## Native tasks

Agents opt in through frontmatter:

```yaml
task_protocol:
  mode: required
  review: before_execution
  min_items: 1
  max_items: 8
```

The runtime exposes `create_task_plan`, `revise_task_plan`, `start_task`,
`resume_task`, `complete_task`, and `block_task` as protocol tools. They update checkpointed
state through validated transitions and never pass through arbitrary tool
handlers. In required mode, final output is rejected until every task is
completed.

After the final task completes, the runtime enters a dedicated finalization
phase. Only `submit_output` remains visible, the model receives a compact
finalization instruction plus the output contract, and output repair uses its
own bounded budget rather than the research-step limit. Structured contracts
reject ordinary assistant text during this phase.

`block_task` records an external blocker. When new input or a changed external
condition resolves it, `resume_task` explicitly moves that item back to
`in_progress`; blocked work is never silently marked complete.

Plan review uses `pending_interaction`, not a high-risk policy approval. Clients
respond with the interaction id and `approved`, `revise`, or `cancelled`.
Canonical task and interaction events let CLI, web, desktop, and API clients
render the same state without parsing model text or tool arguments.

Resolving an interaction is a real human turn, not only a resume signal. The
runtime closes the protocol tool call, appends the human response as a `user`
message, and updates versioned `human_context`. Downstream model turns therefore
receive confirmed inputs and review feedback even after older messages are
trimmed from the recent context window.
