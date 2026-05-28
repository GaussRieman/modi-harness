# Context Manager

## Module

`modi_harness.context`

## Purpose

Build deterministic `ContextPack` objects for model calls.

Contract: see [`../architecture/03-context-manager.md`](../architecture/03-context-manager.md).
Types: see [`../types-reference.md`](../types-reference.md).
Untrusted handling: see [`../architecture/15-untrusted-content.md`](../architecture/15-untrusted-content.md).

## Design

Implement:

- `ContextManager`
- `build_context(state, agent, skills, memory_index, workspace_index, tool_catalog, output_contract) -> ContextPack`
- message windowing (count + token budget)
- workspace index formatter
- trust annotation builder
- memory selection helper (delegates to `MemoryStore.select_for_context`)
- stable `context_hash` computer
- tool visibility intersector

Output:

- canonical `ContextPack`. **No LangChain message conversion here.**

## Determinism

- Stable sort orders documented in architecture doc.
- `context_hash` is SHA-256 over canonical JSON of the pack, excluding timestamps and `raw` fields.

## Rules (impl-specific)

- Reads workspace index; does not write workspace files.
- Reads memory index; does not write memory records.
- Calls `policy.visible_tools(agent, mode, state)` to filter tools; does not call `policy.decide`.
- Has no LangChain or LangGraph dependency in core; an optional `to_langchain_messages` helper lives in `models/` (Model Adapter), not here.

## Tests

- deterministic `context_hash` over identical inputs
- trust annotations attached to all references and tool results
- message windowing respects count and budget
- tool visibility = agent ∩ skill ∩ policy
- skill with no `allowed-tools` does not narrow
- large file appears as `workspace_ref`, not inlined
- memory selection respects token budget
- output_contract `free_form=True` produces no `output_requirement` schema
