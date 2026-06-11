# Memory And Managed Context State

## Module

`modi_harness.memory`

## Purpose

Persist typed records, hydrate small managed context state for model turns, support model-initiated memory recall, and keep model-facing memory writes policy-governed.

This module intentionally separates three concepts that share storage:

- **Managed Context State**: runtime-selected user/project/agent/conversation records that are passively injected as baseline context.
- **Agent Memory**: model-initiated recall and write proposals through `recall_memory` and `propose_memory`.
- **Trace**: runtime events that explain recall, admission, selection, and writes; trace is not durable memory by itself.

The implementation should evolve from the current file-backed `MemoryStore` into a layered memory subsystem:

- `MemoryLedger`: canonical CRUD and audit metadata.
- `MemoryRetriever`: candidate recall and ranking.
- `MemoryAdmissionGate`: context admission and authority classification.
- `MemoryConsolidator`: background dedupe, expiration, supersession, and index maintenance.

## Storage Layout

The default local ledger stores Markdown records with YAML frontmatter:

```text
<memory_root>/
├── user/<user_key>/
│   ├── MEMORY.md
│   └── <record_id>.md
├── agent/<agent_name>/
│   ├── MEMORY.md
│   └── <record_id>.md
├── project/<project_key>/
│   ├── MEMORY.md
│   └── <record_id>.md
└── conversation/<thread_id>/
    ├── MEMORY.md
    └── <record_id>.md
```

Default roots:

- `user`: `~/.modi/memory/user/<user_key>/`
- `agent`: `~/.modi/memory/agent/<agent_name>/`
- `project`: `<project_root>/.modi/memory/project/<project_key>/`
- `conversation`: `<workspace_root>/threads/<thread_id>/memory/`

`MEMORY.md` is an index only. Record bodies live in individual files.

The current implementation uses flat `user/agent/project/conversation` directories. Migration should preserve compatibility by reading the legacy flat layout until records are rewritten under keyed paths.

## Record Schema

Keep the stable `MemoryRecord` contract:

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
    metadata: dict[str, Any]
```

Use `metadata` for fields that are useful but not yet universal:

- `source_kind`
- `source_uri`
- `source_message_id`
- `confidence`
- `entities`
- `valid_from`
- `valid_to`
- `supersedes`
- `superseded_by`
- `workspace_ref`
- `access_policy`
- `retrieval_hints`

## Scope Keys

Add a scope key object so all read, write, and search paths know which physical partition to use:

```python
class MemoryScopeKeys(TypedDict):
    user_key: str
    agent_name: str
    project_key: str
    thread_id: str
```

`ModiSession` constructs scope keys from settings, active agent, project root, and thread id. Direct API calls may pass overrides when operating outside a run.

Lookup precedence remains:

```text
conversation -> project -> agent -> user
```

## Interfaces

### MemoryLedger

```text
load_index(scope_keys, scopes) -> MemoryIndex
read_record(record_id, scope_keys, scopes=None) -> MemoryRecord
write_record(record, scope_keys) -> MemoryRecord
update_record(record_id, patch, scope_keys) -> MemoryRecord
delete_record(record_id, scope_keys) -> None
```

Rules:

- Validate ids with `[A-Za-z0-9_-]+`.
- Reject path traversal.
- Reject body over 4 KB with a hint to move material to Workspace.
- Write record files atomically.
- Rebuild the scope-key index after writes.
- Preserve `created_at`; update `updated_at`.
- Do not return expired or superseded records unless explicitly requested.

### MemoryRetriever

```text
search(query, scope_keys, scopes=None, types=None, tags=None, limit=None) -> list[MemoryCandidate]
```

`MemoryCandidate` should include:

```python
class MemoryCandidate(TypedDict):
    record: MemoryRecord
    score: float
    reasons: list[str]
    signals: dict[str, float]
```

Initial local implementation:

- metadata filters
- substring search for compatibility
- SQLite FTS5 or equivalent keyword index when available

Next implementation:

- BM25-style keyword scoring
- optional embedding vector search
- entity matching via `metadata.entities`
- temporal filters using `expires_at`, `valid_from`, and `valid_to`
- reciprocal-rank fusion across retrieval signals

The ledger remains source of truth. Retrieval indexes must be rebuildable.

### MemoryAdmissionGate

```text
admit(candidates, task, agent, state, policy_context) -> list[SelectedMemory]
```

`SelectedMemory` should include:

```python
class SelectedMemory(TypedDict):
    record: MemoryRecord
    authority: Literal["trusted", "context"]
    score: float
    reasons: list[str]
```

Admission rules:

- Drop expired records.
- Drop records superseded by a newer selected record.
- Drop records outside the active scope keys.
- Drop low-confidence or cross-domain candidates when the task does not justify them.
- Classify durable user feedback and approved project constraints as `trusted`.
- Classify ordinary recalled facts and references as `context`.

Context rendering must preserve the authority classification rather than treating every memory block as instruction-level trusted material.

### MemoryConsolidator

```text
consolidate(scope_keys, scopes=None, dry_run=True) -> MemoryConsolidationReport
rebuild_indexes(scope_keys, scopes=None) -> None
```

Responsibilities:

- dedupe near-identical records
- mark stale records as superseded
- expire project records beyond `MODI_MEMORY_PROJECT_HORIZON_DAYS`
- extract entities and retrieval hints
- summarize oversized or noisy records into workspace references
- rebuild local retrieval indexes

Consolidation writes must emit trace events and must not silently erase user intent.

## Managed Context State Selection Helper

Keep a compatibility helper:

```text
select_for_context(task, agent_name, scope_keys, scopes, budget=None, level="moderate") -> list[MemoryRecord]
```

Despite the method name, this is a managed context-state hydration helper. It is invoked by the runtime before a model turn. The model does not choose these candidates.

Preferred internal flow:

```text
retriever.search(...)
-> admission_gate.admit(...)
-> budget_pack(...)
-> selected records
```

Memory levels:

- `minimal`: feedback only, default 500 token budget.
- `moderate`: feedback, user, project, default 1500 token budget.
- `full`: feedback, user, project, reference, default 3000 token budget.

Explicit `budget` overrides the level default. Budget packing should use a tokenizer when available, with the current bytes/4 approximation as fallback.

Selection criteria:

- scope keys must match the current user, agent, project, and thread partitions
- expired and superseded records are excluded
- type inclusion follows the memory level
- project records require task tag relevance when tags are present
- reference records require explicit `reference_keys`
- ranking records scores and reasons
- admission classifies each selected record as `trusted` or `context`
- budget packing limits what is rendered into `ContextPack.memory_blocks`

The rendered blocks are context hints. They must not outrank system, developer, agent, or current user instructions.

## Direct State Bootstrap API

`ModiSession.add_memory(record)` remains a direct caller API for tests, examples, and application-controlled bootstrap. It is not a model-facing tool and should not be presented as autonomous model memory creation.

Path resolution is determined by the session:

```text
<memory_root>/<scope>/<scope_key>/<record_id>.md
```

Default scope keys:

- `user`: `default`
- `agent`: top-level agent name
- `project`: fingerprint of `project_root`
- `conversation`: thread id, or `session` for direct session calls outside a run

Applications that need user-visible control should wrap this API with their own naming, review, and UI flows rather than exposing the raw path rules to end users.

## Model-Facing Tools

Expose two concepts:

- `recall_memory`: model-initiated read-only search over allowed scopes.
- `propose_memory`: model-facing write proposal.

`save_memory` may remain as a backward-compatible alias for `conversation` and `agent` scope writes, but the architecture should treat model writes as proposals.

`propose_memory` flow:

```text
validate input
build RequestedAction(kind="memory_write")
PolicyGate.decide(...)
if allowed or approved: commit to ledger
update indexes
emit trace
```

Default policy:

- `conversation`: allow, subject to validation.
- `agent`: allow, subject to validation and duplicate check.
- `project`: require approval.
- `user`: require approval.
- source from untrusted tool result: deny unless reviewed or user-confirmed.

## Settings

Add or keep:

```text
MODI_MEMORY_ROOT=~/.modi/memory
MODI_MEMORY_USER_KEY=default
MODI_MEMORY_PROJECT_KEY=        # optional, defaults to project root path hash
MODI_MEMORY_TOKEN_BUDGET=2000
MODI_MEMORY_PROJECT_HORIZON_DAYS=90
MODI_MEMORY_RETRIEVAL_BACKEND=local
MODI_MEMORY_VECTOR_BACKEND=none
MODI_MEMORY_CONSOLIDATION=off
```

`local` means Markdown ledger plus local retrieval index. External backends can be added later as adapters.

## Integration

- `ModiSession` constructs `MemoryScopeKeys` for each run.
- `model_turn_node` asks memory to select records using task, agent, state, scope keys, and memory level.
- `ContextManager.build_context` receives selected memory with authority classification.
- `PolicyGate` remains the authority for writes.
- `MemoryAdmissionGate` is the authority for context admission.
- `TraceRecorder` records recall, admission, selection, write, delete, and consolidation events.
- `HarnessAPI.add_memory`, `list_memory`, and `forget_memory` remain direct user controls and validate schema/source.

## Trace Events

Record:

- `memory_recall_candidates`
- `memory_admission`
- `memory_selection`
- `memory_write_proposed`
- `memory_write`
- `memory_update`
- `memory_delete`
- `memory_consolidated`

Each event should include record ids, scope keys, decision, source, and reasons when available. Candidate events should include scores or retrieval signals.

## Migration Plan

1. Introduce `MemoryScopeKeys` and keyed path resolution while reading legacy flat directories.
2. Split the current `MemoryStore` into ledger-facing methods and a compatibility facade.
3. Add expiration and supersession filtering.
4. Add local keyword retrieval index.
5. Add `MemoryAdmissionGate` and authority-aware context rendering.
6. Add proposal-based write flow while preserving `save_memory` compatibility.
7. Add consolidation hooks and trace events.
8. Add optional external backend adapters only after local semantics are stable.

## Tests

- frontmatter round trip for each memory type
- scope-key path resolution for user, agent, project, conversation
- legacy flat layout remains readable during migration
- scope precedence on lookup
- index integrity after writes and deletes
- id validation rejects traversal and spaces
- oversized body is rejected with workspace hint
- `expires_at` records are filtered from context
- superseded records are filtered from context
- retrieval returns reasons and stable ordering
- selection respects memory level and token budget
- admission classifies trusted vs context memory
- conversation scope is bound to thread id
- agent scope is bound to agent name
- project scope is bound to project key
- `propose_memory` routes through Policy Gate per scope
- untrusted tool output cannot become memory without review
- direct `add_memory` API bypasses model approval but validates schema
- trace events are emitted for recall, admission, selection, write, delete, and consolidation
