# Builtin Tools

Kernel builtins are registered by `ModiHarness` and still pass through the
Action Gateway, JSON Schema validation, hooks, Policy, permission modes, and
trace.

| Tool | Risk | Purpose |
|---|---:|---|
| `read_workspace_file` | L0 | Read one run-scoped file |
| `list_workspace_dir` | L0 | List one run-scoped directory |
| `save_artifact` | L1 | Persist a finished artifact |
| `save_draft` | L1 | Persist intermediate output |
| `recall_memory` | L0 | Query reusable Memory |
| `propose_memory` | L1 | Propose a governed durable Memory write |
| `save_memory` | L1 | Save a thread- or Agent-scoped Memory |

Use `ModiHarness(builtin_tools=[...])` to select builtins and
`permission_profile.deny` in `agent.toml` to deny one for an Agent.
