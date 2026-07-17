# Long-Running Task Runtime Design

## Decision

Modi Harness will add a first-class long-running task execution layer while
preserving mandatory Workflow as the stable outer control plane.

The design introduces three structural abstractions:

1. **Workflow** defines stable lifecycle stages, authority boundaries, human
   gates, and terminal transitions.
2. **Task Graph** defines the dynamic work needed for one confirmed intent,
   including dependencies, priorities, completion contracts, controlled
   replanning, and scheduling policy.
3. **Child Flow** defines the statically versioned execution protocol used by
   one child Agent to attempt one Task.

Scheduling and execution are orthogonal to those structures:

- serial, parallel, and mixed execution are Task Graph scheduling strategies;
- parent-inline, deterministic Operation, child Agent, and human work are Task
  executor modes;
- the parent Workflow run owns the Intent Contract and Task Graph;
- child runs own only their isolated execution state and may never mutate the
  parent graph directly.

The root of a long-running task is a versioned, human-confirmed **Intent
Contract**, not a generated plan. A Task Graph is an execution projection of
that contract. Every required Task must support at least one success criterion,
and the Workflow can complete only after an independent Goal Verifier confirms
that all required success criteria are satisfied.

This design deliberately reverses the V1 hard-cut decision that removed
nested/subagent execution. It does not restore the historical compatibility
runtime. It defines a new child-run model under mandatory Workflow, pinned
execution contracts, deterministic scheduling, scoped authority, and durable
checkpoint semantics.

## Problem

The current Harness already has several important foundations:

- immutable Workflow definitions and versioned execution contracts;
- durable `WorkflowState`, `StepRecord`, and `InvocationRecord` snapshots;
- operation-level completion validation and reconciliation;
- waiting interactions and checkpoint resume;
- an AgentLoop that can recover from definite tool failures within a bounded
  autonomous Node.

However, it does not yet provide a general long-running task system:

- `TaskPlan` is a flat list containing only ID, title, status, and summary;
- only one Task can be current;
- Task dependencies and graph-level readiness do not exist;
- Task completion criteria are not first-class persisted contracts;
- `StepPostcheck` is defined but is not an authoritative completion mechanism;
- the current runtime has no child Agent dispatcher or child Workflow runs;
- independent Tasks cannot execute concurrently;
- parent and child contexts cannot be isolated because no child context exists;
- retries overwrite the conceptual current attempt instead of preserving a
  complete attempt history;
- dynamic replanning is either absent or prompt-driven rather than an atomic,
  validated graph transition;
- completing every Task does not independently prove the user's goal was met.

Long-running execution must therefore become a durable feedback system:

```text
Intent
  -> Clarify
  -> Human Confirm
  -> Plan a minimal Task Graph
  -> Schedule ready Tasks
  -> Execute Attempts
  -> Verify Task candidates
  -> Update durable state
  -> Replan locally when required
  -> Verify the Goal
  -> Complete
```

## Scope

### V1 includes

- single-machine, single-process scheduling;
- one long-running goal per root Workflow run;
- a versioned Intent Contract with human clarification and confirmation;
- a first-class dynamic Task Graph attached to a Workflow Node;
- rolling-wave planning using expandable Tasks and incremental graph patches;
- deterministic serial, parallel, and mixed DAG scheduling;
- parent-inline, Operation, child Agent, and human executor modes;
- statically declared child Agent templates with dynamic task inputs;
- isolated child Workflow runs with independent state and context;
- `all_required` and `any_success` join policies;
- bounded retries, child resume, executor switching, replanning, and HITL;
- typed artifact, evidence, and context references over the existing Workspace;
- task leases and fencing tokens for late-result rejection;
- checkpoint recovery of the parent graph and all child run references;
- Research Assistant, software implementation, redundant execution, failure
  recovery, and intent-drift acceptance scenarios.

### V1 does not include

- distributed or multi-machine scheduling;
- durable wall-clock wakeups across days;
- a remote Worker pool service;
- recursive child spawning by child Agents;
- arbitrary executable join code;
- shared mutable memory between children;
- runtime creation of new Agent authority templates;
- full event sourcing or mandatory replay from an empty database;
- Task Graph reuse across unrelated goals;
- cross-root scheduling fairness;
- speculative execution beyond explicit `any_success` groups.

The snapshot/checkpoint model remains authoritative. Append-only transition
events provide auditability but are not the only source from which current
state must be reconstructed.

## Conceptual Model

```text
WorkflowDefinition                 WorkflowRun
  stable stages                      current node
  authority gates                    intent reference
  schemas                            active graph reference
         |                                  |
         v                                  v
Intent Contract -----------------> TaskGraphRun
  goal                                graph revision
  desired outcome                     dynamic TaskRuns
  success criteria                    joins / policies
  constraints                         criterion coverage
         |                                  |
         +--------------+-------------------+
                        v
                    TaskRun
                      goal
                      dependencies
                      completion contract
                      executor policy
                      failure policy
                        |
                        v
                    TaskAttempt
                      immutable input binding
                      executor identity
                      lease / fencing token
                      ChildRun or Invocation
                        |
                        v
                 Candidate Result
                        |
                        v
                  Parent Verifier
                        |
                        v
             Task / Graph state transition
```

Definitions are immutable and versioned. Runs are mutable only through
validated state transitions. Derived states such as `ready`, progress
percentage, and scheduler queue position are not persisted as authoritative
facts.

## Responsibility Boundaries

### Workflow

Workflow remains the stable outer control plane. It owns:

- lifecycle stages;
- human review gates;
- pinned authority and capability ceilings;
- stable transitions and terminal states;
- entry into and exit from dynamic Task Graph execution;
- final publication, approval, or other business-specific stages.

Workflow does not enumerate every runtime Task and does not mutate itself when
new work is discovered.

### Intent Contract

The Intent Contract is the root semantic contract. It owns:

- the confirmed goal and desired outcome;
- required and optional success criteria;
- constraints and non-goals;
- priorities and explicit tradeoffs;
- assumptions and unresolved questions;
- authority boundaries and budgets;
- version history and confirmation state.

The Intent Contract does not schedule work. It constrains graph generation,
graph patches, completion verification, and requests for human judgment.

### Task Graph

The Task Graph is the dynamic work plane. It owns:

- Tasks and dependency edges;
- priorities, requiredness, groups, and joins;
- Task completion contracts;
- Task executor and failure policies;
- graph revision and replan history;
- criterion coverage;
- graph-level budgets and completion state.

The graph cannot expand authority or bypass Workflow gates.

### Scheduler

The Scheduler is the execution control plane. It owns only short-lived control
state:

- readiness calculation;
- concurrency slots and per-template limits;
- leases and fencing tokens;
- resource locks;
- retry timers;
- handles to active child runs.

It does not own Task results or decide whether a candidate satisfies a Task.

### Parent Runtime

The parent Runtime is the only writer of global graph state. It:

- validates and commits graph patches;
- creates Task Attempts;
- starts executors;
- validates candidate results;
- records Task completion or failure;
- processes discovered-work suggestions;
- invokes the Goal Verifier;
- requests human judgment when required.

### Child Agent and Child Flow

A child Agent executes one Task Attempt under a static template and Child Flow.
It may:

- use its scoped tools;
- persist its own steps, invocations, messages, and artifacts;
- resume its own waiting or repairable run;
- return a structured candidate result;
- suggest discovered work.

It may not:

- modify the parent Task Graph;
- see the complete parent conversation by default;
- inspect unrelated Tasks or child histories;
- expand its authority;
- mark the parent Task completed;
- spawn further children in V1.

### Verifiers

Task and Goal Verifiers are deterministic or statically registered semantic
validators. They own completion decisions. Agents propose candidates; they do
not self-certify completion.

## Workflow Integration

### Task Graph Node

Workflow adds a first-class Node execution mode. Its complete immutable
definition is:

```yaml
- id: execute_goal
  execution: task_graph
  inputs:
    intent: { $ref: "#/nodes/confirm_intent/output" }
  planner: rolling-wave-planner-v1
  graph_policy: long_task_v1
  context_builder: isolated-context-v1
  task_validators: [schema-v1, artifact-integrity-v1]
  group_validators: [all-required-v1, any-success-v1]
  criterion_validators: [login-acceptance-v1]
  goal_verifier: intent-criteria-v1
  operation_adapters: [build-v1, test-v1]
  parent_inline_components: [synthesis-v1]
  human_task_contracts: [human-review-v1]
  child_templates:
    - research-worker
    - code-worker
    - browser-worker
  limits:
    max_tasks: 50
    max_graph_depth: 6
    max_replans: 10
    max_concurrency: 4
    max_child_runs: 20
  completion:
    output_schema_id: task-graph-result-v1
    validator: task-graph-node-result-v1
    require: [goal_verified]
  transitions:
    completed: $complete
    waiting: $wait
    failed: $fail
```

This Node is not an unbounded autonomous loop. It hosts a deterministic Task
Graph scheduler, invokes the Parent Planner only at declared semantic decision
points, and persists every graph transition.

`task_graph` rejects every field not shown above or in the existing common Node
schema. The planner, graph policy, Context Builder, validators, Goal Verifier,
Operation adapters, child templates, limits, schemas, and transition targets
are closed registry references resolved before the root run starts.

The parent execution contract embeds canonical snapshots and fingerprints for:

- the Task Graph Node definition and output schema;
- the Parent Planner, graph policy, Context Builder, Goal Verifier, every
  allowed Task/Group/Criterion validator, every Operation adapter,
  parent-inline component, and HumanTaskContract;
- every child template's Agent definition, child Workflow definition, output
  contract, capability ceiling, permission profile, and runtime limits;
- the complete child execution-contract snapshot and fingerprint produced from
  those pinned definitions;
- the Task Graph protocol and checkpoint-layout versions.

Dynamic Tasks may select only from these pinned IDs. Resume uses the embedded
snapshots, never newly resolved registry definitions. A changed dependency
therefore changes the root execution-contract fingerprint and cannot silently
alter an existing run.

Every referenced registry entry is represented by a `PinnedComponent` snapshot:

```yaml
id: intent-criteria-v1
version: 1
protocol_version: verifier-v1
implementation_digest: sha256:...
configuration: ...
input_schema_id: goal-verification-input-v1
output_schema_id: goal-verification-result-v1
error_codes: [repairable_gap, ambiguous, impossible]
idempotency_key: "root/{root_run_id}/graph/{graph_revision}/input/{input_hash}"
```

The registry must implement `resolve_pinned(snapshot)`, which loads the exact
implementation digest and protocol version. Resolving by current ID/version is
not valid during resume. If the digest is unavailable or its protocol/schema
does not match, the run enters `reconciliation_required` and cannot guess a
replacement; migration requires an explicit new execution contract.

Planner and verifier components use closed callable protocols. A Planner takes
the parent context projection plus a trigger and returns a bounded GraphSeed or
GraphPatch proposal, a reason, and discovered-work suggestions. A Task, Group,
or Criterion Verifier takes its frozen input manifest and returns a versioned
outcome (`passed`, `repairable`, `needs_replan`, `ambiguous`, or `terminal`)
plus bounded evidence/artifact claims. A Goal Verifier takes the confirmed
Intent and committed root references and returns (`passed`, `repairable_gap`,
`ambiguous`, or `impossible`) plus criterion records. All component calls are
recorded as idempotent `VerifierInvocation` or `PlannerInvocation` values in
the root aggregate using the pinned key, input hash, output hash, status, and
error. Replaying a key returns its durable result without executing the
component again.

**Child Flow is a role played by an ordinary immutable Workflow definition.**
It is not a second Workflow engine or an independently interpreted flow type.
A child template selects one mandatory Workflow whose input is a
`ContextManifest` and whose output is a `CandidateSubmission`. The term Child
Flow describes that constrained use of Workflow throughout this design.

The Task Graph Node output contains the confirmed Intent ID and version, final
graph ID and revision, Goal Verification record, criterion results, and
committed artifact/evidence references. The Node can emit `completed` only
after its pinned Goal Verifier passes and its ordinary Node completion contract
accepts this output.

### Workflow contract extension

The `task_graph` mode is an explicit V1 extension to the immutable Workflow
contract, not an unvalidated YAML escape hatch. It adds:

- `WorkflowExecution = operation | autonomous | task_graph`;
- a `TaskGraphNode` definition carrying the fields shown above;
- a versioned schema registry for `output_schema_id` and every dynamic
  structured input/output schema. `$ref` remains limited to local Workflow
  JSON-Pointers; registry references use IDs and are embedded into the
  execution contract before run creation;
- a `$wait` non-terminal sentinel accepted only by `task_graph` transitions;
- a runtime route for `completed`, `waiting`, and `failed` events. `$wait`
  persists the same Node attempt and a durable pending interaction/retry
  record; resume consumes that exact record and re-enters the scheduler without
  incrementing the Node attempt or changing the graph revision;
- parser, canonical fingerprint, instance validation, router, and completion
  handling for the new Node type. Existing `operation` and `autonomous` Node
  behavior and terminal sentinels remain unchanged.

The canonical `TaskGraphNode` is rejected if any registry ID is unknown, any
schema ID is unavailable, or a transition target is neither a declared Node
nor `$complete`, `$fail`, or the task-graph-only `$wait` sentinel. A
`TaskGraphNode` completion schema is resolved to its canonical schema snapshot
before the root execution contract is fingerprinted.

### Typical long-running Workflow

```text
clarify_intent (autonomous)
  -> confirm_intent (human review)
  -> execute_goal (task_graph)
  -> final_approval (optional human review)
  -> publish / complete
```

The existing `operation` and `autonomous` modes remain unchanged. The new mode
does not permit dynamic Workflow nodes or transitions.

Goal verification belongs inside the Task Graph Node because a repairable gap
must reopen that same graph. A later Workflow Node may review, approve, format,
or publish the verified result, but it may not independently decide whether
the Intent criteria were met.

## Intent Contract

### Schema

```yaml
intent_id: intent-01
version: 3
status: draft | confirmed | superseded

goal: "The state that should become true"
desired_outcome: "The practical result the user expects"

success_criteria:
  - id: criterion-login
    description: "Users can authenticate successfully"
    required: true
    verification_mode: validator | artifact | evidence | human_judgment
    validator_id: login_acceptance_v1

constraints:
  - id: constraint-platform
    description: "Must run on the existing platform"
    impact: high

non_goals:
  - "No native mobile application in V1"

priorities: [quality, speed, cost]
tradeoffs:
  quality_vs_speed: prefer_quality

assumptions:
  - id: assumption-language
    value: "Chinese UI"
    impact: low
    confirmed: false

open_questions:
  - id: question-payment
    question: "Does V1 process real payments?"
    impact: high

authority:
  allowed_actions: []
  prohibited_actions: []

budgets:
  max_tasks: 50
  max_replans: 10
  max_child_runs: 20
  max_concurrency: 4
```

### Clarification protocol

The Intent Clarifier:

1. extracts the initial goal, desired outcome, constraints, assumptions, and
   possible success criteria;
2. identifies ambiguity, conflict, and missing information;
3. ranks uncertainty by its effect on graph shape, authority, completion, cost,
   or risk;
4. asks one concise question at a time only for high-impact uncertainty;
5. records low-impact uncertainty as an explicit unconfirmed assumption;
6. repeats until no unresolved item would materially alter execution;
7. submits a compact Intent Contract for human confirmation.

All long-running runs require one initial human confirmation. Human-facing
review shows the goal, desired outcome, success criteria, non-goals, critical
constraints, assumptions, budgets, and authority boundary rather than an
internal graph dump.

### Intent patches

Intent versions are immutable. Material changes use an `IntentPatch`:

```yaml
base_version: 3
reason: "The user now requires production payment processing"
changes:
  - op: add_success_criterion
    criterion: ...
impact:
  reusable_completed_tasks: [catalog, product-ui]
  invalidated_tasks: [mock-checkout]
  tasks_to_add: [payment, security-review]
  estimated_budget_delta: ...
```

Human confirmation is required when a patch:

- changes the goal or desired outcome;
- adds or removes a required criterion;
- expands authority, risk, cost, duration, or child count materially;
- invalidates confirmed high-impact assumptions;
- selects between mutually exclusive valid execution directions.

Equivalent implementation changes, retry strategies, and local graph repairs
do not require human confirmation.

Once a human confirms an IntentPatch, the Parent Runtime applies an
`IntentRebase` as one root-aggregate CAS. The rebase:

1. appends the new immutable confirmed Intent version and supersedes the prior
   version;
2. records a deterministic impact decision for every live and completed Task
   and Group using its `intent_binding_hash` and exact dependency revisions;
3. retains unaffected completed versions as reusable, creates superseding Task
   or Group revisions where required, and rewrites only pending dependency
   edges to exact retained/new revisions;
4. cancels affected pending Tasks and fences affected active Attempts in the
   same transition;
5. increments graph revision once, clears stale criterion coverage, and records
   the replan trigger.

An active Attempt cannot be rebound to a new Intent. It either remains valid
against its recorded prior Intent binding or is fenced and replaced. Reuse is
permitted only when a pinned rebase validator proves that the prior Task's
criteria, inputs, dependency revisions, constraints, authority, and completion
contract remain valid under the new Intent.

The rebase records `intent_binding_state: retained` for a completed or active
Task that passes that proof, and `invalidated` for every Task that cannot be
reused. A retained Task may keep its prior `intent_version` while the graph
points to the new Intent; its retained binding is an explicit persisted fact,
not an implicit version-mismatch exception.

## Dynamic Task Graph

### Graph state

```yaml
graph_id: graph-01
intent_id: intent-01
intent_version: 3
revision: 7
status: planning | active | waiting | verifying | completed | failed | cancelled
completion_policy: all_required_criteria
limits: ...
replan_count: 2
```

### Task state

```yaml
task_id: backend
graph_id: graph-01
task_revision: 4
intent_version: 3
intent_binding_hash: sha256:...
intent_binding_state: current | retained | invalidated
goal: "Implement the order service"
supports: [criterion-orders]
depends_on:
  - { kind: task, task_id: api-design, task_revision: 2 }
  - { kind: task, task_id: data-model, task_revision: 1 }
priority: 80
required: true
kind: executable | expandable

completion_contract:
  output_schema_id: order-service-result-v1
  validator_ids: [build-passed-v1, tests-passed-v1]
  required_artifact_types: [source-code, test-report]
  required_evidence: []

executor_policy:
  allowed_modes: [child_agent, operation]
  bindings:
    child_agent: [backend-worker]
    operation: [build-service-v1]
    parent_inline: []
    human: []
  preferred_binding: { mode: child_agent, id: backend-worker }

failure_policy:
  max_attempts: 3
  retryable_kinds: [transient, resource]
  allow_executor_switch: true

expansion_policy:
  may_discover_work: true
  max_children: 5

status: pending | running | verifying | waiting | completed | failed | cancelled
active_attempt_id: attempt-02
output_refs: []
failure: null
```

`task_id` is a stable logical name and `(task_id, task_revision)` identifies one
concrete TaskRun version. Dependency edges always bind exact Task revisions.
Replacing or superseding a Task creates a new revision and atomically rewrites
affected pending dependency edges through a GraphPatch; prior revisions remain
immutable history. Each Task also records the confirmed Intent version and a
hash of the exact criteria, constraints, assumptions, and authority fragments
used to create it.

`ready` is derived and is not a persisted Task status. A Task is ready when:

- its persisted status is `pending`;
- every required dependency satisfies the declared join semantics;
- its Intent binding is `current` or explicitly `retained` by the latest
  atomic IntentRebase, and its completion contract remains valid;
- it has no active Attempt or lease;
- it is not within retry backoff;
- relevant graph and executor budgets remain available;
- required resources can be leased.

### Rolling-wave generation

Initial planning creates the smallest graph that:

- covers every required success criterion;
- contains at least one executable ready Task;
- expands near-term work concretely;
- leaves distant uncertain stages as `expandable` Tasks.

The initial seed is a `GraphPatch` with `base_revision: 0`, committed through
the same validator and root CAS as every later patch. It must create at least
one executable Task or Group with a satisfiable dependency set; a seed that
only contains unresolved expandable work is rejected.

When an expandable Task is about to become ready, the Runtime requires the
Parent Planner to propose a bounded child graph. The abstract Task becomes a
Group whose completion is derived from its children.

Task results, validation failures, dependency incompatibilities, budget
changes, user constraints, deadlocked graphs, and discovered-work suggestions
may trigger local replanning.

### Graph patches

The Parent Planner cannot replace a graph snapshot. It proposes an incremental
`GraphPatch`:

```yaml
base_revision: 7
trigger:
  type: verification_failed
  task_id: integration-test
reason: "Frontend and backend authentication contracts are incompatible"
operations:
  - op: add_task
    task: ...
  - op: replace_dependencies
    task_id: integration-test
    expected_task_revision: 2
    depends_on:
      - { kind: task, task_id: fix-auth-api, task_revision: 1 }
      - { kind: task, task_id: update-auth-client, task_revision: 1 }
```

V1 patch operations are:

- `add_task`;
- `add_group`;
- `expand_task`;
- `replace_pending_task`;
- `replace_pending_group`;
- `cancel_pending_task`;
- `replace_dependencies` for a pending Task;
- `set_priority` for a pending Task;
- `set_executor_policy` within the existing authority ceiling;
- `supersede_completed_task` by creating a new Task version;
- `add_repair_task`;
- `add_verification_task`.

The Graph Runtime validates:

- `base_revision` matches the current revision;
- every operation targeting a Task or Group carries its expected current
  revision;
- Task IDs and versions are unique;
- every dependency exists;
- the graph is acyclic;
- completed history is immutable;
- running completion contracts and executor bindings are unchanged;
- cancelled Tasks are not required by unmodified live Tasks;
- every required Task supports at least one criterion;
- every required criterion retains executable coverage;
- Worker templates and authority remain within the pinned contract;
- graph depth, Task count, replan count, child count, and budgets remain valid;
- the patch resolves a recorded trigger or reduces a named uncertainty.

Accepted patches commit atomically and increment graph revision exactly once.
Rejected patches return repairable feedback and do not alter graph state.

### Criterion coverage

Criterion state is separate from Task state:

```yaml
criterion_id: criterion-login
status: unsatisfied | partially_satisfied | satisfied | blocked
evidence_refs: [evidence://login-acceptance]
verified_by: acceptance-validator-v1
verified_at: ...
```

Task completion may contribute artifacts or evidence, but it does not directly
mark a criterion satisfied unless the criterion's verifier accepts it.

### Group state

A Group is a persisted graph node, not an executable Task and not an implicit
property of Attempts:

```yaml
group_id: implementation-options
group_revision: 1
intent_version: 3
intent_binding_hash: sha256:...
intent_binding_state: current | retained | invalidated
supports: [criterion-orders]
required: true
depends_on:
  - { kind: task, task_id: api-design, task_revision: 2 }
completion_contract:
  output_schema_id: implementation-group-result-v1
  validator_id: implementation-group-v1
children:
  - { task_id: implementation-a, task_revision: 1, required: true }
  - { task_id: implementation-b, task_revision: 1, required: true }
join_policy: all_required | any_success
failure_behavior: continue | cancel_unneeded | fail_group
status: pending | running | verifying | completed | failed | cancelled
winner_task_ref: null
verification_record_ref: null
```

Dependencies may target an exact Task revision or Group revision using an
explicit `kind: task | group` reference. Group state is derived and committed
by the Parent Runtime from child Task transitions:

- `pending` while no child has started;
- `running` after any child starts and the join is still achievable;
- `verifying` while a potentially satisfying child set is being checked;
- `completed` only after the pinned Group validator accepts the join;
- `failed` when the join is no longer achievable under its failure behavior;
- `cancelled` only through a validated GraphPatch or parent cancellation.

For `all_required`, the candidate set is every required child Task. For
`any_success`, each child is a separate Task with its own singular active
Attempt; the first verified child Task accepted by the Group validator wins.
V1 never runs competing Attempts for one Task. Group membership, policy,
completion contract, dependencies, criteria, and intent binding are immutable
within a Group revision; changing them creates a new Group revision and
rewrites only pending dependents.

`expand_task` is one GraphPatch transaction: it supersedes the pending
expandable Task, creates a Group revision plus all initial child Task revisions,
and rewrites pending dependents from the expandable Task reference to the new
Group reference. The Group inherits the expandable Task's exact Intent binding,
requiredness, criterion support, dependency references, authority ceiling, and
budget slice. Child Tasks inherit the same Intent binding and authority; their
criterion support must partition or collectively cover the Group's support, and
the patch validator rejects any coverage gap. A stale expandable Task can never
be expanded twice.

### Core state transitions

The Parent Runtime is the only writer of these transitions, and every row in a
transition is part of one root revision CAS:

| Object | Allowed transitions | Required precondition |
| --- | --- | --- |
| Task | `pending -> running/waiting/verifying/completed` | matching Task revision and dependency set |
| Task | `waiting -> running/verifying` | exact interaction/repair decision is resumed |
| Task | `pending/running/waiting/verifying -> cancelled` | parent cancellation, `any_success` winner, or superseding rebase |
| Task | `running/verifying -> pending` | retryable Attempt failure or repairable submission |
| Attempt | `created -> leased -> running -> submitted -> completed/failed` | dispatch key and fencing token match |
| Attempt | `created/leased/running/waiting -> cancelled` | pre-launch cancellation, winner fencing, or parent cancellation |
| Attempt | `submitted -> cancelled` | `any_success` winner or parent cancellation fences a candidate under verification |
| Attempt | `running -> waiting` | exact child or human interaction is durably persisted |
| Attempt | `waiting -> running` | exact pending interaction is resumed with the same manifest |
| Attempt | `submitted -> running/waiting` | prior submission is repairable and the same manifest is resumed |
| Group | `pending -> running -> verifying -> completed/failed` | child set and Group validator revision match |
| Group | `pending/running/verifying -> cancelled` | parent cancellation or superseding rebase |
| Graph | `planning -> active -> waiting/verifying -> active/completed/failed/cancelled` | coverage, budget, and Intent revision checks |

No transition may mutate a completed Task/Group revision. Replanning creates a
new revision and preserves the old record as immutable history.

## Child Agent Model

### Static template, dynamic assignment

Child Agent templates are declared before run creation and enter the parent
execution contract:

```yaml
child_templates:
  - id: backend-worker
    agent_definition: backend-worker-v1
    child_workflow: implementation-flow-v1
    output_schema: task-candidate-v1
    capabilities: [workspace-read, workspace-write, test]
    permission_profile: ...
    limits:
      max_steps: 20
      timeout_seconds: 900
```

The parent may select a template and pass dynamic Task inputs. It may not create
new system instructions, tools, authority, or Child Flow definitions at runtime.

The default execution pattern uses homogeneous workers for independent Tasks.
Specialist templates are allowed when statically declared and selected by Task
executor policy.

### Child run identity

Each child execution is a real Workflow run with:

- an independent `run_id`;
- the same `root_run_id` as the parent;
- `parent_run_id` and `parent_attempt_id`;
- the child Workflow and child execution-contract fingerprints pinned by its
  parent template;
- its own Workflow state, StepRecords, invocations, interactions, and trace;
- its own workspace partition and context manifest;
- effective authority equal to the intersection of all parent and child scopes.

The parent stores only child references and structured summaries in its active
planning context.

Child checkpoint identity is collision-free and deterministic:

```text
checkpoint_ns =
  roots/{root_run_id}/nodes/{parent_node_id}/{parent_node_attempt}/
  attempts/{task_attempt_id}/children/{child_run_id}/workflow
```

The parent Attempt stores that exact namespace, the child template ID, child
Workflow fingerprint, and child execution-contract fingerprint copied from the
pinned parent contract. The child's initial checkpoint stores the reciprocal
`root_run_id`, `parent_run_id`, `parent_node_id`, `parent_node_attempt`,
`parent_attempt_id`, template ID, and both fingerprints. Restore requires all
bindings to match; current registry definitions are never consulted to rebuild
an existing child contract.

### Child candidate result

```yaml
submission_id: submission-03
submission_sequence: 3
task_id: backend
task_revision: 4
attempt_id: attempt-02
lease_token: opaque-fencing-token
child_run_id: child-run-09

outcome: candidate_completed | needs_followup | blocked | failed
result:
  summary: ...
  structured_output: ...
artifact_candidates: []
evidence_claims: []
discovered_work:
  - goal: ...
    rationale: ...
    suggested_dependencies: []
failure: null
```

The Parent Runtime rejects candidate results whose Task revision, Attempt ID,
lease token, ChildRun ID, or completion-contract hash does not match the active
Attempt.

`candidate_completed` moves the Task to `verifying`; it does not complete it.

`submission_id` is globally unique and `(attempt_id, submission_sequence)` is
monotonic and unique. An Attempt may submit multiple candidates only when the
parent recorded the previous submission as `repairable` and resumed the same
immutable ContextManifest. Reusing either key returns the prior durable receipt
without running validators again; gaps or conflicting payload hashes are
rejected.

The child first persists the submission and payload hash in its checkpoint,
then calls the parent's idempotent receipt endpoint. The parent performs one
CAS transition that records a `CandidateReceipt(status=received)`, changes the
Attempt to `submitted`, changes the Task to `verifying`, and invalidates that
Attempt's dispatch lease. Validator execution may occur after this receipt and
is itself keyed by `(submission_id, validator_id, validator_version)`. A final
parent CAS records all validator outcomes and atomically either:

- accepts the submission, commits output references, and completes the Task;
- marks it repairable and returns the same Attempt to `running` or `waiting`;
- closes the Attempt and schedules a new Attempt;
- records a replan, human-judgment, or terminal trigger.

A crash at any point replays from the receipt and durable validator records;
neither child resubmission nor parent restart can duplicate acceptance.

## Task Attempts

Every execution has an immutable Attempt identity and append-only history. The
current Attempt value in the root aggregate advances only through the listed
CAS transitions:

```yaml
attempt_id: attempt-02
task_id: backend
task_revision: 4
status: created | leased | running | waiting | submitted | completed | failed | cancelled
executor_mode: child_agent
executor_template: backend-worker
executor_binding:
  mode: child_agent
  id: backend-worker
  component_fingerprint: sha256:...
child_run_id: child-run-09
dispatch_key: dispatch-02
context_manifest_ref: context://attempt-02
completion_contract_hash: sha256:...
parent_execution_contract_fingerprint: sha256:...
child_workflow_fingerprint: sha256:...
child_execution_contract_fingerprint: sha256:...
child_checkpoint_ns: roots/.../children/child-run-09/workflow
lease_epoch: 3
lease_token: opaque-fencing-token
lease_expires_at: ...
started_at: ...
finished_at: null
outcome: null
failure: null
output_refs: []
```

`created` is the durable prepared state before launch; `leased` means an
idempotent dispatch is in progress but not yet acknowledged. A repairable
submission that resumes the same Attempt advances its lease epoch and returns
the Attempt to `running` or `waiting` through a new parent CAS.

`executor_binding` is a discriminated union. Child-only fields such as
`executor_template`, `child_run_id`, child fingerprints, and child checkpoint
namespace are absent for Operation, parent-inline, and human Attempts; those
modes persist their adapter/component/interaction binding instead.

Retries create new Attempts unless the existing child run is resumable and its
immutable input binding remains valid. Attempt history is never overwritten.

## Scheduling

### Ready queue

The Scheduler reconstructs the ready set from persisted graph and Attempt
state. The queue itself is not persisted.

Candidates are ordered deterministically by:

1. explicit priority;
2. critical-path impact;
3. required before optional;
4. stable Task ID tie-break.

Semantic Planner advice may adjust explicit priority only through a validated
GraphPatch; it cannot bypass readiness.

### Executor modes

V1 supports:

- `child_agent`: isolated Agent + Child Flow;
- `operation`: deterministic Operation invocation;
- `parent_inline`: bounded parent planning, synthesis, or graph revision work;
- `human`: durable interaction or judgment.

Concrete domain work should default to child Agents or Operations. Parent-inline
work is reserved for coordination and synthesis so the parent context remains
compact.

Every allowed executor has a persisted, pinned binding in the Task policy and
the selected binding is copied into its Attempt:

- `child_agent` binds a child template ID and its embedded fingerprints;
- `operation` binds an allowed Operation adapter ID plus its input/output
  schemas and idempotency/reconciliation contract;
- `parent_inline` binds a `PinnedComponent` callable and records a durable
  `ParentInlineInvocation` with the same input-hash/idempotency rules as
  Planner/Verifier calls;
- `human` binds a versioned `HumanTaskContract` containing prompt schema,
  response schema, allowed decision class, authority requirement, timeout
  behavior, and resume policy.

A human Task creates a `PendingTaskDecision` with request ID, Task/Attempt and
graph revisions, contract fingerprint, input hash, and expected root revision.
The accepted response is schema-validated and consumed exactly once; duplicate
responses return the prior durable result. Switching executor modes always
creates a new Attempt and may select only another binding already present in
the Task policy and root execution contract.

### Concurrency and locks

The Task Graph policy specifies:

```yaml
max_concurrency: 4
per_template_limits:
  browser-worker: 1
  code-worker: 2
resource_lock_policy:
  workspace-write: exclusive_by_path
```

Tasks that are graph-independent may still serialize when their declared
resource scopes conflict. Read-only artifact use does not require an exclusive
lock.

Scheduler preparation is atomic inside the parent aggregate; executor launch
is deliberately outside that transaction:

```text
parent CAS: derive ready Task
  -> claim logical slot and resource locks
  -> create Attempt in prepared state
  -> allocate child_run_id and checkpoint namespace when needed
  -> assign dispatch_key, lease epoch, and fencing token
  -> persist parent checkpoint
create initial child checkpoint idempotently from the pinned contract
dispatch executor idempotently by dispatch_key
parent CAS: record dispatch acknowledgement and running state
```

Late or duplicate child results with obsolete fencing tokens cannot mutate the
Task.

The dispatcher must implement `ensure_started(dispatch_key, binding)`: repeated
calls return the same child handle or Operation invocation and may never create
a second executor. Parent recovery reconciles every prepared Attempt:

- no child checkpoint: create it idempotently and dispatch;
- child checkpoint exists but no launch acknowledgement: query by
  `dispatch_key`, then start only if absent;
- executor exists but parent acknowledgement is absent: attach the existing
  handle and commit acknowledgement;
- parent Attempt was cancelled before launch: fence it and mark any discovered
  executor cancelled/orphaned;
- binding or fingerprints differ: quarantine the child and require
  reconciliation; never adopt it.

Operation executors use the same durable prepare/dispatch/reconcile pattern and
their existing invocation idempotency or reconciliation contracts. No design
claim relies on atomically committing local state together with an external
side effect.

### Leases and fencing

Each active Attempt has a monotonically increasing `lease_epoch`; its fencing
token is derived from the Attempt ID and epoch. The Parent Scheduler is the only
lease issuer and renewer. A child may report liveness, but liveness does not
extend a lease until a parent CAS records the new expiry. Lease renewal requires
the same dispatch binding, active child checkpoint, non-terminal graph, and
held resource locks.

Expiry makes an Attempt *suspect*, not immediately replaceable. The parent
queries the executor by dispatch key:

- live or durably resumable: renew or resume the same Attempt;
- definitely absent: CAS-close the Attempt, increment the fencing epoch,
  release its locks/slot, and create a replacement according to policy;
- uncertain or side-effecting: enter reconciliation and retain conflicting
  locks until resolved.

Task/Group completion, cancellation, replacement, and executor switching each
advance the relevant fencing epoch in a parent CAS. Late heartbeats,
acknowledgements, submissions, and side-effecting tool calls return the
terminal receipt for their obsolete token. The execution gateway checks the
current token before every child side effect.

Fencing result acceptance does not prove the physical executor has stopped.
Concurrency slots and exclusive resource locks held by a cancelled executor
move to `retiring` and remain unavailable until stop acknowledgement or
definite-absence reconciliation. Read-only work without an exclusive resource
may release its logical lock immediately, but its physical slot remains counted
until termination.

## Join Semantics

Join policy belongs to a persisted Group, not to the whole system or an
implicit collection of Attempts.

V1 implements:

### `all_required`

All required children must complete successfully. Optional children may fail
without blocking the Group unless a Group validator rejects the remaining
coverage.

### `any_success`

The first completed child Task that passes the Group validator satisfies the
Group. Winner selection is one parent CAS that records `winner_task_ref`,
completes the Group, cancels remaining pending child Tasks, advances fencing
epochs for running losing Tasks, moves their slots/locks to `retiring`, and
creates durable cooperative-cancellation requests. Physical executors may stop
later; their late results and side effects receive terminal stale-token
receipts, while resource release waits for stop or reconciliation.

The losing child Task records `cancelled`; its active Attempt records
`cancelled` immediately for result acceptance purposes and retains a
`retiring` resource lease until physical stop or reconciliation. The Group is
already complete and downstream readiness does not wait for those retiring
executors, except where a declared exclusive resource lock is still needed.

Each policy includes a failure behavior:

```yaml
failure_behavior: continue | cancel_unneeded | fail_group
```

This separates join completion from execution behavior. V1 does not implement
quorum or arbitrary custom join code, but the schema reserves a versioned
`join_policy` discriminant for later extension.

`failure_behavior` means:

- `continue`: leave still-useful optional child Tasks running after the join;
- `cancel_unneeded`: request cancellation of children no longer needed for the
  accepted join;
- `fail_group`: fail the Group as soon as the policy can no longer be met.

For `any_success`, `cancel_unneeded` is the default. `continue` is allowed only
for children whose outputs support another live criterion; otherwise they are
unneeded and cancelled. `all_required` fails when any required child becomes
irrecoverably failed, regardless of optional-child behavior.

## Verification and Completion

### Task verification

When a candidate is submitted, the Parent Runtime:

1. validates identity and fencing fields;
2. validates the structured output schema;
3. confirms referenced artifacts and evidence exist and match declared hashes;
4. checks producer and visibility provenance;
5. invokes every versioned Task validator;
6. records validator evidence and result;
7. completes the Task only if all required checks pass.

Outcomes are:

- `passed`: Task becomes `completed`;
- `repairable`: resume or retry according to policy;
- `needs_replan`: request a local GraphPatch;
- `ambiguous`: request human judgment;
- `terminal`: Task becomes `failed` and graph propagation applies.

### Goal verification

Task Graph terminality is not Goal completion. Once no required executable work
remains, the Goal Verifier:

- reads the confirmed Intent version;
- gathers committed artifact/evidence references;
- evaluates every required success criterion independently;
- produces criterion coverage records;
- returns one of:
  - `passed`;
  - `repairable_gap` with proposed repair trigger;
  - `ambiguous` requiring human judgment;
  - `impossible` with explicit reasons.

A repairable gap returns the Task Graph to `active` through a validated
GraphPatch. Only `passed` permits the outer Workflow to follow the Task Graph
Node's `completed` transition.

Goal outcome transitions are explicit:

| Goal outcome | Graph transition | Workflow Node behavior |
| --- | --- | --- |
| `passed` | `verifying -> completed` | emit the verified result and follow `completed` |
| `repairable_gap` | `verifying -> active` after a validated repair patch | continue scheduling |
| `ambiguous` | `verifying -> waiting` with a `PendingGoalDecision` | follow `$wait` and resume only from the recorded decision |
| `impossible` | `verifying -> failed` with terminal reasons | follow `failed`; a new Intent requires a new confirmed version |

`PendingGoalDecision` contains request ID, root/graph revision, Goal
Verification record ID, criterion gaps, bounded options, and an expected
revision. A human decision is accepted once against that expected revision. An
approval either supplies a repair direction (returning to `active`) or accepts
the documented ambiguity as a new IntentPatch; a rejection records terminal
failure. Duplicate decisions return the prior result. A lost process restores
the same pending request rather than asking a new question.

## Failure and Recovery

### Failure record

```yaml
kind: transient | invalid_output | dependency | permission | resource |
      external_blocker | uncertain_side_effect | terminal
code: provider_timeout
message: ...
retryable: true
retry_after: ...
attempt_id: attempt-02
```

### Default recovery order

```text
resume existing child run
  -> retry the same template
  -> switch to another allowed template
  -> propose a local GraphPatch
  -> request human judgment
  -> mark terminal failure
```

The Task failure policy selects which stages are allowed for each failure kind.
The Runtime, not the model, enforces maximum Attempts, backoff, template
authority, and terminality.

### Uncertain side effects

An Attempt with an uncertain side effect enters reconciliation. The Scheduler
must not launch a replacement Attempt that could duplicate the effect until a
registered reconciler or human judgment resolves the original invocation.

### Dependency propagation

A failed Task does not globally fail the graph by default. The Runtime checks:

- whether downstream Tasks require it;
- whether their join policy can still be satisfied;
- whether a replacement or repair path exists;
- whether the affected criterion still has valid coverage.

Only irrecoverable required-path failure makes the Graph terminally failed.

## Context Isolation

### Context manifest

Each Attempt receives an immutable `ContextManifest`:

```yaml
context_id: context-attempt-02
intent:
  intent_id: intent-01
  version: 3
  goal_summary: ...
  relevant_criteria: [criterion-orders]
task:
  task_id: backend
  task_revision: 4
  goal: ...
  completion_contract: ...
  constraints: []
  assumptions: []
dependencies:
  - task_id: api-design
    result_summary: ...
    artifact_refs: [artifact://openapi-v2]
inputs:
  artifact_refs: []
  evidence_refs: []
  memory_refs: []
authority:
  tools: []
  readable_scopes: []
  writable_scopes: []
budgets:
  max_steps: 20
  timeout_seconds: 900
```

The child sees only:

- its Task goal and relevant criteria;
- its frozen completion contract;
- relevant constraints and assumptions;
- direct dependency summaries and explicit references;
- explicitly scoped memory;
- its Child Flow and its own history.

It does not automatically see the full parent conversation, the complete
graph, unrelated Tasks, other child reasoning, or broader workspace/memory.

If Task inputs, Intent version, dependency artifacts, authority, or completion
contract change, a new Attempt and ContextManifest are required. Resume of the
same Attempt uses the same manifest.

### Effective child authority

```text
parent pinned authority
  INTERSECT child template authority
  INTERSECT Child Flow capability ceiling
  INTERSECT Task executor policy
  INTERSECT ContextManifest resource scope
  INTERSECT current policy
```

Authority can only narrow after run creation.

## Artifacts and Evidence

### Artifact reference

```yaml
artifact_id: artifact-openapi-v2
producer_task_id: api-design
producer_attempt_id: attempt-api-01
producer_child_run_id: child-run-02
type: openapi-spec
uri: workspace://artifacts/openapi-v2.yaml
content_hash: sha256:...
schema_version: openapi-3.1
visibility: task | graph | workflow
supersedes: artifact-openapi-v1
created_at: ...
```

Artifacts are immutable. A changed artifact creates a new reference and may
supersede an earlier version.

Children do not create committed Artifact references. They write immutable
bytes to an Attempt-scoped staging area and submit an `ArtifactCandidate`
containing URI, content hash, size, type, schema version, and proposed
visibility. The Workspace blob store is authoritative only for bytes addressed
by URI and hash. The root aggregate is authoritative for accepted Artifact
metadata, provenance, visibility, and supersession.

The parent verifies staged bytes before acceptance. The same CAS that accepts a
Task submission creates the committed Artifact metadata. A crash before that
CAS leaves unreferenced staging bytes, which are safe to retry and later garbage
collect after the Attempt reaches a terminal state plus a retention period. A
crash after the CAS is safe because the bytes necessarily existed and matched
their hash before commit. Committed bytes and metadata are immutable.

The Task Graph uses a new content-addressed `TaskArtifactStore` facade rather
than the legacy path-overwriting `save_artifact` tool:

```text
stage(attempt_id, bytes, metadata)
  -> write temp file in attempt staging partition
  -> fsync and atomically rename to blob://sha256/{content_hash}
  -> create-if-absent; never overwrite an existing hash
seal(blob_ref, expected_hash, expected_size)
  -> verify bytes by rereading the content-addressed object
read_verified(blob_ref)
  -> return bytes only when hash and size still match
```

`ArtifactCandidate` may reference only a sealed blob. The parent reads the
sealed content-addressed object and records its hash in the same CAS that
publishes metadata. A producer cannot mutate it after sealing; changed content
gets a new hash and a new URI. A missing object, hash mismatch, or attempted
overwrite rejects the submission and enters the normal repair path. The legacy
`save_artifact` remains available to non-Task-Graph Workflows but cannot create
committed graph artifacts.

### Evidence reference

```yaml
evidence_id: evidence-login-test
criterion_id: criterion-login
claim: "The login flow satisfies the acceptance criterion"
source_ref: artifact://login-test-report
producer_attempt_id: attempt-test-02
verification_method: test-validator-v1
verification_status: verified
verifier_id: acceptance-worker-v1
verified_at: ...
```

Artifacts are produced work. Evidence supports a completion claim. Task and
Goal validators may require both.

A child may submit only an `EvidenceClaim` with the claim, source candidates,
and producer provenance; it cannot set `verification_status`, `verifier_id`, or
`verified_at`. The Parent Verifier creates the committed Evidence record and is
the sole writer of verification status. Rejected claims remain immutable
submission data attached to their CandidateReceipt, not graph-visible Evidence.

### Cross-child transfer

Children never communicate directly:

```text
Child A produces Artifact A
  -> Parent validates and commits Task A
  -> Scheduler resolves Task B dependency
  -> Context Builder adds Artifact A to Task B's manifest
  -> Child B reads Artifact A
```

This makes every dependency input versioned, attributable, authorized, and
replayable.

All external, uploaded, Web-derived, or child-produced content carries trust
metadata. It is data, not an instruction source.

## Parent Context Management

The parent planning context is a projection, not a concatenation of child
histories. It contains:

- the Intent summary and current version;
- the current graph projection;
- ready/running/waiting/failed Task summaries;
- active failures and criterion gaps;
- committed result summaries;
- artifact/evidence metadata;
- recent graph patches and human decisions;
- current budgets and authority boundaries.

Large outputs are read by reference only when needed. Child hidden reasoning
and full message histories never merge into the parent context.

## Durable State and Ownership

### Aggregate boundaries

V1 persists exactly two kinds of mutable transaction aggregate:

1. The **root Workflow checkpoint aggregate** contains the root WorkflowState,
   IntentRun version history, active TaskGraphRun, all Task/Group/Attempt
   records, CandidateReceipts, committed Artifact/Evidence metadata,
   verification records, leases, locks, and audit events for that root run.
   These are value records inside one revisioned checkpoint, not independently
   mutable repositories.
2. Each **child Workflow checkpoint aggregate** contains only that child
   WorkflowState, steps, messages, invocations, interactions, staged-output
   manifest, and reciprocal parent binding.

Workspace blob storage contains immutable bytes. It does not own graph-visible
metadata or mutable verification status. Append-only trace sinks and UI indexes
are rebuildable projections and never participate in correctness decisions.

### Root checkpoint persistence contract

The long-running runtime requires a `RootCheckpointStore` with this interface:

```text
load(root_run_id) -> RootRunSnapshot | absent
create(root_run_id, snapshot_revision=0, snapshot) -> success | conflict
compare_and_swap(root_run_id, expected_revision, new_snapshot, event)
  -> committed_revision | conflict | durable_error
```

`RootRunSnapshot` contains the ordinary WorkflowState and the complete
TaskGraph aggregate in one serialized record. `compare_and_swap` executes under
the storage transaction, verifies the expected revision, writes the new
snapshot and embedded audit event atomically, and returns `conflict` without
partial data. The root revision is the sole fencing value for parent graph
mutations; callers retry by reloading and re-evaluating, never by merging stale
snapshots blindly.

The V1 SQLite adapter uses one row per root and `BEGIN IMMEDIATE` (or an
equivalent conditional update) for the CAS. The in-memory adapter uses the same
interface under a process lock for tests. A backend without durable conditional
writes is rejected for `task_graph` runs. Existing legacy WorkflowSession
checkpoints remain supported, but a `task_graph` Node is admitted only after
its root aggregate has been migrated into this versioned record; the legacy
non-CAS `put` path is never used for graph correctness.

Child checkpoints use the same interface under their deterministic child
namespace. Parent preparation is committed before child creation, and parent
observations are refreshed from the child snapshot before any transition that
depends on child liveness or output.

The parent may cache a child observation containing child run ID, last observed
child revision/status, and checkpoint cursor. This is explicitly a stale-able
projection. Child state remains authoritative in the child checkpoint, and the
parent must reread it before transitions that depend on current child status.

### State ownership

| State | Authoritative owner |
| --- | --- |
| Root Workflow, Intent, Graph, Task, Group, Attempt state | root checkpoint aggregate |
| Candidate receipts and global verification records | root checkpoint aggregate |
| Committed Artifact/Evidence metadata | root checkpoint aggregate |
| Leases, locks, retry times, fencing epochs | root checkpoint aggregate |
| Child steps, messages, interactions, and invocations | child checkpoint aggregate |
| Staged and committed immutable bytes | Workspace blob store |
| Task/Group/Criterion/Goal decision logic | pinned Parent Verifiers |
| Trace and UI views | rebuildable projections |

No other object may maintain an independent mutable copy of these facts.

### Persisted snapshot

The parent checkpoint adds:

- current Intent snapshot and version history;
- TaskGraphRun snapshot;
- TaskRun and TaskAttempt records;
- CandidateReceipt and pinned Planner/Verifier invocation records;
- child run references and observation projections;
- ContextManifest references;
- committed Artifact/Evidence metadata and verification records;
- leases and fencing tokens;
- held resource locks;
- retry schedules;
- PendingTaskDecision and PendingGoalDecision records;
- pending joins;
- criterion coverage;
- graph transition events;
- child result summaries.

Each child run has its own checkpoint namespace and Workspace partition.

The parent stores child references and observation projections, not
authoritative child statuses. On restore, the observation cursor is compared
with the child checkpoint revision and refreshed before any dependent parent
transition.

Every state mutation uses revision compare-and-swap. A successful transition
atomically commits the new snapshot and one audit event.

One root CAS may change multiple contained records and is the only atomicity
boundary for graph correctness. Child checkpoint writes and root writes cannot
be atomic together; their prepare/idempotent-dispatch/reconciliation protocols
handle every partial state. Audit publication outside the checkpoint uses the
root revision as an idempotency key.

### Verification transition order

Verification has one authoritative order:

```text
CandidateReceipt
  -> Task validator records
  -> Task completion + committed Artifact/Evidence metadata
  -> affected Group validator record and Group transition
  -> criterion verifier records and coverage projection
  -> graph terminality check
  -> Goal Verification record
  -> Task Graph Node completion
```

All records in this sequence are root-aggregate values written by the Parent
Runtime. A Task validator cannot directly complete a Group or satisfy a
criterion. A Group validator cannot update criterion coverage. Criterion
verification may occur incrementally after accepted Task/Group transitions,
but the Goal Verifier always re-evaluates every required criterion from
committed records and produces the final authoritative Goal Verification.

### Derived state

The following are recomputed and are not authoritative persisted fields:

- ready queue membership;
- progress percentage;
- display order;
- remaining Task count;
- critical path;
- scheduler queue position.

## Crash Recovery

On parent recovery:

```text
load Workflow and execution contract
  -> load Intent and Task Graph snapshot
  -> restore child run references and Attempts
  -> recompute ready set
  -> inspect running Attempts
     -> resumable child: resume/poll
     -> waiting child: preserve interaction
     -> uncertain invocation: reconcile
     -> expired lease with no active executor: create replacement Attempt
  -> reject stale late results
  -> resume scheduling
```

The Runtime never restarts the entire goal solely because one child failed or
the parent process restarted. Completed Task outputs and immutable artifacts
remain reusable unless an approved IntentPatch explicitly invalidates them.

Recovery is driven by durable parent/child bindings and dispatch keys, not by
process-local handles. Orphan reconciliation covers both partial creation
directions:

- parent prepared Attempt, no child checkpoint: idempotently create and start;
- child checkpoint exists, parent still prepared: verify reciprocal binding and
  adopt the allocated child ID through the existing Attempt;
- child exists without a matching active parent Attempt: never attach it to
  another Task; fence/cancel it and retain it for audit;
- parent says running but child is missing: query dispatcher, then either
  restore, recreate by the same dispatch key, or classify uncertain;
- fingerprints or namespace binding disagree: quarantine and require
  reconciliation.

### Crash boundary matrix

| Boundary | Durable recovery rule |
| --- | --- |
| Before parent Attempt CAS | No work exists; readiness is recomputed |
| After parent prepare, before child checkpoint | Create child checkpoint idempotently |
| After child checkpoint, before dispatch | `ensure_started(dispatch_key)` starts once |
| After dispatch, before acknowledgement | Query/adopt the executor by dispatch key |
| After candidate staging bytes | Retry submission or garbage collect unreferenced bytes |
| After child submission persist, before parent receipt | Child resubmits same idempotency keys |
| After receipt, during validators | Resume missing validator records by stable keys |
| After validation, before acceptance CAS | Recompute final CAS from durable records |
| After Task acceptance | Committed metadata and Task output survive restart |
| During `any_success` winner CAS | CAS chooses exactly one winner or retries from prior state |
| After winner CAS, before physical stop | Stale fencing rejects loser; cancellation is retried |
| At lease expiry | Reconcile liveness before replacement or lock release |
| During Intent rebase | One CAS exposes either old or fully rebased graph |

## Human-in-the-Loop

HITL is required for semantic judgment, not ordinary recovery.

### Required human gates

- initial Intent confirmation;
- material IntentPatch approval;
- authority or risk expansion;
- mutually exclusive valid execution directions;
- high-impact assumption invalidation;
- ambiguous Goal verification;
- uncertain side-effect reconciliation when automation cannot decide;
- explicit final approval when the outer Workflow declares it.

### Automatic actions

- transient retry within policy;
- resuming the same child context;
- bounded invalid-output repair;
- switching to an already allowed equivalent template;
- local GraphPatch that does not change Intent;
- dependency repair;
- resource backoff;
- cancellation of unneeded `any_success` Attempts.

## Events and Trace

V1 records compact transition events:

```text
intent_drafted
intent_clarification_requested
intent_confirmed
intent_patch_proposed
intent_superseded
graph_created
graph_patch_proposed
graph_revised
task_became_ready
task_leased
attempt_started
child_run_started
child_run_waiting
candidate_submitted
task_verification_passed
task_verification_failed
retry_scheduled
executor_switched
task_completed
task_failed
join_satisfied
criterion_verified
goal_verification_failed
goal_verified
graph_completed
```

Events contain IDs, revisions, reasons, status changes, fingerprints, and
bounded summaries. They do not duplicate full prompts, artifact contents, or
child transcripts.

## Security and Integrity Invariants

The Runtime must enforce:

1. every child template and Child Flow is pinned in the parent execution
   contract;
2. child authority is an intersection and can never expand parent authority;
3. children cannot mutate the parent graph or sibling state;
4. every graph mutation is a validated patch against the current revision;
5. completed Task history and immutable artifacts cannot be rewritten;
6. running Attempt contracts and context manifests cannot change;
7. every candidate result matches the current Attempt lease and fencing token;
8. every required Task supports a success criterion;
9. every required criterion retains graph coverage;
10. Task completion is decided by Parent Verifiers;
11. Goal completion is independent from graph terminality;
12. external content cannot become trusted instructions;
13. uncertain side effects cannot be blindly retried;
14. child recursion is rejected in V1;
15. all concurrency and replan budgets are deterministic hard limits;
16. every resumed Planner/Verifier/schema/template resolves by the pinned
    implementation digest and protocol, never by a mutable registry lookup;
17. every root mutation uses durable expected-revision CAS, and every graph
    ArtifactCandidate references a sealed content-addressed blob.

## Compatibility and Migration

The current Workflow runtime remains mandatory. Existing `operation` and
`autonomous` Workflows continue to run without Task Graph state.

The existing flat `TaskPlan` protocol remains temporarily supported for legacy
autonomous Nodes and UI projection. It is not the authoritative long-running
graph model. A Task Graph Node may project a simplified list for CLI display,
but the projection cannot be written back as graph truth.

The historical subagent runtime is not restored as a compatibility package.
New child dispatch is implemented through child Workflow runs, execution
contract pinning, ContextManifests, and parent-owned Task Attempts.

Research Assistant becomes the first migrated application after the generic
vertical slice is complete:

- its outer Workflow confirms research intent;
- its dimensions become graph Tasks;
- independent Tasks run in parallel research children;
- each child uses a research Child Flow;
- the parent verifies Findings and produces the report;
- research-specific evidence rules remain outside generic graph semantics.

## Implementation Slices

The implementation plan should preserve vertical correctness in these slices:

### Slice 1: Intent and Task Graph state

- immutable Intent versions and HITL confirmation;
- root checkpoint aggregate and all projection/ownership rules;
- Task Graph, Task, Group, Attempt, CandidateReceipt, and patch types;
- deterministic graph validation and readiness;
- one in-process `task_graph` Workflow Node with Operation executors only;
- a minimal deterministic Goal Verifier required before Node completion;
- checkpoint and trace integration.

### Slice 2: Child Workflow runs

- static child templates in Agent definitions and execution contracts;
- child run identity, ContextManifest, workspace isolation, and authority
  intersection;
- one child Attempt at a time;
- prepare/idempotent-dispatch/reconcile and reciprocal checkpoint binding;
- candidate verification and result commit;
- parent/child checkpoint restoration.

### Slice 3: Parallel scheduler and joins

- bounded concurrent child runs;
- leases, fencing tokens, per-template limits, and resource locks;
- `all_required` and `any_success` joins;
- cooperative cancellation and stale-result rejection.

### Slice 4: Dynamic replanning and Goal repair

- expandable Tasks and graph patches;
- failure classification and recovery ladder;
- incremental criterion coverage and repairable Goal Verification gaps;
- parent-inline Planner/Context Builder execution and durable human Task
  decisions, including `$wait` resume;
- ambiguous/impossible Goal outcomes, `PendingGoalDecision`, and executor
  switching within the pinned authority ceiling;
- material IntentPatch HITL.

### Slice 5: Application migrations

- Research Assistant parallel research graph;
- software implementation acceptance Agent;
- redundant `any_success` execution fixture;
- CLI progress projection and child-run inspection.

No slice may land with two authoritative state models for the same run.

## Acceptance Tests

### Workflow and contracts

- `task_graph` definitions parse into an immutable Node, accept only `$wait` as
  its new non-terminal sentinel, resolve registry schema IDs, and reject
  external JSON `$ref`s or unknown component IDs;
- a resumed run loads pinned implementation digests even after the current
  registry entry changes, and refuses resume when the pinned digest is absent;
- Planner/Verifier invocations replay by stable idempotency key and never
  execute twice after a crash.

### Intent

- high-impact ambiguity triggers one-at-a-time clarification;
- low-impact ambiguity becomes an explicit assumption;
- graph execution cannot start before Intent confirmation;
- material Intent changes require a new confirmed version;
- approved Intent v2 preserves unaffected completed Task outputs.
- Group Intent bindings, inherited dependencies, and exact child revisions are
  preserved or superseded deterministically during rebase.

### Graph generation and mutation

- seed graph covers every required criterion and contains a ready Task;
- expandable Task is expanded only when needed;
- cyclic, stale-revision, authority-expanding, or coverage-breaking patches are
  rejected atomically;
- completed Tasks cannot be modified;
- superseding work creates a new Task version;
- graph replan budgets stop unbounded expansion.

### Scheduling

- independent Tasks run concurrently up to the configured limit;
- dependencies enforce serial order;
- mixed DAG execution produces the correct order;
- resource conflicts serialize otherwise independent Tasks;
- deterministic ordering is stable across restart.
- an Operation Task dispatches only its persisted adapter binding;
- a parent-inline Task replays its durable invocation instead of calling twice;
- a human Task restores the same PendingTaskDecision and consumes one
  schema-valid response exactly once.

### Child isolation

- child sees only its ContextManifest and own history;
- parent and sibling messages are unavailable;
- child writes land in its workspace partition;
- child cannot exceed the intersected authority;
- child cannot spawn another child;
- parent receives summaries and references, not full transcripts.

### Verification and joins

- child `candidate_completed` cannot directly complete a Task;
- invalid schema, missing artifact, bad hash, or failed validator rejects the
  candidate;
- `all_required` waits for all required children;
- `any_success` accepts the first verified candidate and fences late results;
- Goal verification can reopen the graph with repair work after all Tasks have
  terminated.

### Recovery

- parent crash restores graph state and recomputes readiness;
- child crash resumes the same run when its manifest is still valid;
- retry creates a new immutable Attempt when resume is impossible;
- completed outputs survive later failures;
- uncertain side effects require reconciliation rather than duplicate retry;
- stale Attempt results cannot overwrite current Task state.
- pre-launch cancellation leaves no adopted executor and releases only the
  resources proven unheld;
- ambiguous Goal verification restores one `PendingGoalDecision`, while an
  impossible result reaches the declared terminal Workflow transition;
- root compare-and-swap conflicts reload the latest aggregate and never merge
  stale graph snapshots.

### Artifact integrity

- staging creates sealed content-addressed blobs that cannot be overwritten;
- modifying a staged path after sealing is impossible for the Task Graph store,
  and hash/size mismatch or missing bytes rejects the candidate;
- a crash before metadata acceptance leaves only garbage-collectable staged
  bytes, while a crash after acceptance leaves a complete committed reference.

### Application scenarios

1. **Software implementation:** design and backend Tasks run in parallel; an
   integration Task waits for both and verifies build/test artifacts.
2. **Deep research:** independent dimensions run in isolated research children;
   the parent verifies evidence and joins Findings into one report.
3. **Redundant execution:** two allowed workers compete under `any_success`; the
   first verified result wins and the other is cancelled/fenced.
4. **Failure recovery:** a child process fails after producing an artifact; the
   run resumes or retries without losing the committed artifact.
5. **Intent drift:** a new critical constraint produces Intent v2 and replans
   only affected pending or superseded Tasks.

## Success Criteria

The design is successfully implemented when:

- a confirmed Intent can drive a dynamically expanding, versioned Task Graph;
- independent Tasks execute concurrently in isolated child Workflow runs;
- serial, parallel, and mixed behavior arises from graph dependencies and join
  policies rather than prompt convention;
- child contexts and authority are demonstrably isolated;
- Task and Goal completion are verified independently of Agent claims;
- parent and child crashes resume from durable state without restarting the
  goal or discarding completed outputs;
- controlled replanning preserves history and cannot expand scope or authority
  silently;
- Research Assistant can use the generic runtime without generic code knowing
  research-specific search or evidence semantics.
