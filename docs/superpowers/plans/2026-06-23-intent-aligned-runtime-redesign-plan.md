# Intent-Aligned Runtime Redesign — Implementation Plan

**Spec:** [`docs/superpowers/specs/2026-06-23-intent-aligned-runtime-redesign.md`](../specs/2026-06-23-intent-aligned-runtime-redesign.md)

**Goal:** Re-center Modi Harness from a governance-first runtime to an
intent-first runtime with bounded agent autonomy.

**Core principle:** **Bounded autonomy within human intent.**

**Hard reset rule:** This plan may break public and internal APIs. Do not keep
legacy names or flows merely for compatibility. Keep working infrastructure
only when it serves the new center.

**Model-first rule:** Semantic judgments belong to the model first. Deterministic
logic floors, constrains, and proves model judgment; it does not replace model
reasoning unless a failure pattern is proven.

**Test discipline:** Each implementation task should be TDD: failing focused
test → minimal implementation → green → commit. Commit at the end of each
task group unless a group is explicitly marked as a single atomic change.

---

## File map

### New packages

- `src/modi_harness/intent/`
  - `__init__.py`
  - `types.py`
  - `extractor.py`
  - `clarity.py`
  - `updater.py`
  - `boundaries.py`
  - `stages.py`
- `src/modi_harness/autonomy/`
  - `__init__.py`
  - `modes.py`
  - `scope.py`
- `src/modi_harness/alignment/`
  - `__init__.py`
  - `types.py`
  - `kernel.py`
  - `drift.py`
  - `judgment.py`
- `src/modi_harness/actions/`
  - `__init__.py`
  - `proposal.py`
  - `gateway.py`
  - `integrity.py`
- `src/modi_harness/governance/`
  - `__init__.py`
  - `gate.py`
  - `proof.py`
- `src/modi_harness/trace/lineage.py`

### Major modified files

- `src/modi_harness/types.py`
- `src/modi_harness/graph/state.py`
- `src/modi_harness/graph/harness_adapter.py`
- `src/modi_harness/graph/nodes.py`
- `src/modi_harness/graph/task_protocol.py`
- `src/modi_harness/graph/interaction_protocol.py`
- `src/modi_harness/context/manager.py`
- `src/modi_harness/tools/gateway.py`
- `src/modi_harness/policy/gate.py`
- `src/modi_harness/api/session.py`
- `src/modi_harness/cli/prompt.py`
- `src/modi_harness/cli/runner.py`
- `docs/architecture/*.md`
- `docs/reference/types.md`

### Test packages

- `tests/intent/`
- `tests/autonomy/`
- `tests/alignment/`
- `tests/actions/`
- `tests/governance/`
- `tests/trace/`
- `tests/graph/runtime/`
- `tests/examples/`

---

## N0 — Baseline and guardrails

**Outcome:** The repository has a clean baseline, and new tests can be added
without accidentally preserving the old governance-first center.

- [ ] **N0.1** Run focused baseline:
  - `uv run pytest tests/test_types.py tests/policy tests/context -q`
  - capture any pre-existing failures before edits.
- [ ] **N0.2** Add architecture marker tests that intentionally fail until the
  new center exists:
  - `tests/intent/test_contracts.py::test_human_intent_context_shape_exists`
  - `tests/alignment/test_kernel_contract.py::test_alignment_decision_is_primary`
  - `tests/actions/test_action_proposal.py::test_tool_call_normalizes_to_action_proposal`
- [ ] **N0.3** Add a short `docs/architecture/README.md` note linking this plan
  as the active redesign implementation plan.
- [ ] **N0.4** Commit baseline guardrails.

**Exit gate:** New failing tests name the new concepts: `HumanIntentContext`,
`IntentClarity`, `AutonomyScope`, `ActionProposal`, `AlignmentDecision`.

---

## N1 — Intent types and initialization

**Outcome:** Every run starts with `HumanIntentContext`, even when the user
only gives thin or ambiguous input.

### N1.1 Type contracts

- [ ] Add `src/modi_harness/intent/types.py` with:
  - `IntentClarity`
  - `IntentBoundary`
  - `IntentStage`
  - `ResponsibilityContext`
  - `EscalationPreference`
  - `IntentCorrection`
  - `IntentPatch`
  - `HumanIntentContext`
  - `HumanJudgment`
- [ ] Re-export from `src/modi_harness/intent/__init__.py`.
- [ ] Add compatibility imports in `src/modi_harness/types.py` only if needed
  by existing modules; avoid making `types.py` the permanent home.
- [ ] Update `docs/reference/types.md` with the new type family.
- [ ] Tests:
  - `tests/intent/test_contracts.py`
  - mypy-friendly construction of minimal and full contexts.

### N1.2 Intent extraction

- [ ] Add `intent/extractor.py`.
- [ ] Implement deterministic initial extraction:
  - `goal` from `research_question`, `goal`, `question`, `prompt`, or final
    fallback text;
  - `confirmed_inputs` from known payload fields;
  - default `current_stage=clarify` for thin starts;
  - default `current_stage=explore` when the goal and required inputs are
    present;
  - default boundaries from agent profile and task shape.
- [ ] Allow explicit caller-supplied partial `HumanIntentContext` to override
  inferred fields.
- [ ] Tests:
  - thin input creates a context instead of blocking;
  - `research_question + source_urls` creates an operational research intent;
  - explicit context fragment overrides inferred goal/boundaries.

### N1.3 Graph state seed

- [ ] Replace seed-time `human_context` initialization with
  `human_intent`.
- [ ] Keep old `human_context` only if needed behind a transitional adapter;
  do not let it remain the source of truth.
- [ ] Add `intent_version` and `stage_id` to state if not embedded directly.
- [ ] Tests:
  - `HarnessGraphAdapter._seed_state` includes `human_intent`;
  - a thin task starts with `status="running"`, not failed/blocked.

**Exit gate:** A run can be seeded with thin intent, and state contains
`HumanIntentContext` as the authoritative human-facing context.

---

## N2 — IntentClarity and AutonomyScope

**Outcome:** Runtime derives autonomy from intent clarity, not permission
posture.

### N2.1 Clarity estimation contract

- [ ] Add `intent/clarity.py`.
- [ ] Implement structured model-facing schema:
  - level: `thin | partial | operational | stable`
  - unknowns
  - assumptions
  - confidence
- [ ] Implement deterministic floor:
  - no usable goal → maximum `thin`;
  - missing required agent startup input → maximum `partial`;
  - missing success criteria/boundaries → maximum `operational`;
  - only explicit or strongly inferred complete intent can be `stable`.
- [ ] Implement cold-start fallback:
  - no model verdict → produce safe clarity from deterministic floor.
- [ ] Tests:
  - model says `stable` for empty input, floor clamps to `thin`;
  - no model verdict still produces `IntentClarity`;
  - research-assistant with source URLs and question becomes at least
    `operational`.

### N2.2 Autonomy types and derivation

- [ ] Add `autonomy/modes.py` and `autonomy/scope.py`.
- [ ] Define `AutonomyMode = guided | bounded | delegated | constrained`.
- [ ] Implement mapping:
  - `thin -> guided`
  - `partial -> guided` or `bounded` when only low-risk exploratory actions
    are needed
  - `operational -> bounded`
  - `stable -> delegated`
- [ ] Derive:
  - allowed stages;
  - allowed action kinds;
  - judgment triggers;
  - max tool risk without judgment.
- [ ] Tests:
  - clarity-to-mode mapping;
  - hard boundary forces constrained scope;
  - source collection is allowed under guided mode.

### N2.3 State integration

- [ ] Add `intent_clarity` and `autonomy_scope` to graph state.
- [ ] Build them during setup before first model turn.
- [ ] Add trace events:
  - `intent_initialized`
  - `intent_clarity_estimated`
  - `autonomy_scope_derived`
- [ ] Tests:
  - first trace includes intent/clarity/scope events;
  - state survives checkpoint/resume.

**Exit gate:** Autonomy is derived from intent clarity and visible in state and
trace.

---

## N3 — ContextPack becomes intent-first

**Outcome:** Model context receives current intent, clarity, autonomy scope,
and stage as first-class authority, not as an incidental snapshot.

- [ ] Update `ContextPack` type to include:
  - `intent_context`
  - `intent_clarity`
  - `autonomy_scope`
  - `current_stage`
  - `active_boundaries`
  - `judgment_history`
- [ ] Update `context/manager.py`:
  - inject intent context before memory;
  - keep memory as historical reusable context, not active authority;
  - include unknowns and assumptions explicitly;
  - include current stage and autonomy instructions in system/agent context.
- [ ] Replace `_with_human_context_snapshot` with an intent snapshot function.
- [ ] Tests:
  - intent survives recent-message window trimming;
  - intent appears before memory in provider-neutral pack;
  - memory cannot override active boundaries.
- [ ] Update `docs/architecture/context-and-memory.md`.

**Exit gate:** The model can see “what the human is trying to achieve” and “how
much freedom it currently has” on every turn.

---

## N4 — ActionProposal and AlignmentKernel

**Outcome:** Tool calls and other consequential operations go through
alignment before governance.

### N4.1 Action proposal normalization

- [ ] Add `actions/proposal.py`.
- [ ] Implement `from_tool_call(...) -> ActionProposal`:
  - action id;
  - kind;
  - summary;
  - tool name and args;
  - intent version;
  - stage id;
  - expected outcome when model provides it;
  - action impact from ToolSpec + args + state.
- [ ] Implement `ActionImpact`:
  - risk level;
  - side effect;
  - external commitment;
  - irreversible;
  - user-visible state changes;
  - scope/goal changes;
  - sensitive data;
  - cost impact.
- [ ] Tests:
  - normal tool call becomes proposal;
  - `submit_output` becomes `output_finalize`;
  - `stage_transition` proposal is supported;
  - same tool can produce different impact from args.

### N4.2 Alignment decision contracts

- [ ] Add `alignment/types.py`.
- [ ] Define `AlignmentDecision`, `GovernanceRequirement`, and related helpers.
- [ ] Add `trace/lineage.py` with `IntentLineage` helpers.
- [ ] Tests:
  - decision carries intent version and stage id;
  - lineage can be built from decision + proposal + optional judgment.

### N4.3 AlignmentKernel first version

- [ ] Add `alignment/kernel.py`.
- [ ] Inputs:
  - `HumanIntentContext`
  - `AutonomyScope`
  - `ActionProposal`
  - agent profile
  - existing governance gate adapter
- [ ] Behavior:
  - ask model for semantic boundary/drift judgment when available;
  - apply deterministic hard floor;
  - return `allow`, `ask_judgment`, `redirect`, `constrain`, or `deny`;
  - attach governance requirements, not final policy verdicts.
- [ ] Tests with stub model:
  - model says aligned → allow when no floor blocks;
  - hard boundary says deny → deny even if model says aligned;
  - thin clarity + external side effect → ask_judgment;
  - soft boundary → ask_judgment or redirect depending model verdict.

### N4.4 Governance demotion adapter

- [ ] Create `governance/gate.py` wrapping existing `PolicyGate`.
- [ ] Keep old risk/mode/permission behavior temporarily, but invoke it only
  after `AlignmentKernel`.
- [ ] Rename docs and internal comments where touched:
  - governance proves/enforces;
  - alignment decides primary fit.
- [ ] Tests:
  - alignment allow + governance approval requirement requests judgment;
  - alignment deny never executes even if governance would allow.

**Exit gate:** There is a tested `AlignmentKernel`, and an action can be
allowed/redirected/constrained/denied before governance is applied.

---

## N5 — ActionGateway replaces ToolGateway center

**Outcome:** Execution flow changes from `tool_call -> PolicyGate` to
`tool_call -> ActionProposal -> AlignmentKernel -> GovernanceGate -> execute`.

- [ ] Add `actions/gateway.py`.
- [ ] Move core ToolGateway flow behind the new gateway:
  - registry lookup;
  - schema validation;
  - proposal normalization;
  - alignment decision;
  - governance proof/enforcement;
  - execution/dry-run/interrupt/deny;
  - normalized untrusted result.
- [ ] Keep existing ToolRegistry and builtin handlers.
- [ ] Preserve hook dispatch, but attach lineage metadata.
- [ ] Add `actions/integrity.py`:
  - hash proposal;
  - store reviewed proposal;
  - verify resumed action matches reviewed action.
- [ ] Tests:
  - old L0/L1 execution still works;
  - denied retry remains blocked;
  - reviewed proposal cannot be changed on resume;
  - trace includes action id and alignment decision id;
  - preview/dry-run behavior still works through governance layer.

**Exit gate:** Runtime execution path is action-centered and no longer calls
`PolicyGate` as the first decision point.

---

## N6 — PendingJudgment and public API

**Outcome:** Human participation becomes judgment, not approval.

### N6.1 Types and state

- [ ] Add `PendingJudgment`.
- [ ] Add `HumanJudgment`.
- [ ] Add `judgment_id` and target fields:
  - action id;
  - stage id;
  - prompt;
  - allowed judgment kinds;
  - proposed intent patch;
  - summary and rationale.
- [ ] Replace or bridge `PendingApproval` in `AgentState`.
- [ ] Tests:
  - pending judgment serializes into response;
  - old pending approval tests migrate or bridge through judgment.

### N6.2 Intent updater

- [ ] Add `intent/updater.py`.
- [ ] Apply `HumanJudgment.intent_updates`:
  - update goal;
  - add/modify boundaries;
  - change current stage;
  - add confirmed input;
  - add correction;
  - increment intent version.
- [ ] Recompute `IntentClarity` and `AutonomyScope` after judgment.
- [ ] Tests:
  - judgment updates intent version;
  - revise research question changes goal;
  - constrain adds hard boundary;
  - autonomy broadens/narrows after update.

### N6.3 Public session API

- [ ] Add `ModiSession.respond_to_judgment(...)`.
- [ ] Add adapter-level resume with judgment payload.
- [ ] Keep `approve_action` / `reject_action` only as temporary wrappers that
  emit deprecation warnings and construct judgments.
- [ ] Tests:
  - approve wrapper routes to judgment;
  - direct judgment response updates intent;
  - resume sees updated context.

### N6.4 CLI prompt

- [ ] Rename user-facing prompt from approval to judgment where relevant.
- [ ] Support:
  - approve;
  - reject with reason;
  - revise/redirect with feedback;
  - constrain with boundary text.
- [ ] Tests:
  - prompt returns judgment payloads;
  - runner resumes via `respond_to_judgment`.

**Exit gate:** Human input updates `HumanIntentContext`; approval is no longer
the primary public model.

---

## N7 — Stage alignment

**Outcome:** Stages become the runtime alignment layer above task plans.

- [ ] Add `intent/stages.py`.
- [ ] Support stage kinds:
  - `clarify`
  - `explore`
  - `plan`
  - `execute`
  - `verify`
  - `deliver`
- [ ] Add `stage_transition` ActionProposal.
- [ ] Let agents propose stage transitions.
- [ ] AlignmentKernel evaluates transitions:
  - unclear exit criteria → ask_judgment;
  - hard boundary before delivery → constrain/deny;
  - stable operational context → allow.
- [ ] Keep `TaskPlan` as agent-owned work structure beneath stages.
- [ ] Tests:
  - research-assistant starts in clarify when missing URLs;
  - source URLs + question start in explore;
  - deliver transition blocked until required coverage criteria;
  - human judgment can move/revise stage.

**Exit gate:** The runtime can explain which stage an action belongs to and why
a stage transition was allowed or interrupted.

---

## N8 — Trace lineage and observability

**Outcome:** Trace proves alignment, not just execution.

- [ ] Add lineage fields to relevant trace events:
  - `intent_version`
  - `stage_id`
  - `action_id`
  - `alignment_decision_id`
  - `judgment_id`
  - `boundary_hits`
- [ ] Add events:
  - `intent_initialized`
  - `intent_updated`
  - `intent_clarity_estimated`
  - `autonomy_scope_derived`
  - `action_proposed`
  - `alignment_decision`
  - `judgment_requested`
  - `judgment_resolved`
  - `intent_lineage_recorded`
- [ ] Add `trace/lineage.py` helpers for reading and grouping lineage.
- [ ] Tests:
  - each consequential action has lineage;
  - judgment updates produce new intent version;
  - final output can be traced to intent version and stage;
  - redaction still applies.
- [ ] Update `docs/architecture/workspace-and-trace.md`.

**Exit gate:** A maintainer can answer the acceptance-criteria questions from
trace alone.

---

## N9 — Research-assistant validation slice

**Outcome:** The first real agent proves the redesign end-to-end.

### N9.1 Happy path

- [ ] Use `research-assistant`.
- [ ] Input includes `research_question + source_urls`.
- [ ] Expected:
  - intent is `operational`;
  - autonomy is `bounded`;
  - stage starts at `explore`;
  - fetch actions allowed;
  - deliver stage allowed only after required evidence conditions.
- [ ] Test:
  - `tests/examples/test_research_assistant_intent_runtime.py::test_research_assistant_operational_happy_path`

### N9.2 Thin intent path

- [ ] Input is only a broad request.
- [ ] Expected:
  - intent is `thin`;
  - autonomy is `guided`;
  - runtime allows request for source URLs / clarification;
  - run does not fail for missing full intent.
- [ ] Test:
  - `test_research_assistant_thin_intent_starts_with_guided_autonomy`

### N9.3 Insufficient evidence redirect

- [ ] Simulate failed/insufficient source coverage.
- [ ] Expected:
  - deliver proposal triggers `PendingJudgment`;
  - user can redirect or approve with limitation;
  - intent context records the decision;
  - final trace shows judgment lineage.
- [ ] Test:
  - `test_research_assistant_insufficient_evidence_requests_judgment`

**Exit gate:** Research-assistant demonstrates thin start, operational start,
judgment, resume, and lineage.

---

## N10 — Legacy cleanup and docs

**Outcome:** The code and docs stop presenting governance as the center.

- [ ] Remove legacy `ask`, `plan`, `bypass` permission-mode aliases unless a
  short compatibility window is explicitly chosen.
- [ ] Rename touched docs:
  - “approval prompt” → “judgment prompt” where human participation is broad;
  - “policy decision” → “governance decision” where it is no longer primary;
  - “permission mode” → “autonomy mode” in public docs.
- [ ] Update README Minimal Example only if public API changes.
- [ ] Update `docs/reference/types.md`.
- [ ] Update `docs/architecture/README.md`, `tools-and-policy.md`,
  `execution-runtime.md`, `context-and-memory.md`, `workspace-and-trace.md`.
- [ ] Add changelog entry.
- [ ] Run:
  - `uv run pytest`
  - `uv run mypy`
  - `uv run ruff check .`
- [ ] Commit final docs + cleanup.

**Exit gate:** The public story, architecture docs, and runtime API all say the
same thing: intent shapes autonomy; alignment checks drift; governance proves
safety.

---

## Suggested commit sequence

1. `test: add intent-aligned runtime guardrails`
2. `feat(intent): add human intent context contracts`
3. `feat(intent): initialize thin human intent context`
4. `feat(autonomy): derive scope from intent clarity`
5. `feat(context): inject intent as runtime authority`
6. `feat(actions): normalize tool calls into action proposals`
7. `feat(alignment): add alignment kernel`
8. `refactor(governance): demote policy gate behind alignment`
9. `refactor(actions): route execution through action gateway`
10. `feat(judgment): add pending judgment and response API`
11. `feat(intent): apply judgments as intent updates`
12. `feat(stages): add stage transition alignment`
13. `feat(trace): record intent lineage`
14. `test(example): validate research assistant intent runtime`
15. `docs: align architecture with intent runtime`

---

## Risks watch

- **Model-first ambiguity:** Clarity and boundary judgment may be unstable.
  Keep deterministic floors narrow but hard, and add regression cases only for
  proven failure classes.
- **API sprawl:** Judgment can turn into a workflow engine. Keep the first
  public surface to `PendingJudgment` and `respond_to_judgment`.
- **Over-constraining autonomy:** If every uncertainty triggers a human prompt,
  the redesign fails. Thin intent should still allow clarification and
  reversible exploration.
- **Trace noise:** Lineage fields should be concise and queryable; avoid
  dumping entire intent context into every event.
- **Compatibility drag:** Wrappers for old approval APIs are temporary. Do not
  let them dictate the new state model.
- **Graph node size:** `graph/nodes.py` is already large. New work should split
  semantics into `intent/`, `alignment/`, `actions/`, and small runtime nodes.

---

## Final acceptance gate

The redesign is ready when a completed `research-assistant` run can answer,
from state and trace:

1. What was the human goal?
2. How clear was the intent at each stage?
3. What autonomy did the agent have?
4. Which boundary allowed or constrained each consequential action?
5. What judgment changed the run?
6. Did the final briefing satisfy the success criteria?
7. Which governance events prove the run stayed aligned?

If the answer is still “the policy mode allowed it,” the implementation is not
done.

