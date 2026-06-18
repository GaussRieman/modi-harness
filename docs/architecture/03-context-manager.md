# Context Manager

Context Manager builds the `ContextPack` for each model step. Context is the model input for one step; it is assembled at runtime and is not durable storage. Context Manager is the **producer** of Modi's canonical context; conversion to LangChain messages is **Model Adapter's** responsibility.

See [`types-reference.md`](../types-reference.md) for `ContextPack`, `ContextBlock`, `MemoryBlock`, `TrustAnnotation`, `Message`, `ToolDescription`.

## Assembly Order

```text
system instruction (incl. untrusted-content standing note)
agent instruction
active skill instructions
selected memory
state summary
available tools
workspace index
recent messages
selected references                 (untrusted, wrapped at Model Adapter)
output requirement
```

## Inputs

- `AgentProfile`
- active `LoadedSkill` list
- `AgentState`
- `MemoryIndex` (pre-built, passed in by caller)
- workspace index (from Workspace Manager)
- tool catalog (from Tool Gateway)
- `OutputContract` (from agent or task override)
- `inlined_references` (optional list of pre-built `ContextBlock`s — see below)
- runtime config: max recent messages

## Trust Model

Trusted: system, agent, active skill, Policy Gate decisions, direct user messages in the API call.

Selected memory is context material. It may be useful and policy-admitted, but
it must not override system, agent, skill, or current user instructions.

Untrusted: tool results, workspace files, referenced documents, skill assets pulled at runtime, hook stdout when not authorized to redirect.

Trust annotations are attached at the block level. See [`15-untrusted-content.md`](./15-untrusted-content.md) for the wrapping contract.

## Tool Visibility

See `Allowed-Tools Algebra` in [`../types-reference.md`](../types-reference.md) for the canonical formula. Summary:

- A skill with `allowed_tools=None` does not narrow.
- A skill with `allowed_tools=[]` narrows to nothing for itself and contributes nothing to the union.
- The agent's `default_tools` is always the upper bound.
- Policy `visible_tools` is the final filter.

Context Manager never widens tool visibility.

## Selected Memory

Memory selection (`select_for_context` with level-based filtering) is performed
by the graph node (`model_turn_node`), **not** by Context Manager and not by the
model. The graph node calls `MemoryStore.select_for_context(...)`, applies the
token budget, and passes the resulting `MemoryIndex` into `build_context()`.
Context Manager receives a pre-built `MemoryIndex` and renders its records as
`memory_blocks`.

These blocks are runtime-selected context hints. They do not outrank system,
agent, skill, or current user instructions. Model-initiated lookup is separate:
an agent can call `recall_memory` when it wants to search the ledger on demand.

## Workspace Index

Workspace files appear as `WorkspaceRef` entries in `workspace_index`, not inlined. Selecting a workspace file for inline rendering requires explicit `references` inclusion and triggers untrusted wrapping.

## Inlined References

`build_context()` accepts an optional `inlined_references` parameter — a list of
pre-built `ContextBlock`s that the caller wants inlined into the context pack.
This is opt-in: the caller (typically `model_turn_node`) decides which references
to inline and builds the blocks before calling Context Manager. Context Manager
places them in the `selected references` slot and applies trust annotations but
does not decide *which* references to inline or manage a reference inlining
threshold.

## Determinism

- Sorting is stable: skills by registration order, tools by name, references by source then id.
- Timestamps are excluded from the hash input.
- `context_hash` is a SHA-256 over a canonical JSON serialization of the pack excluding `raw` and timestamp-like fields.

## Rules

- Preserve instruction hierarchy. Agent does not override system; skill does not override agent; selected memory does not override instructions or current user requests.
- Produce a `ContextPack`. Do not produce LangChain messages.
- Mark every reference, workspace file, tool result, and skill asset block with a trust annotation before adding to the pack.
- Keep large files in workspace; expose by `workspace_ref`.
- Window recent messages by a configured count, then by token budget.
- Memory token budget and reference inlining threshold are not Context Manager config params — they are managed by the caller (graph node) before invoking `build_context()`.
- Context Manager does not load skill packages, does not call Policy Gate, does not call the model, does not call Memory Store directly.

## Boundaries

- Skill loading: Skill Loader.
- Tool execution: Tool Gateway.
- Permission: Policy Gate (Context Manager only consults `policy.visible_tools`).
- LangChain message conversion: Model Adapter.
- Memory persistence: Memory Store.
