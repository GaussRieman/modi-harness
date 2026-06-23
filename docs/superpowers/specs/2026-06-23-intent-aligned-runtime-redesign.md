# Intent-Aligned Runtime Redesign

Date: 2026-06-23

## Decision

Modi Harness should be redesigned as an **intent-first runtime with bounded
agent autonomy**.

The governing principle is:

> **Bounded autonomy within human intent.**

The current runtime is useful, but its center of gravity is still governance:
permission modes, risk levels, approvals, policy decisions, and audit events.
Those mechanisms should remain, but they should move down one layer. The new
center is the relationship between:

- human intent;
- agent autonomy;
- alignment boundaries;
- judgment points;
- consequential actions;
- traceable outcomes.

The target runtime should make this flow explicit:

```text
human intent defines the field
agent autonomy explores within the field
alignment checks boundary and drift
governance preserves and proves alignment
human judgment updates the field
agent resumes with updated autonomy
```

## Why change

The project’s latest first principle says that humans should not micromanage
every step, and agents should not drift away from human purpose. The existing
runtime only partially supports that idea.

Today, the runtime mostly asks:

> Is this action allowed under risk, mode, and permission settings?

The redesigned runtime should ask first:

> Is this action still inside the human intent field?

Only after that should governance decide whether the action requires proof,
approval, dry-run behavior, audit, or denial.

Intent-first does **not** mean intent-complete-before-run.

Users should be able to start with a thin, ambiguous, or partial intent. The
runtime should treat unclear intent as state, not as failure. Its job is to
extract a working intent hypothesis, track unknowns and assumptions, limit
autonomy according to clarity, and let the agent clarify or explore until the
intent becomes operational.

```text
thin intent
-> intent hypothesis
-> limited exploratory autonomy
-> clarification or evidence gathering
-> updated intent context
-> broader autonomy when the field is clear
```

## Non-goals

- Do not preserve old public names merely for compatibility.
- Do not make the product a heavier approval workflow.
- Do not bind agents to human-written step-by-step scripts.
- Do not require users to fully specify intent before a run can begin.
- Do not remove governance, trace, permissions, or output validation; demote
  them to support and proof layers.
- Do not replace LangGraph. LangGraph remains the execution substrate.

## Current mismatch

### 1. `HumanContext` is too weak

Current shape:

```python
HumanContext:
    version
    inputs
    decisions
    feedback
```

This records interaction history, but it does not define the field in which the
agent is allowed to act. It lacks goal, boundaries, stage, success criteria,
responsibility, tradeoffs, and escalation preferences.

### 2. `PolicyGate` is too central

`PolicyGate` is currently the single decider for tool calls, memory writes, and
output finalization. It is anchored to risk level, permission mode, rule packs,
and allow/deny/review lists.

That is governance, not alignment.

### 3. `PermissionMode` describes control, not autonomy

The current modes (`auto`, `preview`, `trust`, plus legacy aliases) describe
permission posture. The first principle requires a mode that describes how much
autonomy the agent has inside the current human intent field.

### 4. Approval is too narrow

`approve_action()` and `reject_action()` make the human role look like a gate.
The runtime needs a richer human judgment model: clarify, redirect, constrain,
revise, approve, reject, cancel, or change the stage.

### 5. Trace is event-complete but not lineage-complete

Trace records what happened, but it does not consistently connect:

```text
intent version -> stage -> action -> alignment decision -> human judgment -> outcome
```

Without that chain, trace proves execution but not alignment.

## Target concepts

### HumanIntentContext

The durable runtime field that defines what the agent is trying to serve.

```python
HumanIntentContext:
    version: int
    goal: str
    desired_outcome: str | None
    boundaries: list[IntentBoundary]
    non_goals: list[str]
    success_criteria: list[str]
    current_stage: IntentStage
    responsibility: ResponsibilityContext
    escalation: EscalationPreference
    tradeoffs: dict[str, str]
    confirmed_inputs: dict[str, Any]
    decisions: list[HumanJudgment]
    corrections: list[IntentCorrection]
```

This replaces `HumanContext` as the primary human-facing state. The existing
input/decision/feedback history can be represented inside `confirmed_inputs`,
`decisions`, and `corrections`.

The context may begin incomplete. Unknowns and assumptions are first-class, so
the runtime can proceed safely without pretending the intent is clearer than it
is.

### IntentClarity

The runtime’s estimate of how operational the current intent is.

```python
IntentClarity:
    level: "thin" | "partial" | "operational" | "stable"
    unknowns: list[str]
    assumptions: list[str]
    confidence: float
```

- `thin`: the user gave only a broad desire or starting point.
- `partial`: enough is known for reversible exploration, but key boundaries or
  success criteria are missing.
- `operational`: the goal, stage, and main boundaries are clear enough for
  bounded execution.
- `stable`: the task is well specified and the agent can be delegated more
  freedom.

Intent clarity should change over the run. Human answers, source evidence,
failed assumptions, and stage transitions can all update it.

### IntentBoundary

A declared edge of the intent field.

```python
IntentBoundary:
    id: str
    kind: "scope" | "risk" | "data" | "tool" | "external_commitment" | "quality" | "cost"
    statement: str
    severity: "soft" | "hard"
    escalation: "continue" | "ask" | "deny"
```

Soft boundaries may trigger clarification or review. Hard boundaries may
constrain or deny action.

### IntentStage

The current phase of work, not a micro-task.

```python
IntentStage:
    id: str
    kind: "clarify" | "explore" | "plan" | "execute" | "verify" | "deliver"
    goal: str
    exit_criteria: list[str]
    judgment_required_before_exit: bool
```

Task plans may remain, but they sit below stages. Humans align stages more
often than individual steps.

### AutonomyMode and AutonomyScope

`PermissionMode` should be replaced or demoted.

```python
AutonomyMode = "guided" | "bounded" | "delegated" | "constrained"
```

- `guided`: high human involvement; useful for ambiguous starts.
- `bounded`: default; agent acts freely inside declared boundaries.
- `delegated`: high autonomy; goal and boundaries are clear.
- `constrained`: low autonomy; risky or responsibility-heavy work.

```python
AutonomyScope:
    mode: AutonomyMode
    intent_clarity: IntentClarity
    allowed_stages: list[str]
    allowed_action_kinds: list[str]
    requires_judgment_for: list[str]
    max_tool_risk_without_judgment: str
```

`AutonomyScope` is derived from intent clarity, active boundaries, stage, agent
declaration, and governance constraints. The default mapping is:

```text
thin        -> guided      # clarify, ask, inspect low-risk context
partial     -> guided/bounded exploratory autonomy
operational -> bounded     # default useful autonomy
stable      -> delegated   # high autonomy inside known boundaries
```

This is what expands the Harness coverage area. A plain workflow usually needs
clear instructions before it can start. Modi Harness should accept ambiguous
starts, turn them into an explicit intent hypothesis, and increase autonomy as
the intent becomes clearer.

### ActionProposal

The model should not send an opaque tool call straight into policy. The runtime
should normalize each proposed action first.

```python
ActionProposal:
    id: str
    kind: "tool_call" | "memory_write" | "output_finalize" | "stage_transition"
    summary: str
    tool_name: str | None
    arguments: dict[str, Any]
    intent_version: int
    stage_id: str
    expected_outcome: str | None
    impact: ActionImpact
```

### ActionImpact

Risk is not enough. The same tool can have different alignment impact depending
on intent, stage, and responsibility.

```python
ActionImpact:
    risk_level: "L0" | "L1" | "L2" | "L3" | "L4"
    side_effect: bool
    external_commitment: bool
    irreversible: bool
    changes_user_visible_state: bool
    changes_scope_or_goal: bool
    uses_sensitive_data: bool
    cost_impact: "low" | "medium" | "high" | None
```

### AlignmentDecision

The new primary decision type.

```python
AlignmentDecision:
    id: str
    decision: "allow" | "ask_judgment" | "redirect" | "constrain" | "deny"
    reason: str
    intent_version: int
    stage_id: str
    boundary_hits: list[str]
    drift_signals: list[str]
    governance_requirements: list[GovernanceRequirement]
```

### HumanJudgment

Human judgment replaces approval as the broad interaction primitive.

```python
HumanJudgment:
    id: str
    kind: "clarify" | "approve" | "reject" | "revise" | "redirect" | "constrain" | "cancel"
    target_action_id: str | None
    target_stage_id: str | None
    rationale: str | None
    intent_updates: IntentPatch
    created_at: str
```

Approval is one judgment kind, not the human interaction model.

### IntentLineage

Trace must connect intent to action and outcome.

```python
IntentLineage:
    intent_version: int
    stage_id: str
    action_id: str
    alignment_decision_id: str
    judgment_id: str | None
    outcome_ref: str | None
```

## Target modules

The codebase may be reorganized without compatibility constraints.

```text
intent/
  context.py              # HumanIntentContext and related types
  extractor.py            # build initial intent from task input
  updater.py              # apply human judgment and corrections
  boundaries.py           # boundary matching and normalization
  stages.py               # stage model and transitions

autonomy/
  modes.py                # guided / bounded / delegated / constrained
  scope.py                # derive AutonomyScope from intent + agent + state

alignment/
  kernel.py               # primary decision engine
  drift.py                # detect drift from goal/boundaries/stage
  decision.py             # AlignmentDecision helpers
  judgment.py             # pending judgment and response protocol

governance/
  gate.py                 # renamed/demoted PolicyGate
  permissions.py          # project/user permission overrides
  proof.py                # audit/proof helpers

actions/
  proposal.py             # ActionProposal normalization
  gateway.py              # replaces ToolGateway as action executor
  integrity.py            # ensure resumed action matches reviewed action

runtime/
  state.py                # graph state around intent/autonomy/alignment
  graph.py                # LangGraph assembly
  nodes.py                # smaller node modules

context/
  builder.py              # ContextPack with intent as first-class authority

trace/
  recorder.py
  lineage.py              # intent/action/judgment/outcome chain
```

The module names are intentionally semantic. The public architecture should
make it obvious that governance is not the center.

## Revised runtime flow

```text
load Agent
-> initialize HumanIntentContext from task input, even if thin
-> estimate IntentClarity and unknowns
-> derive AutonomyScope
-> build ContextPack with intent first
-> model proposes ActionProposal or stage update
-> AlignmentKernel evaluates proposal
   -> allow: execute
   -> ask_judgment: interrupt with PendingJudgment
   -> redirect/constrain: feed correction back to model
   -> deny: block and explain boundary
-> GovernanceGate applies proof/enforcement requirements
-> execute action
-> record IntentLineage
-> update stage/output/memory
-> resume with updated HumanIntentContext
```

## Relationship to existing components

### Keep, but reposition

- LangGraph checkpoint/resume: execution substrate.
- Workspace: run-scoped durable artifacts.
- MemoryStore: reusable context, not active intent.
- ModelAdapter: provider boundary.
- ToolRegistry: action capability catalog.
- OutputController: validation support.
- TraceRecorder: storage backend.
- Agent/Skill loaders: declaration inputs.

### Rename or demote

- `PolicyGate` -> `GovernanceGate`.
- `PermissionMode` -> replaced by `AutonomyMode`; old names may be accepted
  only as temporary adapters.
- `PendingApproval` -> `PendingJudgment`.
- `approve_action` / `reject_action` -> `respond_to_judgment`.
- `HumanContext` -> replaced by `HumanIntentContext`.

### Remove as central concepts

- Risk-level-only decisioning.
- Approval as the primary human participation model.
- Plan review as the only stage-alignment mechanism.
- Trace events that cannot be connected to intent lineage.

## First executable slice

The first slice should prove the new architecture without rewriting every
feature.

### Slice goal

One agent run should:

1. initialize `HumanIntentContext` from task input;
2. estimate `IntentClarity`, including unknowns and assumptions;
3. derive an `AutonomyScope` from clarity and boundaries;
4. convert a tool call into `ActionProposal`;
5. run it through `AlignmentKernel`;
6. request `PendingJudgment` when clarity, boundary, or stage requires it;
7. apply `HumanJudgment` to update `HumanIntentContext`;
8. resume the same run with a changed autonomy scope when appropriate;
9. write trace events with `intent_version`, `stage_id`,
   `alignment_decision_id`, and optional `judgment_id`.

### Slice scope

Use `research-assistant` as the validation agent.

Minimum stages:

- `clarify`: collect source URLs and research question when missing.
- `explore`: fetch and read sources.
- `deliver`: submit structured briefing.

Minimum boundaries:

- do not invent external facts outside provided sources;
- do not fetch unconfirmed replacement URLs after a failed source without
  judgment;
- do not finalize until source coverage and research question are confirmed.

Minimum judgments:

- submit missing source URLs;
- confirm or revise research question;
- approve or redirect finalization if evidence is insufficient.

## Migration strategy

Because the user explicitly allows a clean break, migration should be semantic,
not compatibility-driven.

### Phase 1 — introduce the new center

- Add intent/autonomy/alignment types.
- Seed `HumanIntentContext` in graph state.
- Add `IntentClarity` estimation: model-estimated via structured output, with
  a deterministic completeness floor and cold-start fallback (see D2).
- Add `AlignmentKernel` that initially delegates governance checks to existing
  `PolicyGate`.
- Add `PendingJudgment` alongside existing `PendingApproval`.
- Emit lineage fields in trace.

### Phase 2 — move action execution

- Introduce `ActionProposal`.
- Refactor `ToolGateway` into action proposal normalization + action execution.
- Make `AlignmentKernel` the first decision point.
- Demote `PolicyGate` to governance proof/enforcement.

### Phase 3 — replace approval APIs

- Add `respond_to_judgment`.
- Route approval/reject flows through judgment responses.
- Remove approval-specific state as a top-level concept once tests migrate.

### Phase 4 — stage alignment

- Replace task-plan review semantics with stage-level alignment.
- Keep task plans as agent-owned execution structure.
- Add stage transition judgments.

### Phase 5 — cleanup and rename

- Remove legacy permission-mode aliases.
- Rename docs and code references from permission-first to autonomy-first.
- Update CLI language from “approval prompt” to “judgment prompt” where
  appropriate.

## Testing strategy

Tests should prove the first principle, not just legacy behavior.

Because clarity estimation and boundary judgment are model-first (D2, D4),
tests drive them with a stub model returning fixed structured output, then
assert on how the runtime *handles and enforces* that judgment — clarity floor
clamps an over-confident estimate, hard boundaries block even when the model
judged "aligned", cold-start fallback proceeds without a model verdict. The
tests prove the constraint and proof behavior, not a heuristic algorithm.

Required test groups:

- intent initialization from task input;
- thin-intent initialization without blocking the run;
- intent clarity updates from human input;
- autonomy scope derivation;
- boundary match and drift detection;
- action proposal normalization;
- alignment decision outcomes;
- human judgment updates intent context;
- resumed action sees updated intent;
- governance proof events contain lineage IDs;
- research-assistant happy path;
- research-assistant redirect path when source coverage is insufficient.

Regression tests may keep old approval behavior only until the new judgment
model fully replaces it.

## Acceptance criteria

The redesign is successful when a maintainer can inspect a run and answer:

- What was the human goal?
- What boundaries defined the agent’s autonomy?
- What stage was the agent in when it acted?
- Why did the runtime allow, constrain, redirect, ask, or deny an action?
- What human judgment changed the run?
- Did the final output satisfy the success criteria?
- Which governance events prove the above?

If the answer is only “risk level allowed it,” the redesign has failed.

## Resolved decisions

These were open during brainstorming and are now settled. The governing bias
is **model-first**: understanding and reasoning tasks (how clear is the intent,
is this action inside the field) are solved by the model first, because they
are semantic judgments. Deterministic rules exist to **constrain, floor, and
prove** that judgment — hard red lines the model cannot cross and audit the
runtime can replay — not to replace it. We try the model's capability first and
add constraints only where it underperforms. The Harness is the method, not the
goal; it extends model capability and does not compete with model reasoning
(see the model-first first principle).

### D1 — Intent source: inferred, with explicit override

`HumanIntentContext` is initialized by inferring from `TaskInput` by default.
API callers may additionally pass an explicit `HumanIntentContext` (or a
partial fragment) to override or supplement the inferred field. This keeps the
"thin intent can start a run" property while letting integrators seed a richer
field when they have one. (was Q1)

### D2 — `IntentClarity`: model-estimated, deterministically floored

The model is the primary estimator of clarity. Since the model already reads
the task input to act, it also judges how operational the intent is and emits
`level`, `unknowns`, and `assumptions` through a structured-output contract.
The runtime does not pre-decide clarity with a heuristic and hand the model a
verdict.

Deterministic checks remain as a **floor and a guard**, not as the estimator:

- a minimum-clarity floor from input completeness (e.g. no goal and no source
  at all cannot be reported as `stable`), so a mis-estimating model cannot
  unlock more autonomy than the input could justify;
- a fallback estimate when no model judgment is available (cold start, model
  error), so the run still proceeds safely on a thin intent.

If the model proves to under- or over-estimate in practice, tighten the floor —
do not replace the model with the heuristic. (was Q2)

### D3 — Initial `AutonomyMode`: derived from `IntentClarity`

`AutonomyMode` is derived from clarity using the default mapping in the
AutonomyScope section (`thin -> guided`, … `stable -> delegated`). The caller
does not select the mode directly in the first version, and agent profile does
not override it yet. Derivation is automatic and deterministic. (was Q3)

### D4 — Boundary matching: model semantic judgment over a deterministic floor

Whether an action is inside the intent field is a semantic question, so the
model judges it first. The `AlignmentKernel` presents the active
`IntentBoundary` statements and the normalized `ActionProposal` to the model
and lets it reason about boundary hits and drift, including natural-language
`statement` boundaries.

Deterministic structured matching is the **hard floor underneath** that
judgment, not the primary matcher. It matches on structured signals (action
`kind`, `risk_level`, `side_effect`, `external_commitment`, tool name, scope
tags) and enforces `hard` boundaries the model cannot reason away — e.g. a
`hard`/`deny` boundary still blocks even if the model judged the action
aligned. Soft boundaries and natural-language drift are the model's call; hard
red lines are the runtime's. If the model proves unreliable on a class of
boundaries, promote that class into the deterministic floor — do not move all
matching back to rules. (was Q4)

### D5 — Stage transitions: agent-proposed

Stage transitions are proposed by the agent as a `stage_transition`
`ActionProposal` and evaluated by `AlignmentKernel` (which may raise a
judgment). The runtime does not infer stage transitions from events in the
first version. This keeps the model as the subject of stage progression,
consistent with `ActionProposal.kind`. (was Q5)

### D6 — Public API: minimal judgment surface

The only new public surface is `PendingJudgment` on the run/stream response and
a `respond_to_judgment(judgment)` entry point. Intent, autonomy, and alignment
are handled entirely inside the runtime and are not exposed as configuration or
read APIs in the first version. This avoids turning the product into a generic
workflow engine. (was Q6)
