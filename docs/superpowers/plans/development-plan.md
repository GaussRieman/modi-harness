# Modi Harness Development Plan

This document is the **authoritative development roadmap**. It defines milestones, conventions, and how task tracking, commits, and pushes are coordinated.

When this document and architecture/implement docs disagree on **scope**, this document wins for the current release. When they disagree on **contracts**, architecture/types-reference wins.

## Status

| Release | Status | Date | Tests |
|---|---|---|---|
| V0.1.0 | shipped | 2026-05-29 | 210 green |
| V0.2.0 | shipped | 2026-05-29 | 247 green; 8 smokes (S1–S8) |
| V0.3.0 | shipped | 2026-05-29 | 269 green; 9 smokes (S1–S9) |

## V0.2 Theme — LangGraph-native runtime + checkpointer + Subagent Runtime

V0.2 replaced the V0.1 hand-rolled `_loop` with a real LangGraph compiled graph
backed by `BaseCheckpointSaver` (sqlite default, postgres opt-in, memory for
tests). It added Subagent Runtime as a first-class capability and a streaming
API. The work was a breaking refactor; V0.1 API contracts are not preserved.

Spec: [`docs/superpowers/specs/2026-05-29-v0.2-langgraph-checkpointer-subagent-design.md`](superpowers/specs/2026-05-29-v0.2-langgraph-checkpointer-subagent-design.md).
Plan: [`docs/superpowers/plans/2026-05-29-v0.2-langgraph-checkpointer-subagent-plan.md`](superpowers/plans/2026-05-29-v0.2-langgraph-checkpointer-subagent-plan.md).

V0.2 milestones:

- **N0** — Checkpoint infra + AgentState reducers + ToolSpec.kind + PermissionProfile fields.
- **N1** — LangGraph main graph + RuntimeAdapter rewrite + ModiHarness rewrite (thread_id keyed).
- **N2** — Subagent Runtime: dispatcher, auto-registration, depth/mode/visibility/denied propagation, 9 e2e tests.
- **N3** — Streaming: `ModiHarness.stream()` projecting LangGraph updates to `StreamEvent` dicts.
- **N4** — S7 cross-process resume + S8 subagent denied-bidirectional smokes.
- **N5** — Documentation + tag.

## V0.3 Theme — Async Streaming, Multi-Provider, Memory Levels, Subagent Scenario

V0.3 delivers four independent features on top of the V0.2 LangGraph runtime:
multi-provider model adapter, async streaming with per-token deltas, configurable
memory injection levels, and a subagent sample scenario demonstrating delegation.

Spec: [`docs/superpowers/specs/2026-05-29-v0.3-streaming-multiprovider-memory-subagent-design.md`](superpowers/specs/2026-05-29-v0.3-streaming-multiprovider-memory-subagent-design.md).

V0.3 milestones:

| Milestone | Feature | Status |
|-----------|---------|--------|
| N0 | Multi-Provider Model Adapter | complete |
| N1 | Async Streaming | complete |
| N2 | Memory Selection Levels | complete |
| N3 | Subagent Sample Scenario | complete |

---

## V0.1 history (kept for reference)

# Modi Harness V0.1 Development Plan

This section preserves the original V0.1 plan as it was authored. V0.1 shipped
2026-05-29 with the contents below; V0.2 superseded the runtime sections but
left the M0–M3 architecture intact.

## Goals

- Reach V0.1: a developer can define a Markdown agent, load skills, register a LangChain-compatible tool, run a single-agent LangGraph loop, interrupt for approval, resume, inspect workspace + trace, write memory, configure a hook.
- Keep contracts (types-reference.md, architecture/) stable; iterate on implementation behind them.
- Land the four sample agents (support-bot, research-assistant, case-reviewer, release-coordinator) running their default scenarios end-to-end.

## Non-Goals (V0.1)

- No subagent runtime, no input router (deferred per architecture/future/).
- No HTTP server, no web UI; Python API + CLI smoke entry only.
- No vector memory, no fine-tuning, no prompt optimization.
- No multi-provider production support beyond `langchain-openai`; provider abstractions stay open but unused.

## Milestones

The plan has 6 milestones, sliced so framework-independent governance is built and tested before LangChain or LangGraph enter the picture.

```text
M0  Foundation                  scaffold, settings, types, utils
M1  Storage & Loaders            agent, skill, workspace, memory, trace
M2  Governance & Boundary        hooks, policy, tools, output controller
M3  Context & Model              context manager, model adapter (LangChain enters)
M4  Runtime                      runtime adapter on LangGraph
M5  API & Developer Entry        ModiHarness, CLI smoke entry
M6  Evaluation & Release         scenarios, golden traces, README, v0.1.0 tag
```

### Why this order

- **M0–M2 needs no LLM, no LangChain, no LangGraph.** All governance, storage, validation, and policy logic lands and is fully unit-tested before any framework dependency is exercised at runtime.
- **M3 is the LangChain seam.** Context Manager stays framework-agnostic; only Model Adapter binds to LangChain. If LangChain semantics shift, blast radius is one module.
- **M4 is the LangGraph seam.** Same logic — only Runtime Adapter knows the graph. M5 Harness API never returns LangGraph types.
- **M6 is the acceptance gate.** No "we shipped" until 4 sample agents × 6 smoke scenarios pass the golden trace comparison.

### Implementation Order Mapping

| Milestone | Modules | Implementation order in `implement/00` |
|---|---|---|
| M0 | foundation, settings, types, utils | 1, 2 |
| M1 | agents, skills, workspace, memory, trace | 3, 4, 5, 6, 7 |
| M2 | hooks, policy, tools, output | 8, 9, 10, 13 |
| M3 | context, models | 11, 12 |
| M4 | runtime | 14 |
| M5 | api | 15 |
| M6 | evaluation | 16 |

Output Controller appears in M2 (not after Model Adapter as in the original sequence) because it has no LangChain dependency and depends only on `AgentState` plus `OutputContract` — building it early lets us close the validation loop before runtime exists.

## Per-Module Workflow (TDD)

Every module follows the same pattern:

1. **Re-read** `architecture/<n>-<module>.md` and `implement/<n>-<module>.md` and the relevant `types-reference.md` sections.
2. **Stub the module** under `src/modi_harness/<module>/` with public symbols matching the design doc (`__init__.py` re-exports).
3. **Write tests first** in `tests/<module>/` covering every bullet in the implement doc's "Tests" section. Tests should fail.
4. **Implement** until tests pass. No additional functionality beyond the doc's scope.
5. **Update memory** if the module exposed a constraint or convention worth preserving.
6. **Commit** (one commit per module, conventional message).

If the doc itself turns out to be wrong or incomplete, **fix the doc first**, then continue. Implementation must not silently diverge from contracts.

## Conventions

### Branch & Commit

- Direct on `main` for V0.1; small, frequent commits.
- One commit per module (or per coherent sub-task within a module).
- Commit format: `<type>(<scope>): <short summary>`.
  - types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
  - scope: module name (`agents`, `skills`, `policy`, ...) or `meta`.
- Commit body explains intent, not mechanics.

### Push

- Push at end of each milestone, or sooner if a meaningful checkpoint lands.
- Before pushing: `uv run pytest` must be green.
- Never force-push `main`.

### Tests

- Framework: `pytest` + `pytest-asyncio` (when needed).
- Layout: `tests/<module>/test_<feature>.py`.
- Use deterministic fakes:
  - `FakeChatModel`: returns canned responses keyed by prompt hash.
  - `FakeTool`: returns canned results, supports failure injection.
  - `FakeClock` and `FakeULIDFactory`: for deterministic IDs and timestamps in tests.
- Snapshot tests for trace events use the trace JSONL format directly; golden files in `tests/golden/`.

### Docs

- Architecture and implement docs are read-only during a milestone unless a contract gap is discovered.
- Contract changes require the doc update to land in the same commit as the implementation change.
- `MEMORY.md` (the agent's working memory, not Modi's Memory Store) is updated when conventions emerge.

## Milestone Details

### M0 — Foundation

**Outcome:** `uv sync && uv run pytest && uv run python -m modi_harness` all succeed. Imports work. Settings loads from `.env`.

**Modules:**
- M0-1 project scaffold + uv environment + `.env.example` + `.gitignore` + `pyproject.toml`
- M0-2 `modi_harness.config.Settings` (pydantic-settings, all 30+ MODI_ keys)
- M0-3 `modi_harness.types` (mirrors all 18 sections of types-reference.md)
- M0-4 shared utilities: frontmatter parser (hyphen/underscore normalization), ULID, ISO-8601 time, canonical-JSON fingerprint, context-hash helper

**Exit criteria:**
- `tests/test_smoke.py` imports `modi_harness`, instantiates `Settings`, asserts default keys exist.
- All shared types round-trip via dict ↔ typed ↔ dict.
- Frontmatter parser passes hyphen/underscore equivalence tests.

### M1 — Storage & Loaders

**Outcome:** All four sample agents load successfully. A run workspace can be created, state can be snapshotted, trace JSONL can be written and read back, memory records can be created and selected.

**Modules:**
- M1-1 Agent Loader (`agents/`) — multi-source resolver, OutputContract & PermissionProfile normalization
- M1-2 Skill Loader (`skills/`) — multi-source, tri-state `allowed_tools`, asset indexing without body load
- M1-3 Workspace Manager (`workspace/`) — run subdir, state snapshot, atomic writes, lock file, path traversal rejection
- M1-4 Memory Store (`memory/`) — frontmatter-backed records, scope-ordered lookup, selection helper, file lock per scope
- M1-5 Trace Recorder (`trace/`) — append-only JSONL writer, redaction, large-payload offload, lazy reader

**Exit criteria:**
- All 4 sample agents (`docs/agents/*/agent.md`) load to a valid `AgentProfile`.
- All bundled skills load to valid `LoadedSkill` (tri-state `allowed_tools` covered).
- A workspace can host two concurrent runs without lock contention.
- Memory CRUD round-trip across all four scopes works; selection respects token budget.
- A trace can be appended during a fake run and read back deterministically.

### M2 — Governance & Boundary

**Outcome:** The full tool-call governance chain runs end-to-end with fake tool handlers and a fake state. Output validation works for both free-form and structured contracts.

**Modules:**
- M2-1 Hook System (`hooks/`) — registry, dispatcher, runner (shell + python), settings.json merge
- M2-2 Policy Gate (`policy/`) — `decide(PolicyContext)`, mode-aware decisions, plan-mode rewrite, denied-retry detector, rule pack registry (`core` always on; `coding`/`messaging`/`finance` opt-in)
- M2-3 Tool Gateway (`tools/`) — registry, schema validation, visibility re-check, denied-retry guard, hook dispatch wrappers, normalize result with trust annotation, idempotency cache, dry-run dispatch
- M2-4 Output Controller (`output/`) — free-form pass-through path, structured path, all stable issue codes, denied-side-effect reconciler

**Exit criteria:**
- Each risk level × each mode produces the expected `PolicyDecision`.
- A blocking `pre_tool_use` hook converts to `DeniedAction` and prevents same-fingerprint retry.
- Output Controller produces stable issue codes and correctly transitions `validated`/`needs_review`/`rejected`.
- The `coding` rule pack denies any model-proposed git mutation tool, even if the agent never declared it.

### M3 — Context & Model

**Outcome:** A complete `ContextPack` can be built from real loaders + real workspace + real memory + real tool catalog. A `FakeChatModel` returns a normalized `ModelResult` through Model Adapter. Untrusted blocks are wrapped correctly. `context_hash` is stable.

**Modules:**
- M3-1 Context Manager (`context/`) — assembly order, trust annotations, tool visibility intersection, memory selection delegation, deterministic context hash
- M3-2 Model Adapter (`models/`) — `to_langchain_messages` (sole conversion entry point), untrusted wrapping, prompt-cache prefix marking, retry/fallback, streaming events, normalized error mapping

**Exit criteria:**
- Same inputs to `build_context` produce identical `context_hash`.
- Untrusted blocks always end up wrapped; trusted blocks never wrapped.
- Tool visibility = `agent ∩ skill_union ∩ policy.visible_tools` per types-reference algebra.
- `FakeChatModel` integration round-trips a tool call proposal through `ModelResult`.

### M4 — Runtime Adapter on LangGraph

**Outcome:** A LangGraph-driven loop can run the entire harness flow with fake tools and a fake model, hitting every conditional edge.

**Modules:**
- M4-1 Graph builder (`runtime/`, `graph/`) — 8 nodes, conditional edges, checkpointer
- M4-2 Run / resume API on the runtime (state init, persist after every behavior-changing transition)
- M4-3 Repair budget, denied-retry guard (defense in depth), step limit
- M4-4 Hook dispatch at all 11 event points
- M4-5 Streaming event emission

**Exit criteria:**
- Smoke scenario S1 (governance happy path) passes end-to-end with fake model.
- Smoke scenario S2 (denied retry) passes.
- Smoke scenario S3 (plan mode) passes.
- Smoke scenario S5 (hook block) passes.
- Trace replay reconstructs a human-readable summary without consulting workspace state.

### M5 — Harness API & Developer Entry

**Outcome:** `from modi_harness import ModiHarness` works as documented. CLI smoke entry runs a sample. All public methods are exercised by tests.

**Modules:**
- M5-1 `ModiHarness` class — all public methods per architecture/08-harness-api.md
- M5-2 Threads — `start_thread` / `end_thread` / `list_threads` with `conversation` memory lifecycle
- M5-3 Streaming — `run_task_stream` returns `Iterator[StreamEvent]`; non-stream `run_task` is thin wrapper
- M5-4 CLI smoke entry — `python -m modi_harness run --agent <name> --task <path>` runs a sample scenario

**Exit criteria:**
- All four sample agents run their default scenario from a single Python script.
- Streaming run terminal event equals non-stream `RunTaskResponse`.
- Thread lifecycle drops conversation memory on `end_thread`.

### M6 — Evaluation & Release

**Outcome:** V0.1 is publishable. All scenarios pass; README explains how to run.

**Modules:**
- M6-1 Smoke scenarios S1–S6 wired as pytest tests with golden trace comparison
- M6-2 At least one smoke uses real `langchain-openai` (gated on `OPENAI_API_KEY` env)
- M6-3 Repo `README.md` (project root) — install, quickstart, link to docs/
- M6-4 `CHANGELOG.md` with V0.1 entry
- M6-5 Tag `v0.1.0`, push tag

**Exit criteria:**
- `uv run pytest -m "smoke"` passes locally with fake model.
- All four sample agents produce expected output structures and trace events.
- `git tag v0.1.0` lands on `main`.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LangGraph API churn | Confine LangGraph imports to `runtime/` + `graph/`; runtime types never escape Modi contracts. |
| LangChain provider differences | Confine to Model Adapter; provider import lazy; `langchain-openai` is the only V0.1 path. |
| Determinism breaking | `context_hash`, `fingerprint`, golden trace are reproduced in tests. Time + ULID injection points are pluggable for tests. |
| Scope creep into V0.2 | Non-goals listed above are gate. Anything outside V0.1 becomes a new milestone in this doc, not a sneaky task. |
| Doc/code drift | Contract changes update the doc in the same commit as the code. |

## Task Tracking

Tasks live in the in-conversation task list. Conventions:

- One **Milestone task** per M0–M6. Subject prefixed with `M<n>`.
- One **Module task** per concrete deliverable inside a milestone, prefixed `M<n>-<k>`.
- Sub-tasks expand only as the milestone gets close (avoid having 30+ pending tasks visible at once).
- Status discipline:
  - `pending` — not started.
  - `in_progress` — actively being worked. **At most one in_progress per milestone.**
  - `completed` — code merged + tests pass + commit pushed.
  - `deleted` — superseded; reason captured in description before deletion.
- New work added during a milestone:
  - If it's required to finish the current module: append a sub-task to that module's description, do not create a new task.
  - If it's a new module-level deliverable: create a new `M<n>-<k>` task.
  - If it's outside the current milestone: create the task under the next milestone, leave it `pending`.
- Plan changes:
  - Adjustments to scope or order are reflected here in `development-plan.md` first, then the task tree is reshaped.

## Plan Update Rules

This document is updated when:

- A milestone changes scope.
- A new risk is discovered.
- A non-goal becomes a goal (or vice versa).
- A convention changes.

Each update is a separate commit with subject `docs(plan): <what changed>`.

## Traceability

| Artifact | Source of truth |
|---|---|
| Module contract | `docs/architecture/<n>-<module>.md` |
| Type definitions | `docs/types-reference.md` (mirrored in `src/modi_harness/types.py`) |
| Implementation guide | `docs/implement/<n>-<module>.md` |
| Sample agents | `docs/agents/<name>/` |
| Scenarios & expected behavior | `docs/scenarios/<name>/` |
| Roadmap & process | `docs/development-plan.md` (this file) |
| Test fixtures | `tests/fixtures/` (created in M0-1) |
| Golden traces | `tests/golden/` (created in M6-1) |

## What Comes After V0.1

Out of scope here, listed only so we know what we're saying "later" to:

- Subagent Runtime (architecture/future/).
- Input Router (architecture/future/).
- HTTP API + minimal dashboard.
- Multi-provider Model Adapter (Anthropic, vLLM, local).
- Embedding-based memory selection (current is rule + tag).
- Plugin system for skills, agents, rule packs.

When V0.1 ships, V0.2 gets its own development plan.
