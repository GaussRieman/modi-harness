# Memory

Memory gives agents compact reusable records that may be selected into future
context.

See [Core Concepts](./00-core-concepts.md). Memory answers one question: should
future runs possibly see this information again?

## Position

The ledger complements, but does not replace, other persistence layers:

- Chat history lives in `AgentState.messages`, checkpoints, and trace.
- Large source material lives in Workspace.
- Project documentation lives in the repository or external systems.
- Memory holds compact records intended for possible future context.
- Model-facing memory operations are `recall_memory` and `propose_memory`.

Memory is not just a vector database. Retrieval indexes are implementation details; the canonical record remains an auditable memory ledger.

The core distinction:

```text
Memory
  compact reusable record
  examples: user preference, workspace rule, reusable agent method, pointer

Trace
  runtime history of what happened

Workspace run files
  task-specific inputs, refs, drafts, artifacts, logs
```

## Architecture

Memory is split into four responsibilities:

```text
MemoryLedger
  canonical CRUD, scopes, frontmatter files, audit metadata

MemoryRetriever
  candidate recall over ledger records using metadata, text, vector, entity, and time signals

MemoryAdmissionGate
  decides which recalled candidates are allowed into the current context

MemoryConsolidator
  background dedupe, merge, expiration, supersession, and index maintenance
```

The default implementation may keep these in one package, but the boundaries are architectural. Callers should depend on the stable memory facade rather than on storage details.

## Memory Record

```python
class MemoryRecord(TypedDict):
    id: str
    scope: Literal["user", "agent", "project", "conversation", "workspace", "thread"]
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

The stable schema stays small. Advanced fields live in `metadata` until they prove universal:

- `source_kind`, `source_uri`, `source_message_id`
- `confidence`
- `entities`
- `valid_from`, `valid_to`
- `supersedes`, `superseded_by`
- `workspace_ref`
- `access_policy`
- `retrieval_hints`

Compatibility note: current code still uses `project` and `conversation`.
Conceptually, those map to `workspace` and `thread`.

## Types

- `user`: user identity, role, expertise, durable preferences.
- `feedback`: corrections and validated approaches the user has expressed.
- `project`: workspace-level ongoing work, decisions, constraints, deadlines.
- `reference`: pointers to external systems such as tickets, dashboards, docs, or runbooks.

These mirror the durable categories that recur across coding, research, operations, and support workflows.

## Scopes

- `user`: shared across all agents and projects for the same user.
- `agent`: bound to one agent definition across runs.
- `project`: compatibility name for workspace scope; bound to one work boundary.
- `conversation`: compatibility name for thread scope; bound to one task chain.

A record has exactly one scope. Lookup and context selection are scope-aware. Shadowing follows the ordered precedence:

```text
conversation -> project -> agent -> user
```

Scope paths must include the scope key, not just the scope name:

```text
user/<user_key>/
agent/<agent_name>/
project/<project_key>/
conversation/<thread_id>/
```

## Canonical Ledger

The ledger stores records in a human-readable, auditable format. The default local ledger is Markdown files with YAML frontmatter and a concise `MEMORY.md` index per scope key.

The ledger is the source of truth. Retrieval indexes can be rebuilt from it.

Rules:

- Writes go through the Memory API, never direct model-authored file edits.
- Records are append-mostly. Use `update_record` for explicit revisions and `metadata.supersedes` for semantic replacement.
- Deletion is explicit and auditable. A user request to forget removes or tombstones the matching record.
- Large material stays in Workspace; memory stores a summary and a `workspace_ref`.
- `expires_at` is enforced during retrieval and context selection.

## Retrieval

Memory retrieval has two stages:

1. Candidate recall from active scopes.
2. Ranking and packing for the current task.

The local baseline may start with metadata filters and keyword search. Production-ready retrieval should support hybrid signals:

- scope, type, tag, and recency filters
- exact keyword and BM25-style text match
- semantic vector search
- entity match and entity linking
- temporal relevance for current, past, and future facts
- reciprocal-rank or weighted fusion across signals

Retrieval returns candidates with scores and reasons. Context assembly should be able to explain why a memory was selected.

## Selection For Context

Before each model turn, the graph node may select a small set of memory records
from the ledger. This is runtime memory selection, not the model deciding what
to remember.

Memory levels define both allowed types and budget:

- `minimal`: feedback only, small budget.
- `moderate`: feedback, user, and project records.
- `full`: feedback, user, project, and named reference records.

Selection order is no longer purely static. Static priority is a fallback. The preferred flow is:

```text
scope-keyed ledger read
-> candidate recall
-> rank/fuse
-> admission checks
-> token-budget packing
-> ContextPack.memory_blocks
```

Budget packing should prefer high-score, non-expired, non-superseded, scope-relevant records. If a single memory is too large, it should be summarized or replaced by a workspace reference rather than silently crowding out all other memory.

Selected memory is a context channel, not an instruction override. It must stay
small and explainable. It should not turn every stored record into a
system-level fact.

## Model-Initiated Memory Recall

Agents can also call `recall_memory` explicitly. This path is model-initiated:

- The model chooses whether to search.
- The model chooses query, scopes, types, tags, and limit.
- The runtime executes the search inside scope, policy, and budget boundaries.

This preserves agent autonomy without requiring the model to guess hidden user or project state before it has any signal. In short:

```text
automatic selection = selected memory in context
recall_memory       = on-demand memory search
```

## Trust Boundary

Memory retrieval is a trust boundary.

Memory is more authoritative than untrusted tool output, but not every recalled record should automatically become instruction-level authority. A memory may be stale, overbroad, from a weaker scope, or semantically related but inappropriate for the current task.

The `MemoryAdmissionGate` decides whether a candidate can enter context and at what authority:

- `trusted`: durable user feedback or approved project policy that is relevant to the current task.
- `context`: useful background fact, not an instruction.
- `withheld`: expired, superseded, cross-domain, low confidence, or unsafe for the task.

Context Manager must preserve authority separation when rendering memory. It should not collapse all recalled memory into the same trust level.

## Write Lifecycle

Model-facing memory writes should be treated as proposals, not silent durable facts. Direct application or test seeding is different: it is caller-controlled memory creation and should be visibly tied to a `memory_root` and scope keys.

```text
propose_memory
-> validate schema and source
-> policy decision
-> optional human approval
-> commit to ledger
-> update retrieval indexes
-> trace event
```

Durable `user` and `project` writes require approval by default. `conversation` and `agent` writes may be allowed by policy, but still require validation, duplicate checks, and source metadata.

Writes derived from untrusted tool output require a user round-trip or reviewed runtime decision before they can become memory.

Direct APIs such as `session.add_memory(record)` are convenience controls for applications, tests, and examples. They bypass model-side approval because the caller is already outside the model loop, but they must still validate schema and write to a predictable scope-keyed path:

```text
<memory_root>/<scope>/<scope_key>/<record_id>.md
```

## Consolidation

Consolidation is background maintenance over ledger records:

- merge duplicates
- mark stale records as superseded
- expire workspace/project records beyond horizon
- extract entities and retrieval hints
- update summaries for large or noisy records
- rebuild local retrieval indexes

Consolidation must be traceable. It should never erase user intent silently.

## Policy Authority

Memory writes are subject to Policy Gate:

- A model-proposed memory operation is a `RequestedAction` with `kind="memory_write"`.
- Policy routes by scope, source, permission mode, and rule packs.
- `conversation` and `agent` scopes may be allowed by default.
- `user` and `project` scopes require approval by default.
- Writes sourced directly from untrusted tool results are denied unless reviewed.
- Direct user API writes bypass model-side approval but still validate schema and record audit metadata.

Policy Gate controls whether a memory operation may happen. MemoryAdmissionGate controls whether an existing record belongs in the current context.

## Backend Strategy

The default backend is local and auditable:

- Markdown ledger for source-of-truth records.
- SQLite/FTS-style local retrieval index for keyword search.
- Optional vector index for semantic retrieval.

External systems such as Mem0, Graphiti, Cognee, or LangGraph Store may be added as retrieval or storage adapters. They must preserve Modi's scope, policy, trace, and admission semantics.

Adapters should implement the same facade:

```text
write_record(record) -> MemoryRecord
read_record(id, scope_keys) -> MemoryRecord
delete_record(id, scope_keys) -> None
search(query, filters, limit) -> list[MemoryCandidate]
select_for_context(request) -> list[SelectedMemory]
```

## Traceability

Trace Recorder should capture:

- `memory_recall_candidates`
- `memory_admission`
- `memory_selection`
- `memory_write_proposed`
- `memory_write`
- `memory_update`
- `memory_delete`
- `memory_consolidated`

Traces must include record ids, scope keys, scores or reasons when available, and policy/admission decisions.

## Boundaries

- Memory does not store full chat history.
- Memory does not replace Workspace for large artifacts.
- Memory does not override Policy Gate.
- Retrieval indexes are rebuildable implementation details.
- External memory providers do not become authority unless their results pass Modi's admission and policy layers.
