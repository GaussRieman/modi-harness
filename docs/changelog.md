# Modi Harness — Document & Scope Changelog

This file tracks **document-level and scope-level changes** to the Modi Harness
project: contract additions, plan revisions, samples reorganizations, and other
moves that affect how readers should navigate the docs tree.

For code release notes (per-package additions, fixes, breaking changes), see
the repo-root [`CHANGELOG.md`](../CHANGELOG.md).

When this file and the code CHANGELOG disagree on what landed in a release,
the code CHANGELOG wins for what shipped; this file wins for *why* and
*how the documentation map evolved*.

---

## 2026-06-15 — Research Assistant hardening

Research Assistant now demonstrates the boundary clarified after v0.6.e:
Harness handles structured submission, while the example agent handles research
strategy and memory-use discipline.

Document/scope-level highlights:

- **Harness output contract fallback** — required-fields-only structured
  contracts now receive a minimal generated schema, enabling `submit_output`
  without forcing agent authors to write a large JSON Schema by hand.
- **Trace observability expanded** — model turns now expose approximate token
  breakdowns by source, memory, schema, tools, messages, and workspace refs;
  `output_submitted` records the validated output closure; memory events label
  Harness-selected context separately from agent-triggered recalls.
- **Source compression added to the example path** — Research Assistant
  fetches compact evidence cards and can call `source_extract` for raw text,
  keeping the evidence-preparation step smaller before final briefing assembly.
- **Example Agent guidance clarified** — the Research Assistant briefing skill
  now tells the model to use memory already present in context and avoid
  repeated `recall_memory` calls for the same research question.
- **Example test path updated** — the offline Research Assistant demo now
  batches memory recall/write in one model turn and submits the final answer
  via `submit_output`, not raw JSON text.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

---

## 2026-06-12 — V0.6.e execution efficiency

V0.6.e removes deterministic extra model turns exposed by the model-first
posture: one-tool-per-turn serialization and repeated per-turn memory recall.

Document/scope-level highlights:

- **V0.6.e design spec authored** —
  `docs/superpowers/specs/2026-06-12-v0.6e-execution-efficiency-design.md`
  defines the additive execution-layer correction: batch tool execution and
  per-run recall caching.
- **Implementation plan authored** —
  `docs/superpowers/plans/2026-06-12-v0.6e-execution-efficiency-plan.md`
  tracks TDD tasks for batch execution, error isolation, memory-write
  invalidation, cross-process resume, and verification.
- **Runtime efficiency shipped** — `execute_tool_node` now handles multiple
  tool calls in one visit for non-approval batches, and `model_turn_node`
  reuses memory recall/selection results until committed memory writes
  invalidate the run cache.
- **Scope preserved** — no graph topology change, no fixed orchestration, and
  no concurrent tool execution.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

---

## 2026-06-12 — V0.6.d model-first Harness

V0.6.d corrects the architecture posture: Harness is the model's execution
substrate, not a parallel reasoning system.

Document/scope-level highlights:

- **V0.6.d design spec authored** —
  `docs/superpowers/specs/2026-06-12-v0.6d-model-first-harness-design.md`
  defines the model-first posture and implications for Context, Tools,
  Workspace, Memory, Policy, and Trace.
- **Architecture entry point updated** —
  `architecture/README.md` now describes Harness as execution substrate around
  the model.
- **Core concepts reframed** —
  `architecture/00-core-concepts.md` now starts from model intent and positions
  the concepts as support surfaces.
- **Agent authoring guidance added** —
  `architecture/01-agent-loader.md` now advises authors to write task/domain
  behavior instead of Harness-internal rules.
- **Tool descriptions own usage guidance** — builtin Memory/Workspace tool
  descriptions now carry the guidance previously embedded in the
  `research_assistant` agent prompt; the example prompt is now domain-only.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

---

## 2026-06-12 — V0.6.c canonical Memory scopes

V0.6.c removes the legacy Memory scope names from current docs and runtime
plans. The canonical Memory scopes are now:

```text
user, workspace, agent, thread
```

Document/scope-level highlights:

- **V0.6.c design spec authored** —
  `docs/superpowers/specs/2026-06-12-v0.6c-canonical-memory-scopes-design.md`
  defines the breaking cleanup and canonical local Memory layout.
- **Implementation plan authored** —
  `docs/superpowers/plans/2026-06-12-v0.6c-canonical-memory-scopes-plan.md`
  tracks docs, runtime, example, test, and commit work.
- **Memory architecture docs updated** —
  current architecture and implementation docs now describe
  `memory/workspace/<workspace_key>/` and `memory/thread/<thread_id>/`.
- **Workspace key readability improved** —
  workspace Memory now prefers the readable run-file root name, so the research
  assistant example uses `memory/workspace/research_assistant/`.
- **Legacy scope aliases removed from current intent** —
  `project` and `conversation` remain only in historical release notes or where
  `project` is the existing Memory type/category.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

---

## 2026-06-12 — V0.6.b core concepts alignment

V0.6.b aligns architecture language and adds minimal runtime compatibility for
the preferred Memory scope names.

Document/scope-level highlights:

- **V0.6.b design spec authored** —
  `docs/superpowers/specs/2026-06-12-v0.6b-core-concepts-alignment-design.md`
  defines Workspace, Session, Thread, Run, Store, Context, Memory, and Trace
  from first principles.
- **Implementation plan authored** —
  `docs/superpowers/plans/2026-06-12-v0.6b-core-concepts-alignment-plan.md`
  tracks the documentation alignment tasks.
- **Core concepts entry point added** —
  `architecture/00-core-concepts.md` is now the vocabulary entry point for the
  architecture docs.
- **Workspace wording clarified** —
  Workspace is a work boundary. The current `WorkspaceManager` implementation
  manages run files under that boundary.
- **Memory wording clarified** —
  Memory is compact reusable future context. Trace, drafts, artifacts, raw
  sources, and complete task outputs are not Memory.
- **Compatibility terms documented** —
  `project` maps conceptually to `workspace`; `conversation` maps conceptually
  to `thread`. Public API renames are deferred.
- **Memory scope aliases added** —
  runtime code now accepts `workspace` as an alias for the existing `project`
  memory partition and `thread` as an alias for `conversation`. Builtin memory
  tools and `PolicyGate` accept both old and new names.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

---

## 2026-06-10 — V0.6.a Memory architecture upgrade

Memory moved from a flat Markdown store design toward a governed long-term
context subsystem.

Document/scope-level highlights:

- **V0.6.a design spec authored** —
  `docs/superpowers/specs/2026-06-10-v0.6a-memory-architecture-upgrade-design.md`
  covering keyed scopes, staleness filtering, explainable retrieval, admission,
  proposal-based writes, consolidation, and trace events.
- **Implementation plan authored** —
  `docs/superpowers/plans/2026-06-10-memory-architecture-upgrade-plan.md`
  tracks N0-N7 and now serves as the implementation checklist.
- **Architecture docs updated** —
  `architecture/12-memory-store.md` reframed Memory as `MemoryLedger`,
  `MemoryRetriever`, `MemoryAdmissionGate`, and `MemoryConsolidator`.
- **Implementation docs updated** —
  `implement/14-memory-store.md` now describes scope keys, keyed storage,
  retrieval candidates, admission, proposal writes, and migration phases.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

---

## 2026-06-04 — V0.5.0 release

V0.5 implementation complete. 520 tests green. The release is an intentional
**breaking** API reshape: the single God-Object `ModiHarness` is split into
three top-level objects.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

Document/scope-level highlights of the V0.5 development phase:

- **V0.5 design spec authored** —
  `docs/superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md`
  covering the three-object model (`ModiHarness` × `ModiAgent` × `ModiSession`),
  data/execution flow, module layout, the reshaped plugin manifest, and the
  embedded usage example.
- **Breaking API changes (spec §6.3):**
  - `ModiHarness(agents_dir=..., workspace_root=..., checkpointer=..., tools=...)`
    no longer works. `ModiHarness(chat_model=..., *, rule_packs, permissions,
    hook_specs, builtin_tools, kernel_tools)` is now a slim, immutable
    *capability suite* that knows nothing about specific agents or infra.
  - New first-class `ModiAgent` (immutable agent declaration; markdown- or
    code-constructed via `ModiAgent(...)`, `from_markdown(...)`, `load_dir(...)`).
  - New `ModiSession` binds `harness × agents × infra` and is the **sole
    execution entry point**. `run_task` / `resume_task` / `approve_action` /
    `reject_action` / `stream` / `astream` and all introspection / memory /
    hook / thread methods moved off the harness onto the session.
  - `RuntimeAdapter` → `HarnessGraphAdapter`, relocated to
    `graph/harness_adapter.py`; the `runtime/` directory was removed.
  - `PluginInfo` reshaped: `agents_dir` / `skills_dir` / `tools` →
    `agents: list[ModiAgent]` + `kernel_tools: list[ToolBinding]`; plugins now
    parse their own files. Discovery is opt-in via
    `ModiSession.from_discovery(...)`, not auto-run in the harness constructor.
  - Permission-mode argument on session execution methods is `mode=`, not
    `permission_mode=` (the `run_streaming` CLI helper keeps a `permission_mode=`
    alias).
- **Architecture docs rewritten** — `docs/architecture/08-harness-api.md`
  rewritten for the three-object model; `04-runtime-adapter.md` renamed in place
  to *Harness Graph Adapter*; new `04b-session.md` describes `ModiSession`.
- **User-facing docs updated** — `docs/cli.md` (internal-API references),
  `docs/plugins.md` (manifest rewrite + migration table), and `README.md`
  (three-object quick-start) brought in line with the implemented code.
- **Development plan updated** — V0.5 milestones marked complete and a
  `V0.5.0 | shipped` row added to the status table.

---

## 2026-05-29 — V0.3.0 release

V0.3 implementation complete. 269 tests green; 9 smoke scenarios green
(S1–S8 from V0.2 + S9 subagent delegation). Tag `v0.3.0` pushed.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

Document/scope-level highlights of the V0.3 development phase:

- **V0.3 design spec authored** —
  `docs/superpowers/specs/2026-05-29-v0.3-streaming-multiprovider-memory-subagent-design.md`
  covering multi-provider adapter, async streaming, memory levels, and subagent
  sample scenario.
- **New scenario added** — `docs/scenarios/release-coordinator-with-research/`
  demonstrates subagent delegation (release-coordinator dispatches
  research-assistant). Added to `table_of_contents.md`.
- **Types reference expanded** — `MemoryLevel` literal type, `ModelAdapter.acall`
  and `ModelAdapter.astream` async methods, and `create_chat_model` factory
  function documented in `types-reference.md`.
- **Development plan updated** — V0.3 section now shows a milestone status table
  with all four milestones (N0–N3) marked complete.

---

## 2026-05-29 — V0.2.0 release

V0.2 implementation complete. 247 tests green; 8 smoke scenarios green
(S1–S6 from V0.1 + S7 cross-process resume + S8 subagent denied bidirectional).
Tag `v0.2.0` pushed.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

Document/scope-level highlights of the V0.2 development phase:

- **V0.2 design spec authored** —
  `docs/superpowers/specs/2026-05-29-v0.2-langgraph-checkpointer-subagent-design.md`
  (~500 lines), 10 sections covering architecture, AgentState reducers, interrupt
  flow, checkpointer abstraction, thread_id naming, Subagent Runtime, trace
  collaboration, API surface, streaming, milestones N0–N5, risks.
- **V0.2 implementation plan authored** —
  `docs/superpowers/plans/2026-05-29-v0.2-langgraph-checkpointer-subagent-plan.md`
  with N0–N5 task lists (no inline code; the plan is a checklist driving TDD per
  task).
- **Subagent Runtime promoted** —
  `docs/architecture/future/subagent-runtime.md` becomes
  `docs/architecture/16-subagent-runtime.md` to reflect that it shipped.
- **Checkpointer architecture documented** —
  `docs/architecture/17-checkpointer.md` (new) covers backend dispatch,
  single-host sqlite vs multi-host postgres tradeoffs, resume semantics, and
  trace reconciliation.
- **Runtime Adapter doc rewritten for LangGraph-first design** —
  `docs/architecture/04-runtime-adapter.md` no longer describes the
  hand-rolled state machine; it describes the LangGraph wiring and how nodes
  cooperate through `state["pending_trace_events"]` + trace middleware.
- **Hard rule recorded**: LangGraph must always be on its latest release.
  Sub-package version conflicts must be resolved by bumping the sub-package,
  never by downgrading langgraph.

---

## 2026-05-29 — V0.1.0 release

V0.1 implementation complete. 210 tests green, four sample agents land,
six smoke scenarios pass end-to-end. Tag `v0.1.0` pushed.

Code-level details: see [`CHANGELOG.md`](../CHANGELOG.md).

Document/scope-level highlights of the development phase:

- **Development plan codified** — `docs/development-plan.md` added as the
  authoritative V0.1 roadmap (6 milestones, TDD per module, push gating
  on green tests, task-tracking conventions).
- **Implementation order finalized** — 16 steps from foundation to release,
  framework-independent governance modules built and tested before LangChain
  or LangGraph entered the runtime path.
- **Document authority hierarchy locked in** —
  `types-reference.md` > `architecture/` > `development-plan.md` > `implement/`
  for V0.1 scope; documented in `table_of_contents.md`.

---

## 2026-05-28 — Pre-development hardening

Final document pass before code work began. Goal: make the contracts
self-consistent and developer-ready.

### Authoritative types reference

- New: `docs/types-reference.md` (18 sections, ~550 lines).
- Single source of truth for all internal types; architecture and implement
  docs cross-reference it instead of redefining shapes.
- Added: `ThreadInfo`, `StreamEvent`, `ActionMatcher`, `MemoryIndex`,
  ToolSpec defaults table, OutputContract defaults matrix, frontmatter
  hyphen↔underscore mapping rules, LoadedSkill `allowed_tools` tri-state.
- Clarified: `thread_id` is caller-supplied (not ULID); other `*_id` are ULID.

### P0 cross-cutting subsystems documented

Added four subsystem documents that closed the gap between "borrows from
Claude Code" and the actual V0 contract:

- `architecture/12-memory-store.md` + `implement/14-memory-store.md` —
  typed cross-run memory (4 types × 4 scopes), trusted material,
  scope-ordered selection, body size limit, `record_memory` built-in tool.
- `architecture/13-hook-system.md` + `implement/15-hook-system.md` —
  11 lifecycle events, shell + python runners, JSON stdout protocol,
  user→project settings merge.
- `architecture/14-permission-mode.md` — `ask` / `auto` / `plan` / `bypass`
  with full risk×mode decision matrix.
- `architecture/15-untrusted-content.md` — `<untrusted>` wrapping contract,
  standing system note, sanitization, output-controller checks.

### Module contracts repaired

- ContextPack→LangChain conversion ownership: now exclusively Model Adapter.
- Skill `allowed_tools` algebra: tri-state with explicit semantics.
- AgentState fields aligned: `root_run_id`, `thread_id`, `denied_actions`,
  `step_count`, `status`.
- Policy Gate input: explicit `PolicyContext` instead of state grab-bag.
- `run_id` ownership: Runtime Adapter assigns; API never invents.
- Trace location: single authoritative file, async mirror (no dual-write).
- Model Adapter retry/fallback bounds documented.
- `output_contract` defaults: `free_form=True` when omitted by agent.
- Skill multi-source loading + duplicate fail-fast.
- Workspace concurrency: per-run lock, path traversal & symlink rejection.

### Coding-domain decoupling

- Policy Gate's risk list converted from hard-coded coding terms to pluggable
  rule packs (`core` always; `coding` / `messaging` / `finance` opt-in).
- Default agent example expanded from one (case-reviewer) to four spanning
  conversational, research, structured-review, and ops domains.

### Samples restructured

- Old `docs/samples/<name>/` flat layout split into:
  - `docs/agents/<name>/{agent.md, skills/}` — reusable role definitions.
  - `docs/scenarios/<name>/{scenario.md, task.json, tools.md, expected.md}`
    — end-to-end run fixtures.
- Rationale: an agent definition should be reusable across scenarios; a
  scenario should declare the agent + tool registry + expected behavior.
- Added `agents/README.md` and `scenarios/README.md` with authoring guidance.

### Documentation index rewrite

- `table_of_contents.md` now leads with developer reading order
  ("read these in this order during V0.1") and authority hierarchy.
- Removed defunct paths after the agents/scenarios split.

### Archive cleanup

- `docs/modi_harness_arch_v0.md` (Chinese V0 draft) moved to
  `docs/archive/` with deprecation banner explaining how it differs from
  the new authoritative docs.
- During V0.1 final cleanup, `archive/` directory was removed entirely once
  the V0 draft was no longer referenced from any current document.

---

## 2026-05-28 — Initial document set

Original Modi Harness architecture and implementation drafts.

- 11 module contracts under `docs/architecture/`.
- 14 implementation guides under `docs/implement/`.
- Original Chinese design draft `docs/modi_harness_arch_v0.md`.
- Claude Code system prompt reference (`docs/claude_code_system_prompt_原文.md`)
  retained as design inspiration source.

---

## How to Add Entries

When making a documentation change that affects how the docs are read:

1. Add a dated entry at the top under the appropriate header (release date,
   pre-development hardening, etc.).
2. Group changes by area (types, contracts, samples, plan, etc.).
3. Lead each bullet with the *what*; include *why* when the rationale is not
   obvious from the change itself.
4. Keep code-level changes in the repo-root `CHANGELOG.md`; this file is for
   *document* and *scope* changes.
