# Single Brain and Mandatory Workflow Hard-Cut Design

**Status:** approved for specification review

**Date:** 2026-07-12

## Decision

Modi Harness will remove the fast/slow Brain split, stage control, standalone
AgentLoop execution, and every compatibility surface that preserves those
concepts.

The runtime will have one control model:

```text
Agent (must declare at least one Workflow)
  -> WorkflowRuntime
       |- operation Node
       |    -> RuntimeOperationAdapter
       |
       `- autonomous Node
            -> AgentLoop
                 -> Brain.plan_step(context)
                 -> StepDecision
```

Deterministic business control belongs to Workflow operation Nodes. Open-ended
problem solving belongs to an autonomous Node whose AgentLoop asks one Brain
for the next semantic StepDecision.

There is no mode selection, rules fallback, stage graph, implicit lifecycle,
or legacy execution entry point.

## First principles

### One fact must have one owner

- WorkflowRuntime owns the current business Node.
- AgentLoop owns progress inside the active autonomous Node.
- Brain owns the next semantic decision.
- RuntimeOperationAdapter owns translation into the existing execution gateway.
- WorkflowRuntime is the only state-machine writer: it selects and commits
  declared transitions.
- Harness services provide authority, execution, validation, persistence, and
  transactional commit primitives; they never independently choose a
  transition.

Stage, rule mode, planner mode, and standalone loop state duplicate those
owners. They are removed instead of synchronized.

### Determinism is configuration, not simulated reasoning

If the system already knows which action comes next, the action is an
`operation` Node and its outcome selects a declared transition. A code rule
that repeatedly emits StepDecision objects is an implicit Workflow and is not a
second planning mode.

### Autonomy is local

Brain may plan, revise tasks, ask for input, and choose Operations only inside
the active autonomous Node. It cannot choose a Workflow, modify Workflow
topology, change the Node goal, or commit Node completion.

### Failure is not a mode

Planner failure is an internal failure. It is recorded and follows the active
Node's declared failure path. The runtime does not switch planner categories,
manufacture a generic human prompt, or ask the user to operate the control
plane.

### No compatibility is simpler than hidden compatibility

Old config files, field names, imports, aliases, automatic wrapping, warnings,
and fallback behavior are rejected or deleted. The new runtime never attempts
to infer an old Agent's intended Workflow.

## Considered approaches

### A. Rename the two modes but preserve both paths

This minimizes immediate edits but retains the same duplicated control model.
Rules still encode implicit Workflows and planner fallback still changes
semantics. Rejected.

### B. Remove Brain and call the model directly from AgentLoop

This removes one interface but couples Loop semantics to provider adaptation
and makes deterministic testing harder. Brain remains a useful boundary between
semantic choice and mechanical control. Rejected.

### C. One Brain inside mandatory Workflow — selected

This retains the smallest useful semantic interface and places all stable
business control in Workflow. The implementation may land in several internal
commits, but the completed change exposes only the hard-cut architecture.

## Final object and package model

### Agent

Every Agent must resolve to:

```text
Agent
  ├── identity and instruction
  ├── permissions and tools
  ├── intent defaults
  ├── loop limits
  ├── workflows: Workflow[1..n]
  └── skills
```

An Agent with zero Workflows fails loading. The runtime does not create an
implicit one-Node Workflow and does not enter standalone AgentLoop.

Validation has three deliberately separated boundaries:

```text
source construction
  -> normalize raw Agent and Workflow definitions
  -> validate local closed schemas, reserved files, and non-empty Workflows

Session construction: resolve_agent_graph
  -> recursively validate every filesystem/direct/plugin/factory/subagent
  -> resolve tools, adapters, validators, and capability upper bounds
  -> recompute authoritative Workflow definition fingerprints
  -> produce immutable ResolvedAgent graph

run creation after Workflow selection
  -> bind selected Workflow, effective runtime limits, OutputContract,
     capabilities, adapter/validator versions, and protocol versions
  -> compute and persist the selected execution-contract snapshot/fingerprint
```

`ModiAgent` construction performs only source-local structural/non-empty
validation because it has no Session registries or run limits. Session
construction is the authoritative dependency-resolution boundary. A plugin or
factory cannot bypass it by returning a preconstructed object or claimed
fingerprint. A multi-Workflow Agent has one authoritative definition
fingerprint per Workflow; it receives an execution-contract fingerprint only
after one Workflow is selected for a run.

### Agent package

The only supported package shape is:

```text
agents/<agent-name>/
  agent.toml
  intent.toml
  loop.toml
  workflows/
    <workflow-id>.yaml
  skills/
    <skill>/SKILL.md
```

Declarative `agent.toml` requires an inline, non-empty `instruction`; the
`instruction_file` field and Markdown fallback are deleted.

A factory manifest has exactly one control field:

```toml
factory = "runtime:build_agent"
```

It does not merge declarative identity, instruction, permissions, or Workflows
from the manifest. The factory returns a complete raw Agent definition, which
then enters `resolve_agent_graph` like every other source. Factory-provided
canonical objects and fingerprints are discarded and recomputed.

The loader no longer supports `agent.md` as an Agent declaration and no longer
loads control metadata from arbitrary companion files.

The following files have no replacement and are deleted:

```text
brain.toml
brain.md
rules.toml
stages.toml
agent.md
```

Their presence in an Agent package is an unconditional package-validation
error. Unrelated files are ignored. There is no compatibility warning,
instruction fallback, or metadata merge.

Professional methods belong in Skills. Stable control belongs in Workflows.
Agent identity and general instruction belong in `agent.toml`.

### Workflow and Node

The approved Workflow object model remains:

```text
Workflow
  ├── id
  ├── input_schema
  ├── start_node
  └── nodes: Node[]

Node
  ├── id
  ├── execution: operation | autonomous
  ├── completion
  └── transitions
```

No Edge, Stage, Fallback, ReasoningNode, HumanNode, or DynamicSubgraph object is
introduced.

### Brain

There is one Brain protocol:

```python
class Brain(Protocol):
    def plan_step(self, context: StepContext) -> StepDecision: ...
```

The default implementation adapts one StructuredPlanner. It may normalize a
recoverable provider response into StepDecision, but it has no alternate rule
path, mode, or recovery Brain.

Tests may use a static Brain implementation. That is dependency injection, not
a runtime Brain category.

## Contracts after deletion

### StepDecision

StepDecision contains only fields required to express the next semantic step:

```text
id
step_kind
reason
intent_patch
ask
operation
expected_state_change
postcheck
continuation
human_judgment
continuation_basis
```

`step_kind` does not include `finish`. Brain cannot request terminal success.
Brain continuation is only `continue` or `wait`; proposal-level `stop` is
deleted. Loop terminal outcomes remain internal WorkflowRuntime decisions, not
Brain-authored continuation values.

The following fields are deleted:

```text
reasoning_mode
rule_ref
```

Brain source is no longer a semantic property of a Step. Trace already records
the Brain implementation and model call when operational diagnostics need it.

### Human judgment

Supported triggers are limited to real human boundaries:

```text
none
boundary
autonomy_scope
operation_risk
```

The following triggers are deleted:

```text
stage_gate
failure_recovery
```

Missing input uses a structured ask, not HumanJudgmentAssessment. Human
judgment is reserved for a real responsibility, authority, risk, or
intent-boundary decision.

### Continuation basis

Continuation source is reduced to evidence that remains meaningful inside an
autonomous Node:

```text
task_plan
postcheck_result
autonomy_budget
planner
```

Rule and stage sources are deleted.

### Loop state and Step records

WorkflowState owns the active Node. Embedded LoopState and StepRecord therefore
do not contain a stage identifier.

Loop state contains only run/loop identity, status, step index, budget,
continuation, current event, and pending Step identity. StepRecord contains
Step lineage, decision, operation/result references, state delta, postcheck,
and timestamps.

The following fields are deleted everywhere:

```text
stage_id
reasoning_mode
rule_ref
```

AgentLoop exists only with Workflow scope:

```text
workflow_run_id + workflow_id + node_id + node_attempt
```

Construction without that scope is an error.

### Runtime operations

The active RuntimeOperation kinds are:

```text
tool
memory_write
workflow_control
```

`stage_transition` is deleted. Standalone `output_finalize` is deleted from
Brain proposals; WorkflowRuntime invokes Output Controller only when a
validated transition targets `$complete`.

Removed terminal vocabulary is rejected by the new closed contracts rather
than handled at runtime:

```text
step_kind = finish
continuation = stop
RuntimeOperation.kind = output_finalize
```

`complete_node` is the only first-version Workflow control Operation and is
available only inside an active autonomous Node.

RuntimeOperationAdapter registration is closed and versioned. Each adapter
declares its stable ID, kind, target, input/output schema versions, side-effect
classification, recovery mode, and whether a Workflow author may select it.
Operation Nodes may select only adapters with `node_selectable = true`.
`workflow_control`, `complete_node`, and terminal output-control adapters are
internal protocol facilities and fail static Workflow validation when named by
an Operation Node.

The first-version recovery modes are:

```text
pure
provider_idempotent
gateway_claimed
manual_reconciliation
```

`manual_reconciliation` for a side-effecting Operation forces a single
provider dispatch. The trusted invocation context overrides any ToolSpec retry
configuration: timeout or an uncertain result enters reconciliation without a
second attempt, including while a timed-out handler may still be running.
Automatic retry is permitted only for `pure`, `provider_idempotent`, or a
durably claimed `gateway_claimed` invocation.

## Runtime data flow

### Run creation

```text
load Agent package
  -> require at least one validated Workflow
  -> select explicit workflow_id, or the sole Workflow
  -> validate Workflow input
  -> pin execution contract
  -> create WorkflowState
  -> enter start Node
```

Multiple Workflows without an explicit ID fail with `workflow_required`.
Zero Workflows fail Agent loading, not run routing.

`workflow_id` is a control-envelope field, never part of domain input. It is
carried consistently by:

- `ModiSession.run_task`, `stream`, and `astream`;
- sync/async Harness adapter request types;
- CLI `--workflow` and API request control fields;
- subagent delegation requests as `target_workflow_id`.

The selected ID and definition/execution fingerprints are pinned in
WorkflowState. Resume APIs do not accept a replacement Workflow ID; they use
the pinned selection and reject mismatched events. Delegation to an Agent with
multiple Workflows requires `target_workflow_id`; delegation to a sole-Workflow
Agent may default it exactly like a top-level run.

The execution-contract snapshot is the actual run contract, not merely a copy
of Workflow YAML. Its canonical fingerprint covers:

- the canonical selected Workflow definition;
- every reachable adapter ID, kind, target, schema version, implementation
  version, side-effect class, recovery mode, and visibility;
- every reachable completion-validator ID and implementation version;
- the Agent OutputContract and embedded-loop protocol version;
- the effective capability upper bound after Agent and Node narrowing;
- fixed run limits, including step and transition budgets.

Resume recomputes this contract from authoritative registries and refuses to
continue on any mismatch. Current policy, authority, or capability may become
stricter after a run starts, but effective authority is always the intersection
of the pinned capability snapshot and the current authority; it can never
expand beyond the run-creation snapshot.

### Operation Node

```text
enter Node
  -> resolve input snapshot
  -> select trusted Node.operation adapter
  -> execute through existing alignment/action/policy path
  -> validate Node completion
  -> commit declared outcome transition
```

An Operation may internally use code, a tool, a model-backed structured
operation, human interaction, or output validation. WorkflowRuntime observes
only its normalized result.

### Autonomous Node

```text
enter Node
  -> create scoped AgentLoop and TaskPlan protocol
  -> build StepContext from Node goal/input/completion/capabilities
  -> Brain.plan_step(context)
  -> validate StepDecision
  -> execute at most one RuntimeOperation
  -> record StepRecord
  -> continue, wait, fail, or propose complete_node
```

Brain cannot emit a terminal finish Step. It must propose `complete_node` with
a committed result artifact. Harness decides whether the Node is complete.

### Workflow completion

Only a declared transition to `$complete` can complete a run. Output Controller
validates or opens a candidate-bound review before WorkflowRuntime atomically
commits the terminal output and status.

There is no AgentLoop finalization path outside WorkflowRuntime.

Terminal output mapping is closed:

| Output Controller result | WorkflowRuntime action |
| --- | --- |
| `validated` or `final` | atomically commit Node result, `$complete` transition, final output, revision, and Workflow completed status |
| `needs_review` | persist a judgment bound to Workflow revision, Node attempt, candidate hash, and output-contract hash; wait without committing the transition |
| ordinary contract rejection from autonomous Node | return completion feedback to the same AgentLoop within its remaining budget |
| ordinary contract rejection from operation Node | record the successful Operation result, emit Node `failed`, and never re-execute that Operation |
| integrity/security rejection or unknown error code | fail Workflow directly |

Review approval revalidates the exact bound candidate and performs the same
atomic terminal commit. Review rejection follows the execution-specific
ordinary rejection path. A changed candidate/hash or stale approval is an
integrity failure. Checkpoint recovery must expose either the state before the
terminal transaction or the fully committed terminal state, never an applied
transition without terminal output/status.

## Failure semantics

### Brain failure

Planner exception, normalization failure, or invalid StepDecision produces:

```text
brain_planning_failed
  -> fail active AgentLoop and Node attempt
  -> emit Node failed
  -> follow transitions.failed, or fail Workflow when it is absent
```

It never becomes a user correction prompt or a fallback planning mode.

Failure classification is closed:

- Brain owns provider invocation, response parsing, normalization, and
  structural StepDecision schema validation. Failure in those phases becomes
  `brain_planning_failed` and emits Node `failed`.
- AgentLoop validates embedded-loop invariants, allowed Step kind, single
  Operation shape, current Node capability, and immutable Node scope.
- WorkflowRuntime validates Workflow run identity, current attempt, revision,
  and transition authority.
- A structurally valid decision that forges scope, targets a stale attempt,
  exceeds authority/capability, or violates another integrity boundary fails
  Workflow directly as `brain_decision_integrity_error`; it never follows a
  business failure transition.

### Operation failure

Operation denial or normalized failure follows the Node's declared `failed`
transition. Runtime integrity errors terminate directly and cannot be hidden by
a business failure path.

### Operation dispatch and external effects

Every side-effecting invocation has a durable state:

```text
prepared -> dispatching -> terminal
                    `----> reconciliation_required
```

WorkflowRuntime first persists `prepared`. A dispatcher must atomically claim
it as `dispatching` with the expected Workflow revision and confirm that the
run is still non-terminal before calling the provider. Only the claim owner may
dispatch. Provider success or definite failure becomes `terminal`; timeout,
crash, or any outcome whose external effect is unknown becomes
`reconciliation_required` according to the adapter recovery mode.

Cancellation may atomically cancel a `prepared` invocation. It cannot report a
terminal cancelled Workflow while an invocation is `dispatching`; it records
the cancellation request and waits for a definite terminal result or enters
reconciliation. This prevents an already-prepared call from producing a new
external effect after the Workflow has reported cancellation.

### Missing input

Missing information produces a field-scoped ask and waits on the same Node.
The answer updates active intent/human context and resumes the same attempt.

### Budget exhaustion

Autonomous step exhaustion fails the active Node. Workflow transition
exhaustion fails the Workflow. Neither asks the user to choose an internal next
step.

### Human judgment

Human judgment is created only for a real boundary, authority, or risk
decision. Approval, rejection, revision, constraint, clarification, and
cancellation update the active run through the existing intent/governance
path.

## Intent without stages

Human Intent Context remains first-class:

```text
goal
desired outcome
boundaries and non-goals
success criteria
responsibility
tradeoffs
human decisions and corrections
```

Intent no longer contains current stage. Workflow current Node supplies
lifecycle position, while intent supplies human purpose and authority.

`IntentStage`, stage defaults, stage exit criteria, stage gates, and stage
transition mutations are deleted.

## Deletion inventory

### Runtime source

Delete or replace:

- `src/modi_harness/brain/rules.py`;
- `src/modi_harness/brain/slow.py`;
- rule-provider and mode exports from `brain/__init__.py`;
- mode-specific comments and types in `brain/types.py`;
- mode and stage fields from `loop/types.py` and `loop/runtime.py`;
- mode fallback, stage transition, and failure-recovery branches in graph
  nodes;
- stage data and mutation support in intent types/extraction;
- package loading for `brain.toml`, `rules.toml`, and `stages.toml`;
- single-file `agent.md` declaration loading;
- standalone AgentLoop initialization and finalization;
- standalone `output_finalize` operation routing.

The replacement Brain implementation lives in a neutrally named module such as
`brain/default.py`; old module paths are not re-exported.

### Agent and example packages

Every current Agent source is migrated or deleted in the same hard cut:

| Source category | Current instances | Required action |
| --- | --- | --- |
| Root Agent packages | `agents/research_assistant`, `agents/modi-webagent` | migrate each retained Agent/subagent to `agent.toml` plus explicit Workflows; delete old declarations/control files |
| Shipped examples | support triage, code auditor, research assistant examples | migrate retained examples to package directories and explicit Workflows; delete duplicate Markdown declarations |
| Plugin fixtures | sample plugin Agent and any plugin-contributed test Agents | add canonical raw Workflow definitions and revalidate through `resolve_agent_graph` |
| Discovery factories | registry/factory fixtures and project Agent factories | return complete raw Agent definitions with Workflows; remove trusted-profile/fingerprint shortcuts |
| Programmatic Agents | API/session/graph/subagent test constructors | use one shared minimal explicit Workflow fixture; tests that intentionally omit it assert validation failure |
| Nested subagents | support-triage children and recursive API/session fixtures | give every nested Agent its own Workflow and validate recursively before parent Session construction |
| Removed/duplicate Agents | obsolete single-file copies and redundant fixtures | delete rather than wrap or retain as compatibility samples |

Repository discovery tests must enumerate all filesystem, plugin, factory,
direct, and nested Agent sources and prove that no resolved Agent graph contains
an Agent with zero Workflows.

Delete from migrated packages:

- `brain.toml`;
- `brain.md`;
- `rules.toml`;
- `stages.toml`;
- `brain_rules.py`;
- factory registration of rule providers;
- frontmatter blocks or metadata for old Brain modes;
- prose instructing a specific planner category.

Move stable lifecycle decisions into Workflow operation Nodes. Keep research
methods in Skills and general behavior in `agent.toml` instruction.

### Tests

Delete mode/rule/stage compatibility tests. Rewrite useful behavioral tests to
assert the single path:

- Brain returns a StepDecision;
- invalid planning fails the active Node;
- Workflow operation transitions are deterministic;
- autonomous planning stays within Node scope;
- real interaction/judgment resumes the same attempt;
- no standalone path exists.

Test fixtures no longer manufacture mode fields or old triggers.

### Documentation

Delete obsolete design and plan documents whose proposed architecture depends
on two Brain modes or stage control, including the old Brain-Agent Loop design,
its implementation plan, and the research-assistant planner-mode design.

Update README, type reference, architecture pages, guides, examples, and the
approved Workflow design so they describe only mandatory Workflow and one
Brain. Historical Git commits remain the history; live documentation does not
preserve obsolete concepts.

## Implementation strategy

The hard cut may use multiple internal commits so each change is reviewable,
but no compatibility layer survives the final change.

### Cut 1: Build the complete new runtime vertically

- finish WorkflowRuntime operation/autonomous execution, state, wait/resume,
  transition, terminal output, and checkpoint contracts before changing public
  routing;
- implement the one Brain only inside Workflow-scoped autonomous execution;
- implement source-local validation, Session `resolve_agent_graph`, and
  run-creation execution-contract pinning at their separate boundaries;
- verify the new runtime directly with isolated Agents that already have
  explicit Workflows.

The old public path may still exist in the worktree during this internal cut,
but no feature flag or compatibility adapter is added and no release is made.

### Cut 2: Migrate every Agent source

- migrate both root Agent packages, all retained shipped examples, plugin and
  factory fixtures, programmatic test Agents, and every nested subagent using
  the inventory above;
- express research assistant's stable lifecycle as operation/autonomous Nodes;
- delete duplicate Agent declarations as each source is migrated;
- run every migrated Agent against the new runtime directly.

### Cut 3: Perform the atomic hard switch

In one integration change:

- route run/stream/async/CLI/API/delegation through mandatory WorkflowRuntime;
- require `resolve_agent_graph` before Session construction;
- make AgentLoop construction require Workflow Node scope;
- replace the mode stack with the one Brain and remove mode/rule fields;
- delete standalone graph routing, terminal vocabulary, and finalization;
- delete stage types, fields, operations, prompts, and policies;
- delete old package discovery/parsing and reserved control files;
- remove every compatibility alias, fallback, and old import.
- delete or rewrite every behavior-dependent compatibility test and fixture
  before running the integration suite;

The integration test suite must be green at this boundary. There is no state
where mandatory Workflow is enabled before Agent migration, or where Brain
failure expects a Node without Workflow scope.

### Cut 4: Delete obsolete documentation and non-runtime debris

- remove superseded specs/plans and any remaining non-runtime dead assets;
- update remaining tests and live documentation;
- run banned-concept searches and the full non-live suite.

These cuts are implementation sequencing only. There is no supported or
released state between them and no feature flag selects the old runtime.

## Minimum viable usability

The hard cut is usable when one Agent can:

1. load from `agent.toml` with at least one Workflow;
2. select the sole Workflow without an explicit ID;
3. validate input and enter the start Node;
4. execute an operation Node through existing policy/action controls;
5. execute an autonomous Node through AgentLoop and the one Brain;
6. ask for missing input and resume;
7. propose and validate `complete_node`;
8. commit a declared transition and final output;
9. checkpoint/resume at durable boundaries;
10. fail deterministically without a planner-mode fallback.

The research assistant is the first end-to-end proof.

## Testing and verification

### Contract tests

- StepDecision rejects removed fields;
- human judgment rejects removed triggers;
- LoopState/StepRecord contain no stage or mode fields;
- RuntimeOperation rejects stage transition and standalone finalization kinds;
- StepDecision rejects `step_kind=finish` and `continuation=stop`;
- Agent validation requires at least one Workflow.

### Runtime tests

- exactly one Brain implementation is invoked per autonomous Step;
- Planner provider/parse/normalization/schema errors produce
  `brain_planning_failed`, emit Node `failed`, and follow its declared
  transition; decision integrity errors fail Workflow directly;
- deterministic operation routing never calls Brain;
- autonomous Brain cannot change Workflow or Node scope;
- zero-Workflow and standalone AgentLoop construction fail;
- `$complete` remains the only terminal-success path.
- provider/parse/normalization/schema planning failures emit Node `failed`,
  while scope/stale/authority integrity violations fail Workflow directly;
- autonomous ordinary output rejection returns completion feedback;
- operation ordinary output rejection follows `failed` without re-execution;
- integrity output rejection fails directly;
- candidate-bound review and terminal commit recover atomically across crash.

### Package tests

- only the new package shape loads;
- presence of a reserved obsolete control filename fails package validation;
- `agent.md`-only Agents fail discovery;
- research assistant loads without old metadata.
- discovery enumerates filesystem, plugin, factory, direct, programmatic, and
  recursively nested Agents and proves every resolved Agent has a Workflow;
- direct/factory/plugin claims of prevalidated Workflow objects or fingerprints
  are discarded and recomputed by `resolve_agent_graph`.

### Workflow control-path tests

- run, stream, async stream, CLI, and API pass `workflow_id` outside domain
  input;
- sole Workflow defaults; multiple without ID fails; explicit unknown ID
  fails;
- resume uses the pinned Workflow and cannot replace it;
- subagent delegation defaults a sole target Workflow and requires an explicit
  target for a multi-Workflow Agent;
- all control paths produce the same selected Workflow/fingerprint state.

Because this is a hard cut, the preferred behavior for an old declaration is a
clear load error, not silent ignore. Package validation should reject reserved
obsolete filenames when present so dead configuration cannot mislead authors.

### Repository checks

Live source, Agent packages, examples, tests, README, references, architecture,
and guides must contain no active use of the removed types, fields, files, or
runtime branches. The only permitted references are this decision record and a
concise changelog entry explaining the breaking removal.

Run:

- focused Brain/Loop/Workflow tests after each cut;
- full non-live pytest suite after research assistant migration;
- Ruff and mypy on all changed packages;
- repository searches for removed imports, fields, triggers, config names, and
  compatibility aliases.

## Acceptance criteria

The hard cut is complete when:

1. every Agent has at least one explicit Workflow;
2. AgentLoop cannot run outside an autonomous Node;
3. one Brain path produces every autonomous StepDecision;
4. deterministic control exists only as Workflow operation Nodes;
5. stage is absent from intent, loop, Step, operation, trace, and config;
6. removed fields and files fail validation rather than being adapted;
7. planner provider/parse/normalization/schema failure emits Node `failed` and
   uses its declared transition without mode fallback or generic human
   recovery; decision integrity failures terminate Workflow directly;
8. Workflow `$complete` is the only successful run-finalization path;
9. research assistant passes an end-to-end Workflow run;
10. no deprecated aliases, flags, loaders, imports, or compatibility tests
    remain;
11. current documentation describes only the new architecture;
12. focused, full, lint, type, and banned-concept checks pass.
