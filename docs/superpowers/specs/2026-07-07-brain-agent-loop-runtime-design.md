# Brain-Agent Loop Runtime

Date: 2026-07-07

## Decision

Modi Harness should promote `AgentLoop`, `Brain`, and `Step` to first-class
runtime concepts.

The current intent-aligned runtime already made human intent, autonomy scope,
alignment, and action governance explicit. The next redesign moves one layer
up: an Agent is no longer primarily a Markdown instruction that produces model
turns and tool calls. An Agent becomes a durable intent execution body whose
life cycle is owned by a Loop and whose control decisions are made by a Brain.

The governing principle is:

```text
AgentLoop owns the lifecycle.
Brain controls the next semantic step.
Step records progress.
RuntimeOperation runs only when a step needs consequential work.
Harness governs runtime operation execution.
```

This changes the center of the runtime from:

```text
model_turn -> tool_call -> action gateway -> result
```

to:

```text
RuntimeEvent + Intent + LoopState
-> AgentLoop.resume()
-> Brain.plan_step()
-> StepDecision
-> optional RuntimeOperation execution
-> StepRecord
-> AgentLoop decides continue / wait / finish
```

Actions remain important, but they are no longer the semantic unit of agent
progress. They are one kind of runtime operation inside a step, alongside
stage transitions, memory writes, and output finalization.

## Why change

The action-centered runtime can prove whether a proposed tool call is aligned,
governed, and traceable. It still leaves the highest-level control question too
implicit:

> Why is this the next step in the life of this intent?

Today that control behavior is mostly encoded inside a large Agent instruction,
the model's free-form reasoning, and graph node routing. That makes simple
tasks pay the cost of slow reasoning and makes complex tasks hard to audit as a
sequence of intentional progress decisions.

The redesign separates three concerns:

- `AgentLoop`: owns life cycle, status, resume, checkpoint, step count, and
  continuation policy.
- `Brain`: owns control reasoning for the next step, including fast rule-driven
  planning and slow model-driven planning.
- `Harness` / runtime operation gateway: owns low-level validation, alignment,
  governance, execution, and operation trace.

This lets Modi Harness support both "I know that I know" work and "I do not
yet know what I do not know" work:

```text
known knowns       -> fast Brain mode -> human-authored rules decide quickly
unknown unknowns   -> slow Brain mode -> model reasons under structure
```

## Non-goals

- Do not complete the full low-level action taxonomy in this redesign.
- Do not remove `HumanIntentContext`, `AutonomyScope`, `ActionProposal`,
  `AlignmentKernel`, or `ActionGateway`; they remain the execution and proof
  layers beneath steps.
- Do not make Brain a tool executor or permission gate.
- Do not put all Agent behavior back into one large `.md` file.
- Do not make users author step-by-step scripts for every run. Rules speed up
  known cases; slow mode still handles open-ended work.
- Do not replace LangGraph immediately. LangGraph may remain the execution
  substrate while the domain model becomes Loop-first.

## Core concepts

### Agent

An Agent is the composition of identity, intent defaults, Brain configuration,
Loop policy, skills, tools, output contract, and runtime constraints.

The old shape:

```text
Agent = markdown instruction + tools + skills
```

becomes:

```text
Agent = IntentDefaults + Brain + LoopPolicy + Skills/Tools + OutputContract
```

Markdown remains useful for instructions and slow reasoning guidance, but it no
longer carries the entire control layer.

### AgentLoop

`AgentLoop` is the durable life-cycle controller for one active intent run. It
is not merely a `while` loop. It owns the status and continuation contract of a
run.

```python
AgentLoop:
    loop_id: str
    run_id: str
    agent_name: str
    status: "active" | "waiting" | "completed" | "failed" | "cancelled"
    intent_version: int
    stage_id: str
    step_index: int
    max_auto_steps: int
    continuation: LoopContinuation
    last_event_id: str | None
    pending_step_id: str | None
```

Responsibilities:

- create and resume an intent run;
- build `StepContext` from the current event, intent, state, and Agent spec;
- call `Brain.plan_step()`;
- apply intent and stage updates proposed by Brain only through controlled
  state mutation;
- execute an operation when a step requires one;
- write a `StepRecord`;
- decide whether the run continues automatically, waits for human input,
  finishes, or fails;
- checkpoint after every durable boundary.

Non-responsibilities:

- it does not decide semantic control without Brain;
- it does not execute tools directly;
- it does not bypass alignment or governance.

### Brain

`Brain` is the Agent's control layer. It decides the next semantic step from
the current intent, stage, loop state, Agent state, available capabilities, and
incoming event.

```python
Brain:
    spec: BrainSpec

    plan_step(context: StepContext) -> StepDecision
```

Brain can update its interpretation of intent by proposing a non-stage
`BrainIntentPatch`, but the Loop applies that patch. Brain can propose a
runtime operation, but the Harness executes it. Brain never writes
checkpointed state directly.

Brain must remain a decision producer, not a flow engine. It must not own loop
state, interpret execution results after the fact, persist step records, or run
postchecks. Brain may propose a `postcheck`; Loop or Harness executes and
records it.

### Fast mode

Fast mode is rule-driven and model-free. It handles known, repetitive, or
mechanically decidable control situations.

Fast rules should be human-authored or human-approved. A rule says:

```python
FastRule:
    id: str
    priority: int
    match: RuleCondition
    decision_template: StepDecisionTemplate
    confidence: float
    source: "human_explicit" | "human_correction" | "approved_learned"
```

Examples:

```text
If the current stage is clarify and all required inputs are present,
transition to plan.

If a known hard boundary would be crossed, ask for judgment.
```

Fast mode represents:

```text
I know that I know.
```

It should be cheap, deterministic, explainable, and traceable to a rule id.

Fast rules are intentionally narrow. In the first version, a FastRule may only
cover known-known control in three categories:

1. required input is missing, so produce a `clarify` step with an `ask`;
2. stage exit criteria are satisfied, so propose a `stage_transition` runtime
   operation;
3. a known hard boundary is triggered, so wait for human judgment.

Fast rules must not expand into full scripts or multi-step workflows. Anything
outside those categories falls to slow mode. This keeps fast mode from becoming
a parallel workflow engine.

Fast mode is a candidate path, not an interrupt policy. A rule miss, conflict,
exception, invalid template, or invalid fast `StepDecision` must fall through to
slow mode with traceable miss/failure evidence. The only fast path that may wait
without slow fallback is an explicit semantic judgment boundary, such as a known
hard boundary that already maps to `human_judgment.required == true`.

### Slow mode

Slow mode is model-driven structured control reasoning. It handles ambiguous,
complex, conflicting, or novel situations.

Slow mode receives the same `StepContext` plus Brain instructions and returns a
validated `StepDecision`. This is not a requirement that the model reliably
emits runtime schema unaided. The slow boundary is:

```text
model output -> Brain adapter/normalizer -> StepDecision -> Loop validation
```

The model may reason about unknowns, task decomposition, stage transitions,
missing information, and verification needs, but it may not directly execute
tools. The adapter should accept the preferred structured protocol output, then
normalize recoverable model intent into a safe `StepDecision`. If the model
skips the protocol tool, calls a business tool directly, or produces natural
language that clearly maps to a safe next step, the adapter may normalize it.
Only unrecoverable normalization failure or an actual semantic judgment point
should become a human handoff.

Slow mode represents:

```text
I do not know what I do not know yet.
```

Slow mode is entered when:

- no fast rule matches;
- matching rules conflict;
- matched rule confidence is below the configured threshold;
- intent clarity is too low for the proposed autonomous step;
- the current stage has unresolved exit criteria;
- previous step failure invalidated a known path;
- the Loop is resuming after human correction that changes the field.

Human judgment is last resort in this chain. The order is fast known-known
rules, slow model reasoning plus adapter normalization, then human judgment when
normalization cannot recover safely or the step itself needs human judgment.

### Step

`Step` is the semantic progress unit of a loop. It is the thing a maintainer
should inspect when asking, "How did this run move forward?"

Step kinds:

```python
StepKind = Literal[
    "clarify",
    "plan",
    "observe",
    "act",
    "verify",
    "handoff",
    "finish",
]
```

`Step` sits above low-level action:

```text
Step = semantic progress decision
Action/Operation = execution detail inside a step
```

### StepContext

`StepContext` is the input Brain sees.

```python
StepContext:
    loop: LoopSnapshot
    event: RuntimeEvent | None
    intent: HumanIntentContext
    intent_clarity: IntentClarity
    autonomy_scope: AutonomyScope
    stage: IntentStage
    agent_state: AgentStateSnapshot
    recent_steps: list[StepRecordSummary]
    available_capabilities: CapabilityCatalog
    brain_spec: BrainSpec
```

The context should be compact. It is not the full conversation or full trace.
It contains enough state to choose the next step and enough lineage to avoid
repeating failed moves.

### StepDecision

`StepDecision` is Brain's output.

```python
StepDecision:
    id: str
    step_kind: StepKind
    reasoning_mode: "fast" | "slow"
    reason: str
    rule_ref: str | None
    intent_patch: BrainIntentPatch | None
    ask: AskRequest | None
    operation: RuntimeOperationProposal | None
    expected_state_change: dict[str, Any] | None
    postcheck: StepPostcheck | None
    continuation: "continue" | "wait" | "stop"
    human_judgment: HumanJudgmentAssessment
    continuation_basis: ContinuationBasis | None
```

A decision can ask, transition, execute an operation, verify, or finish, but it
must be explicit about continuation. This avoids hiding control flow in tool
results.

StepDecision invariants:

- `ask` and `operation` are mutually exclusive. A step either asks the human or
  proposes an operation, never both.
- `step_kind == "finish"` must not carry `ask` or `operation`; it stops the
  loop after output validation/finalization has succeeded.
- `intent_patch` may accompany any decision, but it is a `BrainIntentPatch`,
  not the full human `IntentPatch`. It must not contain stage mutations such as
  `set_stage`. The Loop applies valid non-stage intent updates before
  continuing and records the resulting intent version.
- Stage transitions are not direct state mutations inside `StepDecision`. A
  Brain-requested transition is represented as `operation.kind ==
  "stage_transition"` so it goes through the same alignment, governance,
  integrity, and trace path as other consequential operations. The Loop applies
  the new stage only after that operation succeeds.
- `postcheck` may accompany an operation or verification step. It never
  executes before the operation it checks.
- `human_judgment` must explicitly say whether Brain believes human judgment
  is required now. If not required, it must provide a reason tied to intent,
  stage, autonomy scope, or a fast rule.
- If `human_judgment.required == true`, the decision must not carry
  `operation`. The Loop must enter waiting/handoff and no runtime operation may
  execute until the judgment is resolved.
- `continuation_basis` is required when `continuation == "continue"`. It must
  state why another automatic step is justified.
- `operation` with `continuation == "wait"` is invalid unless the decision also
  carries an ask or required human judgment. Since ask and operation are
  mutually exclusive and required judgment cannot carry an operation, ordinary
  operation steps should request `continue`; the Loop can still stop later if
  governance, postcheck, budget, or execution result creates a blocker.

### HumanJudgmentAssessment

Brain must not leave judgment needs implicit.

```python
HumanJudgmentAssessment:
    required: bool
    reason: str
    trigger: (
        "none"
        | "missing_input"
        | "boundary"
        | "stage_gate"
        | "autonomy_scope"
        | "operation_risk"
        | "failure_recovery"
    )
```

When `required == true`, the decision must carry `ask` or set
`continuation == "wait"`, and it must not carry an operation. When `required ==
false`, the reason must explain why the current autonomy field is sufficient
for the step.

### ContinuationBasis

`ContinuationBasis` explains why the Loop may take another automatic step.

```python
ContinuationBasis:
    source: (
        "fast_rule"
        | "stage_exit_criteria"
        | "postcheck_result"
        | "autonomy_budget"
        | "slow_plan"
    )
    reference: str | None
    reason: str
```

Loop still makes the final continuation decision, but Brain must provide the
semantic basis when it asks to continue.

### BrainIntentPatch

`BrainIntentPatch` is the subset of `IntentPatch` that Brain may propose
inside a `StepDecision`.

```python
BrainIntentPatch:
    goal: str | None
    desired_outcome: str | None
    add_boundaries: list[IntentBoundary]
    remove_boundary_ids: list[str]
    add_non_goals: list[str]
    add_success_criteria: list[str]
    confirmed_inputs: dict[str, Any]
    tradeoffs: dict[str, str]
```

It intentionally excludes `set_stage`. Stage changes are control-flow
operations, not ordinary intent edits, and must use
`RuntimeOperationProposal(kind="stage_transition")`.

The Loop must reject any Brain-authored decision whose intent patch contains a
stage field or unknown mutation key. Human judgments may still use the broader
`IntentPatch` shape when a human explicitly revises the run.

### RuntimeOperationProposal

`RuntimeOperationProposal` is the thin bridge from Step to existing action
runtime.

```python
RuntimeOperationProposal:
    kind: "tool" | "output_finalize" | "stage_transition" | "memory_write"
    summary: str
    target: str
    arguments: dict[str, Any]
    expected_outcome: str | None
```

Runtime operation variants:

```python
ToolOperation
StageTransitionOperation
MemoryWriteOperation
OutputFinalizeOperation
```

In the first implementation, `RuntimeOperationProposal` can still map directly
to the existing `ToolCallProposal -> ActionProposal -> ActionGateway` path. The
deeper operation taxonomy can evolve after the Loop and Step contracts are
stable.

`OutputFinalizeOperation` happens inside a `verify` or `act` step. A later
`finish` step records that the Loop is complete and must not carry its own
operation. This preserves the invariant that `step_kind == "finish"` is a
terminal lifecycle record, not an execution step.

For `kind == "stage_transition"`, the minimum operation arguments are:

```python
stage_transition.arguments:
    from_stage: str
    to_stage: str
    reason: str
```

The executed operation must return the validated target `IntentStage`. The Loop
applies that returned stage, not the raw `to_stage` argument, so the runtime has
one place to enforce stage existence, exit criteria, judgment gates, and trace
lineage.

### StepRecord

`StepRecord` is the durable audit record for one step.

```python
StepRecord:
    step_id: str
    loop_id: str
    run_id: str
    index: int
    step_kind: StepKind
    status: "planned" | "running" | "waiting" | "completed" | "failed"
    intent_version: int
    stage_id: str
    input_event_id: str | None
    decision: StepDecision
    operation_ref: str | None
    operation_result_ref: str | None
    state_delta: dict[str, Any]
    postcheck_result: StepPostcheckResult | None
    started_at: str
    finished_at: str | None
```

Trace may still include model calls, tool calls, alignment decisions, and run
summaries, but `StepRecord` becomes the primary progress spine.

## Agent package structure

Agent definitions should move from one big Markdown file toward a package with
separate control surfaces:

```text
agents/<agent-name>/
  agent.toml          # identity, responsibility, tools, skills, output contract
  intent.toml         # default intent fields, boundaries, success criteria
  loop.toml           # max auto steps, continuation defaults, stage policy
  brain.toml          # fast/slow settings and rule pack references
  brain.md            # slow-mode control instruction
  rules.toml          # human-authored fast rules
  stages.toml         # stage graph, entry/exit criteria, judgment gates
  skills/
    <skill>/SKILL.md
```

The exact filenames may be adjusted during implementation, but the separation
is intentional:

- `agent.toml` declares who the Agent is and what it may use;
- `intent.toml` declares the default field the Agent serves;
- `loop.toml` declares life-cycle behavior;
- `brain.toml` and `rules.toml` declare control logic;
- `brain.md` guides slow reasoning;
- `stages.toml` declares phase-level progress, not micro-tasks;
- Skill files remain reusable professional methods.

For migration, a single-file `agent.md` declaration can be mechanically
expanded into this package shape by treating its body as Agent instruction or
slow Brain guidance and using defaults for the missing files. This is an input
format adapter only; the runtime still enters through `AgentLoop` and Brain.

## Runtime flow

The revised run loop is:

```text
load Agent package
-> initialize HumanIntentContext
-> initialize AgentLoop
-> estimate IntentClarity
-> derive AutonomyScope
-> AgentLoop.resume(event)
   -> build StepContext
   -> Brain.plan_step(context)
      -> try fast rules
      -> if no safe match, use slow model planner
   -> create StepRecord(status=planned)
   -> validate BrainIntentPatch if present
   -> if StepDecision.human_judgment.required: preserve ask payload and wait/handoff
   -> if StepDecision.ask: wait
   -> if StepDecision.operation: execute through runtime operation gateway
      -> if operation is approved stage_transition, apply returned stage
   -> apply validated BrainIntentPatch if the step did not fail
   -> run postcheck
   -> update AgentState and LoopState
   -> write LoopContinuationDecision
   -> complete StepRecord
   -> continue / wait / stop according to Loop policy
-> checkpoint after every step boundary
```

Graph node mapping can start as:

```text
setup_node             -> initialize intent + loop
brain_step_node        -> Brain.plan_step + StepDecision staging
execute_tool_node      -> execute_operation_node
await_interaction_node -> loop waiting/resume
validate_output_node   -> verify/finish step handling
```

LangGraph remains the substrate, but `AgentLoop` becomes the domain model.
Graph nodes should not be the only place where Loop semantics exist.

## Relationship to existing runtime

### Keep and reuse

- `HumanIntentContext`, `IntentClarity`, `IntentStage`, and `AutonomyScope`
  remain the intent and autonomy substrate.
- `ActionProposal`, `ActionGateway`, `AlignmentKernel`, `GovernanceGate`, and
  policy gates remain the current low-level execution and proof path beneath
  runtime operations.
- Checkpoint/resume stays in LangGraph until the Loop abstraction proves stable.
- Trace recorder remains the storage backend, enriched around Step lineage.
- Existing `ToolSpec`, `ToolRegistry`, and Skill loading continue to provide
  capabilities.

### Promote

- `AgentLoop` becomes a real runtime object or persisted state family.
- `StepDecision` becomes the contract between Brain and Loop.
- `StepRecord` becomes the top-level audit object for progress.
- `RuntimeOperationProposal` becomes the Step-level operation contract above
  the current `ActionProposal` execution path.
- Brain config becomes part of Agent declaration, not an incidental prompt.

### Demote

- A raw model turn is no longer the center of control.
- A raw tool call is no longer the unit of agent progress.
- `Agent.md` is no longer the only place to encode Agent behavior.
- Task plans remain useful below stages, but they do not replace Loop/Step.

## Fast and slow planning algorithm

Brain planning should follow this order:

```text
1. Normalize StepContext.
2. Evaluate fast rules in priority order.
3. Reject fast matches that conflict, fall below confidence, or violate Loop
   and Autonomy constraints.
4. If exactly one safe fast match exists, return fast StepDecision.
5. Otherwise build the slow planner prompt from StepContext, BrainSpec,
   stage contract, recent StepRecords, and rule miss/conflict evidence.
6. Adapt and normalize slow model output into a `StepDecision` candidate.
7. Validate StepDecision shape, invariants, and allowed step kind.
8. If normalization cannot produce a safe candidate, return a slow handoff step
   with `human_judgment.required == true`.
9. Return the validated slow StepDecision to Loop.
```

Fast rules are not governance rules. They choose the next step. Alignment and
governance still run later if the step proposes an operation.

Fast rule validation must reject rules outside the first-version categories:
missing-input clarification, satisfied-stage transition, and known hard-boundary
judgment. Broader control belongs in slow mode until the runtime has evidence
that a new fast category is safe and useful.

Slow planning output must enter the Loop as structured data, but the model is
not trusted to produce that shape perfectly. The Brain adapter/normalizer owns
the recovery boundary between model output and `StepDecision`. If normalization
fails, the Loop records a slow handoff step and enters waiting with a clear
human-judgment prompt.

## Loop continuation policy

The Loop should make continuation explicit after every step.

Inputs:

- `StepDecision.continuation`;
- current `AutonomyScope`;
- `max_auto_steps`;
- step failure count;
- pending human judgment;
- stage exit criteria;
- operation and postcheck result.

Outcomes:

```python
LoopContinuation = Literal[
    "continue",
    "wait_for_user",
    "wait_for_judgment",
    "complete",
    "fail",
    "cancel",
]
```

The Loop records its final continuation decision separately from Brain's
requested continuation:

```python
LoopContinuationDecision:
    outcome: LoopContinuation
    step_id: str
    requested: "continue" | "wait" | "stop"
    basis: ContinuationBasis | None
    blockers: list[str]
    reason: str
```

Brain supplies `continuation_basis` when it requests `continue`; Loop decides
whether that basis is sufficient under autonomy scope, budgets, failures,
pending judgments, and postcheck results. This prevents automatic continuation
from being hidden in graph routing.

The Loop may continue automatically only when:

- the decision asks to continue;
- the decision carries a valid `continuation_basis`;
- `human_judgment.required` is false;
- no pending ask or judgment exists;
- the current autonomy scope allows the next step kind;
- the max automatic step budget has not been exhausted;
- postcheck did not fail critically.

## Trace and explainability

Trace should be readable at two levels:

1. Step-level progress:

```text
step_planned
step_started
step_completed | step_waiting | step_failed
```

2. Operation-level proof beneath a step:

```text
action_proposed
alignment_decision
governance_decision
tool_result
intent_lineage_recorded
```

Every operation-level event should carry `parent_step_id`. Every StepRecord
should carry `intent_version` and `stage_id`.

Validated Brain intent patches are committed at the end of the step, after any
operation succeeds. If an operation fails or is denied, the patch is not applied
unless the Loop converts the failure into an explicit waiting/handoff step and
the human later confirms the intent update. This keeps one failed operation from
silently changing the active intent field.

A maintainer should be able to answer:

- What step did the Loop take?
- Did Brain use fast or slow mode?
- Which rule or slow reasoning produced the decision?
- What intent version and stage were active?
- Did the step ask, execute, verify, hand off, or finish?
- If it executed, what action lineage proved safety?
- Why did the Loop continue, wait, or stop?
- What `LoopContinuationDecision` justified that outcome?

## First executable slice

The first slice should prove the control model before expanding the low-level
runtime operation layer.

### Slice goal

One Agent run should show:

1. `LoopState`, `StepContext`, `StepDecision`, and `StepRecord` exist as
   explicit contracts;
2. Loop initialization from task input and Agent defaults;
3. the existing model turn is wrapped as slow Brain behavior;
4. `Brain.plan_step()` produces either fast or slow `StepDecision`;
5. `StepRecord` is written with `step_kind`, `reasoning_mode`, `rule_ref`, and
   intent lineage;
6. optional runtime operation is routed through a runtime operation gateway
   adapter backed by existing `ActionGateway` behavior;
7. Loop continuation decision after the step;
8. checkpoint/resume preserves Loop state and recent StepRecords.

### Slice scope

Use a narrow validation Agent with simple known rules:

- if required input is missing, fast rule returns `clarify` with `ask`;
- if required input is present and stage exit criteria are satisfied, fast rule
  proposes a `stage_transition` runtime operation;
- if a known hard boundary is triggered, fast rule waits for human judgment;
- if no rule applies, slow mode returns a structured planning step;
- if a step proposes a runtime operation, the runtime operation gateway handles
  it through the current action runtime adapter.

### Slice non-scope

- no automatic rule learning;
- no full Agent package loading surface;
- no full Agent package migration for every example Agent;
- no replacement of all graph nodes in one patch;
- no public API stabilization of every new type.

## Migration strategy

### Phase 1: Document and type the new center

- Add `LoopState`, `LoopPolicy`, `StepContext`, `StepDecision`, and
  `StepRecord` contracts.
- Add `BrainSpec` that supports fast rule config and slow planner instruction.
- Update type reference and architecture docs.
- Keep current runtime behavior for Agents without Brain/Loop config.

### Phase 2: Add Loop as a domain layer

- Initialize Loop state during setup.
- Emit step trace around existing model/tool/validation nodes.
- Preserve current model-driven path as slow mode.
- Add checkpoint tests for Loop state and StepRecords.

### Phase 3: Add fast Brain mode

- Load fast rules from Agent package.
- Evaluate fast rules before the slow model planning path.
- Return structured `StepDecision` for known cases.
- Trace rule id, confidence, and reason.

### Phase 4: Make slow Brain structured

- Replace free-form model turn control with structured slow `StepDecision`.
- Validate slow output before operation execution.
- Normalize recoverable model output before validation, then route malformed or
  unsafe slow decisions to wait/handoff instead of tool execution.

Phase 4 first slice status:

- `SlowModelBrain` requires a `StructuredSlowPlanner` that must return
  a valid slow `StepDecision` candidate after adapter normalization.
- Invalid, unsafe, or failed slow planner output becomes a slow
  `handoff`/judgment step only after normalization cannot recover it; it cannot
  carry an operation.
- The graph-backed slow planner exposes only the `submit_step_decision`
  protocol tool to the model as the preferred path. Business tools can only be
  requested as `RuntimeOperationProposal`s and are executed by the Loop/Harness
  path. If the model calls a business tool directly, the adapter may normalize
  that call into a `RuntimeOperationProposal` instead of failing immediately.
- There is no free-form `model_turn` fallback.

### Phase 5: Split Agent declarations

- Support package-style Agent definitions.
- Treat single-file `.md` Agents as declaration input that is adapted into the
  Brain/Loop runtime, not as a separate control path.
- Move intent defaults, stages, Brain config, and fast rules into separate
  files for at least one real Agent.

Phase 5 first slice status:

- Single-file `agent.md` declarations continue to load as an input adapter.
- Package directories may now include `brain.toml`, `rules.toml`,
  `stages.toml`, `intent.toml`, and `loop.toml`; these load into
  `AgentProfile.metadata`.
- `rules.toml` is merged into `metadata["brain"]["fast_rules"]` so fast rules
  do not depend on a monolithic Markdown frontmatter block.
- Declarative `agent.toml` supports basic profile fields plus
  `instruction_file`. Factory-only `agent.toml` remains under project
  discovery.

### Phase 6: Retire old control assumptions

- Stop treating `model_turn` as the conceptual runtime center.
- Stop treating raw tool calls as progress records.
- Make Step lineage required for consequential operations.

Phase 6 first slice status:

- `ActionProposal` and `IntentLineage` now carry `parent_step_id`.
- `action_proposed` and `alignment_decision` trace payloads include
  `parent_step_id`.
- Side-effecting/runtime-control operations require Step lineage before real
  execution; there is no policy-only fallback when intent/scope is missing.
- Runtime traces are centered on `step_planned`, `runtime_operation_staged`,
  `step_completed`, and `loop_continuation_decision`. `model_call` is no longer
  the semantic progress unit.

## Testing strategy

Tests should prove control behavior, not just action execution.

Required groups:

- Loop initialization and status transitions;
- checkpoint/resume of Loop state;
- `StepContext` construction from intent, stage, event, and recent steps;
- fast rule match, priority, conflict, and miss behavior;
- fast rule rejection when a rule tries to encode a broader workflow than the
  three allowed first-version categories;
- slow fallback when fast mode cannot safely decide;
- structured slow output validation;
- slow Brain normalization when the model omits the protocol tool or proposes a
  directly recoverable business-tool call;
- `StepDecision` application of intent patches and stage transitions;
- rejection of `StepDecision.continuation == "continue"` without a valid
  `ContinuationBasis`;
- rejection or normalization of `operation + continuation == "wait"` without
  an ask or human judgment;
- rejection of Brain decisions that claim no human judgment is required without
  a valid `HumanJudgmentAssessment.reason`;
- rejection of Brain decisions that combine
  `human_judgment.required == true` with an operation, with no runtime
  operation executed;
- operation execution through the runtime operation gateway adapter;
- rejection of Brain-authored stage fields or unknown keys inside
  `BrainIntentPatch`;
- `LoopContinuationDecision` recording requested continuation, blockers, basis,
  and final outcome;
- parent step id on action lineage events;
- max automatic step budget behavior;
- waiting and resume after ask/judgment;
- CLI/API surfacing of `pending_judgment` as an interactive judgment pause,
  rather than silently exiting after interruption;
- single-file Agent declarations without Brain config still run through the
  structured slow Brain path.

Scenario tests should include:

- simple task solved through fast rules without a model planning turn;
- ambiguous task enters slow mode and asks or plans safely;
- failed step causes slow replanning rather than repeating the same fast rule;
- human correction updates intent and resumes the same Loop.

## Acceptance criteria

The redesign is successful when a maintainer can inspect a run and answer:

- What Loop owned this intent?
- What step was taken next and why?
- Did Brain use a fast rule or slow model reasoning?
- If fast, which human-authored rule fired?
- If slow, what structured decision did the model return?
- What intent version and stage shaped the decision?
- Did the step execute a runtime operation, ask for input, verify, hand off, or
  finish?
- If it executed a runtime operation, which operation variant and action
  lineage proved safety?
- Why did the Loop continue, wait, complete, or fail?
- What `LoopContinuationDecision` justified that outcome?

If the only explanation is "the model emitted a tool call," the redesign has
not achieved its goal.

## Open decisions

### D1: Persistence shape

`AgentLoop` may be implemented first as fields inside `MainGraphState`, then
promoted to a dedicated runtime object once the contract stabilizes. The first
implementation should not require a separate database.

### D2: Rule grammar

Fast rule conditions should start with a small declarative grammar over intent,
stage, task input, known facts, recent step outcomes, and available
capabilities. Avoid arbitrary Python predicates in user-authored Agent
packages.

### D3: Slow planner model contract

Slow mode needs a structured-output contract for `StepDecision`. The first
version can use the existing model adapter and parser patterns, but malformed
output must never fall through to action execution.

### D4: Legacy `agent.md`

Legacy `agent.md` should remain loadable during migration. The loader should
adapt it into the new package model with default Loop policy and slow-only
Brain behavior.
