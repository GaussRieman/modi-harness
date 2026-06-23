# Context and Memory

## Context assembly

`ContextManager` builds the canonical `ContextPack` before every model call. It
combines, in authority order:

- system safety and untrusted-content guidance;
- **the human intent field** — goal, current stage, intent clarity (with open
  unknowns and assumptions), autonomy mode, success criteria, and the active
  boundaries — rendered as first-class authority ahead of memory;
- active Agent and Skill instructions;
- selected Memory blocks and workspace references;
- compact state and confirmed human-context summaries;
- a bounded recent-message window;
- Policy-filtered Tool descriptions;
- the active output contract.

The pack is deterministic and carries a context hash plus trust annotations.
The Model Adapter owns conversion to provider messages.

Intent is authority, not a trimmable message: it lives in the system
instruction (and as structured `intent_context` / `intent_clarity` /
`autonomy_scope` / `current_stage` / `active_boundaries` / `judgment_history`
fields on the pack), so it survives recent-message-window trimming. Active
boundaries are declared immutable; reusable Memory and observation data render
*after* them and cannot relax or override them. The model therefore sees what
the human is trying to achieve and how much freedom it currently has on every
turn.

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

