# Context and Memory

## Context assembly

`ContextManager` builds the canonical `ContextPack` before every model call. It
combines:

- system safety and untrusted-content guidance;
- active Agent and Skill instructions;
- selected Memory blocks and workspace references;
- compact state and confirmed human-context summaries;
- a bounded recent-message window;
- Policy-filtered Tool descriptions;
- the active output contract.

The pack is deterministic and carries a context hash plus trust annotations.
The Model Adapter owns conversion to provider messages.

During finalization, Context Manager replaces normal Agent instructions with a
compact submission instruction and exposes only `submit_output`.

## Memory storage

`MemoryStore` persists small Markdown records with YAML frontmatter. Canonical
scopes are:

```text
thread -> workspace -> agent -> user
```

Physical paths are keyed by `MemoryScopeKeys`; Sessions derive default user,
workspace, Agent, and thread keys. Records are bounded, typed, timestamped, and
may expire or supersede earlier records.

## Retrieval and admission

`memory.retriever` ranks eligible records using deterministic signals such as
scope, tags, text match, and recency. `memory.admission` assigns authority and
selects records for context. There is no embedding or vector-store dependency.

Full selected Memory is injected on the first model step. Later steps receive a
compact reference summary. `RunRecallCache` avoids repeated identical recall
work and is invalidated after committed Memory writes.

Model-requested recall and writes enter through builtin Tools and therefore
still pass Tool Gateway, Policy, hooks, and trace. Memory is reusable context,
not an artifact or raw-output store.

## Source entry points

- `context/manager.py`
- `memory/store.py`, `memory/scope.py`
- `memory/retriever.py`, `memory/admission.py`
- `memory/recall_cache.py`, `memory/consolidator.py`
- `graph/nodes.py`

