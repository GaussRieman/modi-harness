# Memory Store

Memory Store gives agents a typed, persistent record of facts, preferences, and pointers that survives across runs and conversations.

Modi Harness exposes memory as a first-class subsystem so that agents can recall what was true in earlier sessions without re-deriving it from raw history every turn.

## Position

Memory is read by Context Manager during context assembly and written by Runtime Adapter (or by user-facing tools) when new durable facts appear.

It is **not** a vector database, not a chat history store, and not a project documentation tool.

- Chat history lives in `AgentState.messages` and run-scoped trace.
- Large source material lives in Workspace.
- Memory holds short, structured records intended to be reloaded into future contexts.

## Memory Record

```python
class MemoryRecord(TypedDict):
    id: str
    scope: Literal["user", "agent", "project", "conversation"]
    type: Literal["user", "feedback", "project", "reference"]
    name: str
    description: str
    body: str
    tags: list[str]
    source_run_id: str | None
    created_at: str
    updated_at: str
    expires_at: str | None
    metadata: dict
```

## Types

- `user`: who the user is, role, expertise, preferences.
- `feedback`: corrections and validated approaches the user has expressed.
- `project`: ongoing work, decisions, constraints, deadlines.
- `reference`: pointers to external systems (dashboards, tickets, docs).

These mirror Claude Code's memory typology because the same four cover the durable categories that recur across domains.

## Scopes

- `user`: shared across all agents and projects for the same user.
- `agent`: bound to one agent definition across runs.
- `project`: bound to one project root or workspace root.
- `conversation`: bound to a single conversation thread, dropped when the thread ends.

A record always has exactly one scope. Lookup is scope-ordered: conversation → project → agent → user.

## Index

```python
class MemoryIndex(TypedDict):
    records: list[MemoryRecord]
    by_scope: dict[str, list[str]]
    by_type: dict[str, list[str]]
    by_tag: dict[str, list[str]]
```

The index is small and fully loaded; record bodies stay on disk until selected.

## Operations

```text
load_memory(scope_keys) -> MemoryIndex
read_record(id) -> MemoryRecord
write_record(record) -> MemoryRecord
update_record(id, patch) -> MemoryRecord
delete_record(id) -> None
search(query, scopes, types, tags, limit) -> list[MemoryRecord]
```

## Selection for Context

Context Manager selects memory in this order, capped by token budget:

1. All `feedback` records in active scopes.
2. All `user` records.
3. `project` records matching current task tags.
4. `reference` records pointed to by the current task.

Selected records become a dedicated `memory` block in `ContextPack`, kept separate from skill instructions and from untrusted material.

## Trust

Memory is **trusted material**, on the same level as agent instructions.

- Memory is created by the user or by validated agent decisions.
- Memory cannot be written by raw tool output or by external documents without going through an explicit write path.
- Untrusted observations cannot be promoted to memory inside a single model step; they must round-trip through the user, an explicit tool call, or a reviewed runtime decision.

## Policy Authority on Writes

Memory writes are subject to Policy Gate:

- A model-proposed `memory_write` is a `RequestedAction` with `kind="memory_write"`.
- Policy Gate routes by scope and source:
  - `conversation` and `agent` scopes: `allow` by default (treated as L2 within harness storage)
  - `user` and `project` scopes: `require_approval` by default
  - any write whose `source_kind` is `tool_result` without an explicit user round-trip: `deny`
- A direct user-initiated write via `HarnessAPI.add_memory` bypasses model-side approval but still validates schema and trust source.
- Rule packs may elevate further (e.g. a `finance` pack may require approval on all `project` writes).

## Rules

- Memory writes happen only through Memory Store API, never through direct file edits by the model.
- Records are append-mostly: prefer `update_record` over silent overwrites; deletes are explicit.
- A record's `body` should be small (target under 1 KB). Large material lives in Workspace and is referenced by `metadata.workspace_ref`.
- When the user asks to forget, delete the matching record rather than masking it.
- A stale record beats a wrong one: include `updated_at` and let Context Manager drop records older than a configured horizon for `project` scope.
- Memory contributes to the deterministic `context_hash` used by Trace Recorder.

## Boundaries

- Memory Store does not decide which records belong in context; Context Manager owns selection.
- Memory Store does not enforce policy on the actions a memory describes; Policy Gate owns runtime authority.
- Memory Store does not embed or vectorize records in V0.1; selection is rule-based and tag-based.
