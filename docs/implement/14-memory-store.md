# Memory Store

## Module

`modi_harness.memory`

## Purpose

Persist typed memory records and expose them to Context Manager.

## Storage Layout

```text
<memory_root>/
├── user/
│   ├── MEMORY.md           # index, one line per record
│   └── <record_id>.md      # one record per file, with frontmatter
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

Default `memory_root`:

- `user`: `~/.modi/memory/user/`
- `agent`: `~/.modi/memory/agent/<agent_name>/`
- `project`: `<project_root>/.modi/memory/project/`
- `conversation`: `<workspace_root>/threads/<thread_id>/memory/`

`MEMORY.md` is a flat index, never holds record bodies. Each record file uses YAML frontmatter that mirrors `MemoryRecord` fields.

## Design

Implement:

- `MemoryStore`
- `load_index(scopes) -> MemoryIndex`
- `read_record(id) -> MemoryRecord`
- `write_record(record) -> MemoryRecord`
- `update_record(id, patch) -> MemoryRecord`
- `delete_record(id) -> None`
- `search(query, scopes=None, types=None, tags=None, limit=None) -> list[MemoryRecord]`
- frontmatter parser shared with Agent Loader and Skill Loader
- index writer that keeps `MEMORY.md` ordered and under a max line count

No LangChain or LangGraph dependency.

## Resolution

`MemoryStore` is constructed with a `MemoryPaths` dataclass:

```python
class MemoryPaths(TypedDict):
    user: Path
    agent: Path
    project: Path
    conversation: Path
```

Settings provides defaults; callers may override per `ModiHarness` instance.

## Selection Helper

Provide `select_for_context(task, agent_name, scopes, budget) -> list[MemoryRecord]` for Context Manager's convenience. Selection is rule-based:

1. all `feedback` in active scopes
2. all `user`
3. `project` records whose tags intersect task tags
4. `reference` records explicitly named in task or agent

Truncate to `budget` by token count, preferring smaller and newer records when overflow is unavoidable.

## Rules

- Writes are atomic per record file; index update follows.
- Concurrency: file lock per scope index.
- Validate `id` against `[a-z0-9_-]+`; reject path traversal.
- `body` over 4 KB rejects with a hint to move content to Workspace and store a `workspace_ref` in metadata.
- `delete_record` removes both the file and the index line.
- Settings exposes `MODI_MEMORY_ROOT` for user/agent scopes; project and conversation scopes derive from project and workspace roots.

## Settings

Add to `Settings`:

```text
MODI_MEMORY_ROOT=~/.modi/memory
MODI_MEMORY_PROJECT_KEY=     # optional, defaults to project root path hash
MODI_MEMORY_TOKEN_BUDGET=2000
MODI_MEMORY_PROJECT_HORIZON_DAYS=90
```

## Integration

- `ContextManager.build_context` accepts `memory_index` and renders a `memory` block.
- A built-in tool `record_memory` (registered by Memory Store at startup) is the **only** model-facing path to write memory; it has `risk_level=L2` for `conversation`/`agent` scopes and `L3` for `user`/`project` scopes, so Policy Gate can apply different decisions per scope.
- `RuntimeAdapter` invokes Memory Store directly only for read paths and for system-driven writes (e.g. recording the run's own outcome); model-driven writes always go through `record_memory` so they pass Policy Gate.
- `HarnessAPI` adds `add_memory`, `list_memory`, `forget_memory` for direct user control. These bypass model-side approval but still validate schema and trust source.
- `TraceRecorder` records `memory_selection` (per model step), `memory_write`, and `memory_delete` events.

## Tests

- frontmatter round trip for each type
- scope ordering on lookup
- index integrity after concurrent writes
- selection respects token budget
- forget removes file and index entry
- oversized body is rejected with hint
- conversation scope lifecycle bound to thread end
- `record_memory` tool routes through Policy Gate per scope
- direct `add_memory` API write does not require Policy approval
- `memory_selection` trace event recorded on each context build
