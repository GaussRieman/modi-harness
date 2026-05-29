# Changelog

All notable changes to Modi Harness are documented in this file.

## [0.1.0] - 2026-05-29

First public release. Feature-complete for V0.1 per
[`docs/development-plan.md`](docs/development-plan.md).

### Added

**Foundation (M0)**
- Python package skeleton with `uv` + `pyproject.toml` (hatchling build).
- Typed `Settings` (pydantic) loaded from `.env` and environment, grouped
  into model / runtime / storage / loaders / tools / policy / memory / hooks.
- Authoritative type contracts in `modi_harness.types` mirroring
  `docs/types-reference.md` (18 sections).
- Shared utilities: frontmatter parser (hyphen↔underscore normalization),
  ULID generation, ISO-8601 UTC ms timestamps, canonical JSON, deterministic
  fingerprint and context hash.

**Storage & Loaders (M1)**
- `AgentLoader` — Markdown agents from project / user / plugin sources,
  duplicate-name fail-fast, OutputContract free-form default, tags as
  first-class.
- `SkillLoader` — skill packages with tri-state `allowed_tools`
  (`None` / `[]` / list), asset indexing without body load.
- `WorkspaceManager` — run-scoped layout (input/state/references/artifacts/
  drafts/logs), atomic state writes, snapshots per step, path traversal &
  symlink-escape rejection, per-run lock.
- `MemoryStore` — typed records (user/feedback/project/reference) across
  four scopes (user/agent/project/conversation), scope-ordered lookup,
  rule-based `select_for_context`, ID validation, 4 KiB body limit.
- `TraceRecorder` — append-only JSONL, key-based redaction, large-payload
  offload to workspace, lazy reader.

**Governance & Boundary (M2)**
- `HookSystem` — `pre_tool_use` / `post_tool_use` / `user_prompt_submit` /
  `pre_model_call` / `post_model_call` / `on_*` events, shell and
  `python:module.fn` runners, timeout kill, JSON stdout parsed into
  decision/feedback/redirect, matcher AND-combined, on_failure semantics.
- `PolicyGate` — pure `decide(PolicyContext) -> PolicyDecision`, risk×mode
  matrix, denied-retry guard, plan-mode rewrite, memory_write & output_finalize
  decisions, rule packs (`core` always; `coding` / `messaging` / `finance` opt-in).
- `ToolGateway` — full chain (registry → schema → visibility → denied-retry
  → pre-hook → policy → execute → post-hook → trust-annotated normalize),
  idempotency cache, dry-run dispatch in plan mode.
- `OutputController` — free-form pass-through + structured contract
  (JSON Schema, required fields, citations, risk label, forbidden patterns),
  denied-side-effect reconciliation, prompt-injection / security-keyword checks,
  stable issue codes.

**Context & Model (M3)**
- `ContextManager` — deterministic `ContextPack` builder, tool visibility
  algebra (agent ∩ skill_union ∩ policy.visible_tools), memory blocks
  rendered before references, message windowing, no LangChain dependency.
- `ModelAdapter` — sole owner of `ContextPack → LangChain messages`,
  untrusted-block wrapping with closing-tag escape, tool description binding,
  tool-call extraction (modern + legacy formats), malformed-call surfacing
  without auto-retry.

**Runtime (M4)**
- `RuntimeAdapter` — single-agent loop integrating all M0–M3 modules,
  ULID `run_id`, step limit + repair budget, denied-retry defense in depth,
  approval/rejection flow with workspace snapshot per step, full trace
  emission at every transition.

**API & Developer Entry (M5)**
- `ModiHarness` — single public entry point, `run_task` / `approve_action` /
  `reject_action` / `get_state` / `get_artifacts` / `get_trace` /
  `get_denials` / memory CRUD / thread lifecycle / hook introspection.
- `register_tool(spec, handler, dry_run=...)` for downstream integrators.
- CLI: `modi run --agent NAME --task path.json` + `modi info`.

**Sample Agents & Scenarios**
- Four agents: `support-bot`, `research-assistant`, `case-reviewer`,
  `release-coordinator` (each with bundled skills and frontmatter conforming
  to the type contracts).
- Four default scenarios with `scenario.md` + `task.json` + `tools.md` +
  `expected.md`.

**Evaluation (M6)**
- Six smoke scenarios as pytest tests (S1 happy path, S2 denied retry,
  S3 plan mode, S4 memory round-trip, S5 hook block, S6 denied-side-effect
  output check). 210 tests, all green.

### Non-Goals (V0.1)

- No subagent runtime, no input router (see `docs/architecture/future/`).
- No HTTP server, no web UI.
- No embedding-based memory.
- Single chat-model provider path tested (`langchain-openai`); multi-provider
  abstractions kept open but not exercised.
