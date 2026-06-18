# V0.6.a Memory Architecture Upgrade Implementation Plan

**Spec source of truth:** [`docs/superpowers/specs/2026-06-10-v0.6a-memory-architecture-upgrade-design.md`](../specs/2026-06-10-v0.6a-memory-architecture-upgrade-design.md)

**Architecture reference:** [`docs/architecture/12-memory-store.md`](../../architecture/12-memory-store.md)

**Implementation reference:** [`docs/implement/14-memory-store.md`](../../implement/14-memory-store.md)

**Goal:** Upgrade Memory from a flat, rule-selected Markdown store into a governed long-term context subsystem with keyed scopes, expiration/supersession, retrieval candidates, admission decisions, proposal-based writes, and traceable lifecycle events.

**Principle:** Keep the Markdown ledger as the canonical source of truth. Add retrieval, admission, and consolidation as layered capabilities around it. Do not introduce an external memory backend until local semantics are stable.

**Test discipline:** TDD per task. Each milestone should leave the focused memory/context/tool tests green before moving on; run the full suite at milestone boundaries.

---

## File Map

### New files

- `src/modi_harness/memory/scope.py` — `MemoryScopeKeys` and keyed path helpers.
- `src/modi_harness/memory/retriever.py` — `MemoryCandidate`, local candidate recall, score/reason metadata.
- `src/modi_harness/memory/admission.py` — `SelectedMemory`, authority classification, filtering.
- `src/modi_harness/memory/consolidator.py` — index rebuild and future lifecycle maintenance hooks.
- `tests/memory/test_scope_keys.py`
- `tests/memory/test_expiration.py`
- `tests/memory/test_retriever.py`
- `tests/memory/test_admission.py`
- `tests/context/test_memory_authority.py`

### Modified files

- `src/modi_harness/types.py` — add memory candidate/selection contracts if kept globally.
- `src/modi_harness/config/settings.py` — add memory settings for user key, retrieval backend, vector backend, consolidation.
- `src/modi_harness/api/session.py` — construct `MemoryScopeKeys` and pass them into memory calls.
- `src/modi_harness/memory/store.py` — split keyed ledger behavior from compatibility facade.
- `src/modi_harness/memory/__init__.py` — export new memory contracts.
- `src/modi_harness/graph/deps.py` — carry scope keys if graph deps need them.
- `src/modi_harness/graph/nodes.py` — request selected memory through the upgraded facade.
- `src/modi_harness/context/manager.py` — preserve trusted vs context memory authority.
- `src/modi_harness/tools/builtin.py` — add `propose_memory`; keep `save_memory` compatibility.
- `src/modi_harness/policy/gate.py` — ensure write proposals carry source/scope audit.
- `src/modi_harness/trace/recorder.py` — record recall/admission/write lifecycle events.
- `docs/types-reference.md` — sync public type contracts after implementation.
- `docs/builtins.md` — document `propose_memory` and `save_memory` compatibility.
- `CHANGELOG.md` and `docs/changelog.md` — summarize shipped Memory upgrade.

---

## N0 — Scope Keys and Keyed Ledger Paths

After this milestone: memory can be physically partitioned by user, agent, project, and thread while still reading the legacy flat layout.

- [x] **N0.1** Add `MemoryScopeKeys` with `user_key`, `agent_name`, `project_key`, and `thread_id`.
- [x] **N0.2** Add keyed path resolution for `user/<user_key>`, `agent/<agent_name>`, `project/<project_key>`, and `conversation/<thread_id>`.
- [x] **N0.3** Keep legacy flat directories readable as fallback during migration.
- [x] **N0.4** Update `ModiSession` to construct scope keys from settings, active agent, project root, and thread id.
- [x] **N0.5** Extend API memory methods with scope-key-aware behavior while preserving current signatures where possible.
- [x] **N0.6** Tests: scope-key path resolution, legacy fallback, lookup precedence, agent/thread/project isolation.

**Exit gate:** two agents, two project keys, and two thread ids cannot see each other's scoped records unless querying a shared higher scope.

---

## N1 — Expiration and Supersession

After this milestone: stale memory stops entering normal read/search/context paths.

- [x] **N1.1** Enforce `expires_at` in `load_index`, `search`, and `select_for_context` unless `include_expired=True`.
- [x] **N1.2** Support `metadata.supersedes` and `metadata.superseded_by`; filter superseded records from default reads.
- [x] **N1.3** Add project horizon handling using `MODI_MEMORY_PROJECT_HORIZON_DAYS`.
- [x] **N1.4** Add direct read behavior for expired/superseded records when a caller names the id explicitly.
- [x] **N1.5** Tests: expired records omitted from selection, superseded records omitted, explicit read still available or returns a clear tombstone/status per chosen contract.

**Exit gate:** context selection never injects expired or superseded memory by default.

---

## N2 — Local Retriever with Candidates, Scores, and Reasons

After this milestone: memory search returns explainable candidates internally while public compatibility can still return records.

- [x] **N2.1** Add `MemoryCandidate` contract with `record`, `score`, `reasons`, and `signals`.
- [x] **N2.2** Implement local candidate recall using metadata filters plus the current substring matching as baseline.
- [x] **N2.3** Add stable scoring: exact tag/type/scope matches, name/description/body hits, and recency signal.
- [x] **N2.4** Keep `MemoryStore.search(...) -> list[MemoryRecord]` as compatibility wrapper over candidate search.
- [x] **N2.5** Add placeholder backend setting `MODI_MEMORY_RETRIEVAL_BACKEND=local`.
- [x] **N2.6** Tests: deterministic ordering, reasons populated, compatibility wrapper unchanged for existing callers.

**Exit gate:** internal retrieval can explain why each candidate was recalled without changing current public API behavior.

---

## N3 — MemoryAdmissionGate and Authority-Aware Context

After this milestone: recalled memory is not automatically instruction-level trusted.

- [x] **N3.1** Add `SelectedMemory` with `record`, `authority`, `score`, and `reasons`.
- [x] **N3.2** Implement admission filtering for expired, superseded, out-of-scope, low-confidence, and cross-domain records.
- [x] **N3.3** Classify durable feedback and approved project constraints as `trusted`; classify ordinary facts/references as `context`.
- [x] **N3.4** Update ContextManager memory rendering to preserve authority classification.
- [x] **N3.5** Update trust annotations so memory blocks no longer collapse into one trust level.
- [x] **N3.6** Tests: trusted vs context classification, withheld records absent, context hash changes when authority changes.

**Exit gate:** memory authority is visible in `ContextPack` and can be tested independently from retrieval.

---

## N4 — Selection Flow and Budget Packing

After this milestone: `select_for_context` uses recall -> admission -> packing while preserving memory-level behavior.

- [x] **N4.1** Refactor `select_for_context` into candidate recall, admission, and budget packing stages.
- [x] **N4.2** Preserve `minimal`, `moderate`, and `full` memory level semantics.
- [x] **N4.3** Prefer high-score, non-expired, non-superseded, scope-relevant records under budget.
- [x] **N4.4** Use tokenizer-based counting if already available; otherwise keep bytes/4 fallback.
- [x] **N4.5** Add trace payload for selected ids, scores, authority, and reasons.
- [x] **N4.6** Tests: level filters, budget packing, selected order, trace event payload.

**Exit gate:** existing memory-level tests still pass, and new tests prove selection is explainable.

---

## N5 — Proposal-Based Memory Writes

After this milestone: model-facing durable writes flow through proposal, policy, and commit.

- [x] **N5.1** Add builtin `propose_memory` tool schema with scope, type, name, description, body, tags, and source metadata.
- [x] **N5.2** Route proposals through `PolicyGate` as `RequestedAction(kind="memory_write")`.
- [x] **N5.3** Commit allowed/approved proposals to the ledger; deny or return approval requests for the rest.
- [x] **N5.4** Keep `save_memory` as backward-compatible alias for `conversation` and `agent` writes.
- [x] **N5.5** Reject writes derived directly from untrusted tool results unless user-confirmed/reviewed.
- [x] **N5.6** Tests: per-scope policy behavior, duplicate id guard, untrusted-source denial, alias compatibility.

**Exit gate:** no model-facing memory write bypasses Policy Gate.

---

## N6 — Consolidation and Index Maintenance Hooks

After this milestone: lifecycle maintenance has a stable extension point even if advanced consolidation remains conservative.

- [x] **N6.1** Add `MemoryConsolidator` with `rebuild_indexes` and `consolidate(..., dry_run=True)`.
- [x] **N6.2** Implement safe index rebuild for keyed scope paths.
- [x] **N6.3** Add dry-run report structure for duplicates, expired project records, and supersession candidates.
- [x] **N6.4** Do not auto-delete or auto-merge without explicit commit behavior.
- [x] **N6.5** Tests: dry-run report, index rebuild, no silent destructive changes.

**Exit gate:** consolidation can report maintenance opportunities and rebuild indexes without changing memory content.

---

## N7 — Trace, Docs, and Release Cleanup

- [x] **N7.1** Add trace events: `memory_recall_candidates`, `memory_admission`, `memory_selection`, `memory_write_proposed`, `memory_write`, `memory_update`, `memory_delete`, `memory_consolidated`.
- [x] **N7.2** Update `docs/types-reference.md`.
- [x] **N7.3** Update `docs/builtins.md`.
- [x] **N7.4** Update `CHANGELOG.md` and `docs/changelog.md`.
- [x] **N7.5** Run focused tests: `uv run pytest tests/memory tests/context tests/tools`.
- [x] **N7.6** Run full suite: `uv run pytest`.

**Exit gate:** documentation, trace semantics, and tests agree with the upgraded architecture.

---

## Risks Watch

- **API churn:** keep public `add_memory`, `list_memory`, `forget_memory`, `recall_memory`, and `save_memory` compatibility until downstream examples are migrated.
- **Authority rendering:** changing memory trust shape may affect model prompts and context hashes. Make this explicit in tests.
- **Migration ambiguity:** legacy flat directories must remain readable long enough to avoid losing existing local memory.
- **Overbuilding retrieval:** keep N2 local and explainable. Defer vector/graph backends until scope, policy, and admission semantics are solid.
- **Policy bypass:** direct user API writes may bypass model approval, but must still validate schema and record source/audit metadata.
