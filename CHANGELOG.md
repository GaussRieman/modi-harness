# Changelog

All notable changes to Modi Harness are documented in this file.

## [Unreleased]

### Intent-aligned runtime

Re-centered the runtime from governance-first to intent-first: **bounded
autonomy within human intent**. Intent shapes autonomy, alignment checks drift,
governance proves safety.

- Added the human intent context (`HumanIntentContext`) as runtime authority:
  goal, desired outcome, stage, boundaries, non-goals, and success criteria,
  injected into the system instruction rather than a trimmable message.
- Added `IntentClarity` and `AutonomyScope`: clarity is model-estimated (with a
  deterministic ceiling floor) and maps to a guided / bounded / delegated
  autonomy mode that widens the agent's allowed stages and risk as intent firms
  up.
- Added the `ActionGateway`: every model-requested action is normalized into an
  `ActionProposal`, judged by the model-first `AlignmentKernel` against intent,
  then proven safe by the governance/policy gate (which can only tighten).
- Added stages (`clarify`/`explore`/`plan`/`execute`/`verify`/`deliver`) and the
  agent-facing `transition_stage` builtin; entering `deliver` without success
  criteria raises a judgment.
- Added `PendingJudgment` and `respond_to_judgment(kind=...)`: a judgment can
  approve, reject, or correct (revise / redirect / constrain) the run by editing
  the intent, which bumps the intent version and recomputes autonomy.
- Added intent lineage to the trace: `intent_initialized`,
  `intent_clarity_estimated`, `autonomy_scope_derived`, `action_proposed`,
  `alignment_decision`, `intent_lineage_recorded`, `judgment_requested` /
  `judgment_resolved`, `intent_updated`. Lineage events carry only join keys,
  never raw tool arguments.

### Breaking

- Removed the legacy permission-mode aliases `ask`, `plan`, and `bypass`.
  `PermissionMode` is now exactly `auto` / `preview` / `trust`, and
  `normalize_mode` rejects the old names instead of warning. The default mode is
  now `auto` (was `ask`). Migrate: `ask` → `auto`, `plan` → `preview`,
  `bypass` → `trust`.

## [0.7.1] - 2026-06-18

### Dynamic Agent commands

- Added `modi <agent-name>` with registry-backed dynamic resolution.
- Added opt-in Agent-driven startup and the checkpointed `request_user_input`
  protocol for text, multiline, URL-list, choice, and confirmation input.
- Research Assistant now collects URLs and confirms its generated question
  through native interactions before plan review.
- Kept `modi run NAME --task` as an automation compatibility surface rather
  than the primary human interface.

## [0.7.0] - 2026-06-18

### Agent discovery and interactive task runtime

- Added `modi.toml` Agent discovery, source-qualified resolution, trusted project
  factories, and `modi agents list/show/which` diagnostics.
- Added opt-in native task plans with checkpointed create/revise/start/complete/block
  transitions and required-plan output guards.
- Added first-class plan review interactions with approve, revise, and cancel on
  the same thread, separate from policy approval.
- Added live, plain, and JSONL CLI streaming with truthful task progress and
  append-only completion history.
- Promoted Research Assistant into a discoverable project Agent package and
  removed its simulated checklist tools and custom renderer.

### Research Assistant hardening

- Trace now records `output_submitted` after successful output validation, and
  model turns include approximate context token breakdowns plus model elapsed
  time / usage payloads so slow steps can be attributed to sources, memory,
  schema, tools, messages, or workspace refs.
- Context assembly now injects selected memory records only on the first model
  step; later model steps carry a `run_context.memory` reference summary with
  record count and hash instead of repeating full memory bodies.
- Memory trace events now distinguish Harness-managed run memory
  (`harness_memory`) from model-initiated `recall_memory`
  (`agent_recall_memory`).
- The `research_assistant` example now compresses fetched webpages into
  evidence cards, exposes `source_extract` for raw text compression, and
  narrows its prompt flow so source evaluation produces an evidence draft
  before the final briefing is submitted.
- Harness now synthesizes a minimal JSON Schema for structured
  `output_contract.required_fields` blocks that omit an explicit schema. Such
  agents can use the `submit_output` protocol instead of asking the model to
  hand-write raw JSON text.
- Updated the `research_assistant` example to deliver its final briefing via
  `submit_output` in offline tests, keeping the model responsible for content
  while Harness owns structured submission and validation.
- Added Research Assistant skill guidance to avoid repeated `recall_memory`
  calls for the same research question; use memory already present in context
  and call recall at most once when context is insufficient.

### V0.6.e — Execution Efficiency

- Batched tool execution in `execute_tool_node`: when a model emits multiple
  tool calls in one turn, the runtime now executes all non-approval calls
  serially in one node visit and returns one `tool_result` per call instead of
  deferring the tail for the model to re-issue.
- Preserved deterministic side-effect order for batched calls and isolated
  per-call errors so one schema/tool failure does not abort the rest of the
  batch.
- Added an in-process per-run `RunRecallCache` for memory recall/selection.
  `model_turn_node` now reuses recall results within a run until a committed
  `save_memory` or committed `propose_memory` write invalidates the cache.
- No graph topology change and no concurrent tool execution; this is an
  execution-layer efficiency correction.

### V0.6.d — Model-First Harness

- Documented the architecture posture that the model is the reasoning center
  and Harness is the execution substrate around it.
- Reframed Context, Workspace, Memory, and Trace as model-supporting surfaces
  rather than parallel decision systems.
- Added agent authoring guidance: prompts should describe domain behavior and
  output expectations, while Harness usage guidance belongs in tool
  descriptions, policy, context assembly, and runtime behavior.
- Moved Harness usage guidance into builtin tool descriptions: `recall_memory`
  carries recall-before-acting guidance, while `propose_memory` and
  `save_memory` together carry the proposal-vs-durable-write and
  memory-is-not-an-output-store guidance (including `source_kind` on proposals);
  `save_artifact` and `save_draft` now note they are workspace outputs, not
  memory, and `read_workspace_file` clarifies it reads workspace inputs
  (caller-provided files, references, prior drafts).
- Reframed the `research_assistant` agent prompt to domain behavior and output
  expectations only; Memory and Workspace usage now lives in tool descriptions.
- Follow-ups (not in this release, per "no large runtime rewrite"): automatic
  memory preselection and context-assembly minimization.

### Workspace input lifecycle

- Workspace run directories now use lazy materialization: `create_run()` creates
  only the run root, and `input/`, `drafts/`, `artifacts/`, `references/`,
  `state/`, and `logs/` appear only after the first write.
- `ModiSession.run_task`, `stream`, and `astream` now accept `inputs=[...]` for
  caller-provided run input files. The runtime writes them under
  `<workspace_root>/<run_id>/input/` and injects `WorkspaceRef`s into
  `task["input_refs"]`.

### V0.6.c — Canonical Memory Scopes

- Breaking: Memory scopes are now only `user`, `workspace`, `agent`, and
  `thread`.
- Removed `project` and `conversation` as Memory storage scopes and builtin
  tool schema values.
- Memory storage now writes workspace and thread records under
  `memory/workspace/<workspace_key>/` and `memory/thread/<thread_id>/`.
- Workspace memory keys now prefer readable workspace run-file root names such
  as `research_assistant`, falling back to a stable hash only for generic roots.
- `PolicyGate` now routes Memory writes by canonical scopes only:
  `thread`/`agent` may be allowed, while `user`/`workspace` require approval by
  default.
- Updated `research_assistant` to use project-local `.modi/memory` and the
  canonical Memory directory layout.

### V0.6.b — Core Concept Alignment

- Added runtime-compatible Memory scope aliases: `workspace` maps to the
  existing `project` storage partition, and `thread` maps to `conversation`.
- Builtin memory tools now accept `workspace` and `thread` in addition to the
  legacy scope names.
- `PolicyGate` treats `thread` memory writes like `conversation` writes and
  `workspace` memory writes like `project` writes.
- Added architecture docs for the core concepts:
  Workspace, Session, Thread, Run, Store, Context, Memory, and Trace.

### V0.6.a — Governed Memory Architecture Upgrade

- Memory now supports keyed physical scope partitions via `MemoryScopeKeys`: `user/<user_key>`, `agent/<agent_name>`, `project/<project_key>`, and `conversation/<thread_id>`, while legacy flat directories remain readable during migration.
- Normal memory index/search/context selection now filters expired records, superseded records, and project memory beyond the configured horizon; explicit `read_record(id)` remains available for audit.
- Added explainable local retrieval candidates with scores, reasons, and signals; public `search()` remains record-compatible.
- Added admission-aware context selection with `trusted` vs `context` authority metadata on selected memory blocks.
- Added `propose_memory` builtin for policy-governed model-facing writes; `save_memory` remains as a backward-compatible `conversation`/`agent` alias.
- Added safe `MemoryConsolidator` hooks for keyed index rebuilds and dry-run duplicate/expired/superseded reports.
- Added memory trace events for selection and proposal/write lifecycle.

### Permissions model — three-mode product surface

- The product-level mode set is now `auto` / `preview` / `trust`. The legacy four-mode names (`ask` / `auto` / `plan` / `bypass`) remain accepted for one minor release as deprecation aliases that emit `DeprecationWarning` on use.
- `auto` is the default. It collapses the old `ask`/`auto` distinction: when the runtime can prompt a human (`MODI_INTERACTIVE` is unset or truthy), risky actions stage `require_approval`; in non-interactive runs (`MODI_INTERACTIVE=0`) those actions deny instead. No more silent `auto`-without-user-around runs that quietly succeeded only because nobody was watching.
- `preview` replaces `plan`. Same plan-only intent, but L1+ tools that don't declare a `dry_run` handler are now intercepted at the gateway with a synthetic `{"ok": true, "dry_run": true, "simulated": true}` result. Previously `plan` mode would silently pass through (no-op for L0, error or unintended write for L1+); now the agent's plan can complete end-to-end without any side effect, and the trace records `simulated: true` for audit.
- `trust` replaces `bypass` and now requires the operator to set `MODI_ALLOW_TRUST=1` in the environment for the run to start. This is a startup guard, not a per-call check — it's there so a config can't accidentally ship with the policy gate disabled.
- New `settings.permissions` block in `~/.modi/settings.json` and `.modi/settings.json` with three lists: `always_allow`, `always_deny`, `always_ask`. Each entry is either a tool name (exact) or a risk-level token (`L0`..`L4`). User and project files merge (project entries first, deduped). Priority within the layer is `deny > ask > allow`. Hard `deny`s from the agent profile or `core` rule pack still beat `always_allow`.
- Authoritative reference: `docs/architecture/tools-and-policy.md`.

### Fixed
- Streaming runs (`stream` / `astream`) now persist `logs/trace.jsonl` to the workspace. The runtime adapter's per-node accumulator was missing `pending_trace_events`, so streaming runs left empty workspaces. The synchronous `run_task` path was unaffected because `graph.invoke()` returns the cumulative reducer-merged state.
- Subagent dispatch now flushes the child run's trace events to disk. `dispatch_subagent` invoked the child graph but never called `TraceMiddleware.flush()`, so every subagent left an empty workspace under its `run_id`.
- Output contract is now folded into the leading system message instead of appended as a trailing one. Several Anthropic-compatible proxies (GLM gateways, some Chinese vendors) reject multiple non-consecutive system messages with `ValueError: Received multiple non-consecutive system messages`. Previously, any agent with `output_contract` set would fail on those providers.
- `validate_output_node` now appends a `[validation_failed]` repair message (role=`user`) listing every issue when the output is rejected. Previously, a rejected draft just bumped `repair_used` and looped back to `model_turn` with no feedback, so the model retried blind and exhausted the repair budget producing the same bad output. The repair message lists each issue's code, field, message, and hint, so the model has the information it needs to fix the output on the next turn.

### Performance
- `AgentLoader.load_agent` and `SkillLoader.load_skill` cache parsed profiles per file path with mtime-based invalidation. Eliminates redundant YAML parsing on every `model_turn` (~30× speedup on agents, ~16× on skills). Edits to agent/skill files are picked up automatically on the next load via `stat()` mtime check.

### V0.4d — Builtin Tools

- Six kernel-level builtin tools available to every agent without listing in `agent.md`: `read_workspace_file`, `list_workspace_dir`, `save_artifact`, `save_draft`, `recall_memory`, `save_memory`
- `ToolSpec.kind` literal extended with `"builtin"`
- `ModiHarness.__init__` accepts `enable_builtin_tools` (default `True`) and `builtin_tools` (subset filter)
- Builtins still flow through PolicyGate / hooks / trace — only the agent allowlist check is bypassed
- `save_memory` restricted to `conversation` and `agent` scopes (`user` reserved for `harness.add_memory`)
- `save_memory` rejects writes to an existing `id` in any scope. The builtin layer constrains the model; `MemoryStore.write_record` and `harness.add_memory` are unchanged and keep their overwrite semantics for direct API callers.
- See `docs/guides/builtin-tools.md`

## [0.4.2] — 2026-05-29

### V0.4c — Plugin System
- Discover plugins via `modi_harness.plugins` entry point group
- `ModiHarness` accepts `plugins=` and `auto_discover_plugins=` parameters
- Plugins contribute agents, skills, and tools through a single `get_plugin()` function
- New `modi plugins list` CLI subcommand
- Fail-fast error handling: broken plugins raise `PluginLoadError` at harness construction
- New `docs/guides/plugins.md` author guide

## [0.4.1] — 2026-05-29

### V0.4b — Real CLI Experience
- Live streaming output via `rich`: token-by-token model deltas, colored tool activity markers
- Interactive approval prompts: inline `[a]/[r]/[d]` single-keypress decisions
- TTY auto-detection: streams to terminal, emits JSON when piped
- `--stream` / `--no-stream` flags to override auto-detection
- New `cli/` package: `StreamRenderer`, `ApprovalPrompt`, `run_streaming`

### Dependencies
- Added `rich>=13.7`

## [0.4.0] — 2026-05-29

### V0.4a — Model Layer Enhancements
- Per-agent provider override: each agent YAML can specify its own `model:` block with provider, name, api_key, base_url, and fallback config
- Env var expansion: `${VAR_NAME}` syntax in agent model config
- Fallback: on transient failure after retries exhausted, try secondary provider (single hop)
- Error normalization: flat `ModelErrorCode` enum, `ModelError` exception, `classify_error()` classifier
- `ModelAdapterCache` caches per-agent adapters by `(provider, name, base_url)`
- `ModelResult.fallback_used` field tracks whether fallback was used

## [0.3.0] — 2026-05-29

### New Features
- Multi-provider Model Adapter: support OpenAI and Anthropic via `create_chat_model` factory
- Async streaming: `ModiHarness.astream()` and `RuntimeAdapter.astream()` yield per-token `model_delta` events
- Memory selection levels: `minimal` / `moderate` / `full` control memory injection granularity
- Subagent sample scenario: release-coordinator delegates to research-assistant (S9 smoke test)

### Dependencies
- Added `langchain-anthropic>=0.3`

## [0.2.0] - 2026-05-29

**Theme:** real LangGraph runtime + persistent checkpointer + Subagent Runtime.
This is a breaking refactor; V0.1 API contracts are not preserved.

### Added

- **LangGraph main graph** (`modi_harness.graph`): `build_main_graph(deps, checkpointer)`
  returns a `CompiledGraph` with four nodes (`setup`, `model_turn`, `execute_tool`,
  `validate_output`) and conditional edges. Nodes are pure functions; deps are
  passed via `RunnableConfig.configurable["modi_deps"]`.
- **Checkpointer abstraction** (`modi_harness.checkpoint`): `build_checkpointer(settings)`
  dispatches to `MemorySaver` / `SqliteSaver` / `PostgresSaver`. `PostgresSaver` is
  lazy-imported. Settings: `MODI_CHECKPOINT_BACKEND` (default `sqlite`),
  `MODI_CHECKPOINT_SQLITE_PATH`, `MODI_CHECKPOINT_POSTGRES_DSN`.
- **Interrupt + Command(resume=)** flow: approvals use `langgraph.types.interrupt`
  with the decision_kind (`require_approval` | `require_review`) preserved in the
  interrupt payload. `Command(resume={"decision": "approved" | "rejected", ...})`
  resumes the graph from the saved checkpoint.
- **Cross-process resume**: an interrupted run can be resumed by a fresh
  Python process pointed at the same sqlite checkpointer; covered by
  `tests/runtime/test_cross_process_resume.py` (S7 smoke).
- **Subagent Runtime** (`modi_harness.subagent`):
  - Auto-registers a `delegate_to_<agent>` tool per discovered agent at
    `ModiHarness.__init__` (kind=`"subagent"`).
  - `dispatch_subagent(...)` validates visibility (`allowed_subagents`),
    enforces depth cap (`subagent_max_depth`) and permission-mode tightening
    (parent strictness must be ≥ child), propagates parent `denied_actions`
    into child seed state and child diff back into parent on completion.
  - Child output is wrapped as untrusted (`source_kind="subagent_result"`).
  - 9 e2e scenarios in `tests/subagent/test_e2e.py`.
- **Streaming** (`ModiHarness.stream(...)`): yields normalized event dicts
  (`model_delta`, `tool_call_proposal`, `tool_call_result`, `approval_request`,
  `terminal`) projected from `graph.stream(stream_mode="updates")`. Terminal
  payload contains the full `RunTaskResponse`.
- **Trace middleware** (`modi_harness.graph.trace_middleware.TraceMiddleware`):
  cursor-based flush of `state["pending_trace_events"]` into `trace.jsonl`.
  Cursor rebuilds from disk on resume in a fresh process, preventing duplicate
  writes by `event_id`.
- **AgentState additions**: `parent_thread_id`, `pending_trace_events` (with
  `operator.add` reducer), `repair_used`. Append-only list fields
  (`messages`, `tool_calls`, `denied_actions`, `workspace_refs`,
  `pending_trace_events`) now carry `Annotated[..., operator.add]` reducers.
- **ToolSpec additions**: `kind: "regular" | "subagent"` (default `"regular"`),
  `subagent_target: str | None`.
- **PermissionProfile additions**: `allowed_subagents: list[str]` (default
  `[]`, safe), `subagent_max_depth: int | None`.
- **CLI**: `modi resume --thread-id T [--payload P.json]` for Command(resume)
  outside of in-process approval flow.

### Changed (breaking)

- Hand-rolled `RuntimeAdapter._loop`, `_RunContext`, `_runs` removed; runtime
  is a thin wrapper around `graph.invoke` / `Command(resume=)`.
- `ModiHarness` introspection (`get_state`, `get_artifacts`, `get_trace`,
  `get_denials`, `get_hook_results`) is keyed by `thread_id` instead of `run_id`.
- `ModiHarness.approve_action` / `reject_action` take `thread_id` instead of
  `run_id`.
- `ModiHarness.start_thread` removed; threads are implicit on first
  `run_task` and persist in the checkpointer.
- `ModiHarness.resume_task(thread_id, payload)` added as the canonical
  resume entry point.
- `WorkspaceManager.save_state` / `snapshot_state` removed (checkpointer owns
  state persistence); `create_child_run(parent_run_id, child_run_id)` added.

### Dependencies

- Bumped to latest 1.x line: `langgraph>=1.0`, `langchain>=1.0`,
  `langchain-openai>=1.0`. Added `langgraph-checkpoint-sqlite>=2`.
  `langgraph-checkpoint-postgres` is an optional dependency loaded only when
  `MODI_CHECKPOINT_BACKEND=postgres`.

### Tests

- 247 tests green (was 210 in V0.1); 8 smoke scenarios green
  (S1–S6 from V0.1 plus S7 cross-process resume and S8 subagent denied
  bidirectional flow).

## [0.1.0] - 2026-05-29

First public release. Feature-complete for V0.1 per
[`docs/superpowers/plans/development-plan.md`](docs/superpowers/plans/development-plan.md).

### Added

**Foundation (M0)**
- Python package skeleton with `uv` + `pyproject.toml` (hatchling build).
- Typed `Settings` (pydantic) loaded from `.env` and environment, grouped
  into model / runtime / storage / loaders / tools / policy / memory / hooks.
- Authoritative type contracts in `modi_harness.types` mirroring
  `docs/reference/types.md` (18 sections).
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
