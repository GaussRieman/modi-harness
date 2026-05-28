# Context Manager

Context Manager builds the `ContextPack` for each model step. It is the **producer** of Modi's canonical context; conversion to LangChain messages is **Model Adapter's** responsibility.

See [`types-reference.md`](../types-reference.md) for `ContextPack`, `ContextBlock`, `MemoryBlock`, `TrustAnnotation`, `Message`, `ToolDescription`.

## Assembly Order

```text
system instruction (incl. untrusted-content standing note)
agent instruction
active skill instructions
memory blocks                       (trusted)
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
- `MemoryIndex` (from Memory Store)
- workspace index (from Workspace Manager)
- tool catalog (from Tool Gateway)
- `OutputContract` (from agent or task override)
- runtime config: max recent messages, memory token budget, reference inlining threshold

## Trust Model

Trusted: system, agent, active skill, memory, Policy Gate decisions, direct user messages in the API call.

Untrusted: tool results, workspace files, referenced documents, skill assets pulled at runtime, hook stdout when not authorized to redirect.

Trust annotations are attached at the block level. See [`15-untrusted-content.md`](./15-untrusted-content.md) for the wrapping contract.

## Tool Visibility

See `Allowed-Tools Algebra` in [`../types-reference.md`](../types-reference.md) for the canonical formula. Summary:

- A skill with `allowed_tools=None` does not narrow.
- A skill with `allowed_tools=[]` narrows to nothing for itself and contributes nothing to the union.
- The agent's `default_tools` is always the upper bound.
- Policy `visible_tools` is the final filter.

Context Manager never widens tool visibility.

## Memory Selection

Context Manager calls `MemoryStore.select_for_context(task, agent_name, scopes, budget)` and renders selected records as `memory_blocks`. Memory is rendered before references and is never wrapped as untrusted.

## Workspace Index

Workspace files appear as `WorkspaceRef` entries in `workspace_index`, not inlined. Selecting a workspace file for inline rendering requires explicit `references` inclusion and triggers untrusted wrapping.

## Determinism

- Sorting is stable: skills by registration order, tools by name, references by source then id.
- Timestamps are excluded from the hash input.
- `context_hash` is a SHA-256 over a canonical JSON serialization of the pack excluding `raw` and timestamp-like fields.

## Rules

- Preserve instruction hierarchy. Agent does not override system; skill does not override agent; memory does not override agent.
- Produce a `ContextPack`. Do not produce LangChain messages.
- Mark every reference, workspace file, tool result, and skill asset block with a trust annotation before adding to the pack.
- Keep large files in workspace; expose by `workspace_ref`.
- Window recent messages by a configured count, then by token budget.
- Apply memory token budget separately from reference budget.
- Context Manager does not load skill packages, does not call Policy Gate, does not call the model.

## Boundaries

- Skill loading: Skill Loader.
- Tool execution: Tool Gateway.
- Permission: Policy Gate (Context Manager only consults `policy.visible_tools`).
- LangChain message conversion: Model Adapter.
- Memory persistence: Memory Store.
