# Workflow Runtime and Autonomous Node Design

**Status:** needs final human review after three independent review iterations

**Date:** 2026-07-12

**Scope:** first implementation slice of Agent-owned Workflows

## Summary

Modi Harness will add a small Workflow Runtime above the existing execution
stack. A Workflow defines the stable business path. Each Node chooses one of
two execution strategies:

- `operation`: the system already knows which registered Operation to run;
- `autonomous`: the system defines a goal and completion contract, while the
  existing `AgentLoop` plans and executes the steps needed to satisfy it.

The design introduces only three author-facing configuration objects:

```text
Agent
  └── workflows: Workflow[]

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

It adds one Workflow control operation, `complete_node`. An autonomous Agent
may propose that the active Node is complete, but only the Harness may validate
and commit that completion.

This is not a second agent protocol and not a dynamic graph compiler. The
existing `AgentLoop`, `TaskPlan`, `Brain`, `StepDecision`, `RuntimeOperation`,
`StepRecord`, policy, checkpoint, trace, and output paths remain authoritative.

## Context

The current Brain-Agent Loop is good at open-ended work. It can clarify intent,
create and revise a `TaskPlan`, choose one `StepDecision` at a time, execute
`RuntimeOperation`s, wait for human input, and preserve `StepRecord`s.

It is deliberately not a workflow language. Encoding a stable business process
as fast Brain rules or slow-model instructions causes several problems:

- the main business path is distributed across prompts, rule providers, and
  continuation logic;
- deterministic transitions depend on repeated planning decisions;
- recovery rules gradually become an implicit workflow engine;
- operators cannot inspect the stable path independently of a model run;
- checkpoint recovery must infer which business phase the Agent intended.

At the opposite extreme, letting the Agent generate and compile temporary
nodes and edges would require a dynamic graph schema, compiler, graph versions,
checkpoint migration, and state mapping between a generated subgraph and the
main graph.

The required middle ground is:

> **Workflow owns the stable business path. AgentLoop autonomously solves one
> bounded composite Node.**

An autonomous Node has a runtime-shaped internal path, but that path is already
represented by:

```text
TaskPlan + StepRecords + RuntimeOperations
```

It does not need to become another Workflow Graph object.

## Goals

The first version must:

1. make an Agent's stable business paths explicit and loadable;
2. preserve real multi-step planning inside bounded autonomous Nodes;
3. reuse the current AgentLoop and operation execution path;
4. make Workflow progress deterministic, durable, resumable, and traceable;
5. ensure the model can propose Node completion but cannot commit it;
6. keep permissions, alignment, policy, action execution, checkpoint, trace,
   and output validation beneath both execution strategies;
7. preserve standalone AgentLoop behavior for Agents without Workflows;
8. provide enough contracts to implement and test the first slice without
   inventing additional node kinds or control systems.

## Non-goals

The first version does not include:

- dynamic generation or compilation of Workflow subgraphs;
- a visual Workflow editor;
- a general conditional expression language;
- arbitrary cross-Workflow jumps;
- nested Workflows;
- parallel or fan-out Nodes;
- compensation transactions or sagas;
- a separate fallback runtime;
- a separate reasoning executor;
- model-selected Workflow routing;
- live migration of an in-progress run to a changed Workflow definition;
- reinterpreting existing `stages.toml` or fast rules as Workflow definitions.

There are no author-facing `Edge`, `Fallback`, `DynamicSubgraph`,
`ReasoningNode`, or `HumanNode` objects. Runtime state and audit records are
implementation records, not additional configuration concepts.

## Considered approaches

### A. Workflow with AgentLoop inside autonomous Nodes — selected

```text
WorkflowRuntime
  -> autonomous Node
       -> AgentLoop
            -> Brain
            -> StepDecision
            -> RuntimeOperation
            -> StepRecord
```

This preserves visible, revisable planning while reusing the current execution,
checkpoint, human-interaction, and audit contracts. The Workflow definition
contains only the stable business path.

### B. Dynamically compile an Agent plan into a temporary Workflow

This makes the plan look uniform with the main Workflow, but immediately adds
dynamic graph validation, versions, changing edges, checkpoint migration, and
main/subgraph state mapping. The first version does not need those capabilities.

### C. Remove TaskPlan and choose only the next Step

This is smaller at runtime but makes the plan invisible and harder to evaluate
or revise. The current Task Plan protocol already exists, so removing it would
discard useful structure without simplifying the stable Workflow path.

## Design principles

### Stable path outside, adaptive path inside

The Workflow declares which business Nodes exist and how validated outcomes
move between them. The autonomous Node declares a goal, not a solution path.

### One control owner at each level

- `WorkflowRuntime` owns the active Node and inter-Node transition.
- `AgentLoop` owns semantic steps inside the active autonomous Node.
- `Brain` proposes one next `StepDecision`.
- the operation gateway owns real execution.
- Harness validation owns Node completion.

No layer may mutate the state owned by the layer above it.

### Execution strategy is not implementation type

`Node.execution` has exactly two values: `operation` and `autonomous`.

Deterministic code, a tool call, structured LLM reasoning, human interaction,
and output validation are all possible Operation implementations. They are not
Node execution kinds. This prevents `deterministic`, `reasoning`, `human`, or
`validation` from growing separate executors and recovery semantics.

### Model proposals are not state transitions

The model may propose a `RuntimeOperation`, including `complete_node`. It may
not directly update `WorkflowState`, choose an undeclared transition target, or
finalize the Workflow.

## Author-facing object model

### Agent

`AgentProfile` gains a resolved `workflows: list[Workflow]` field. An empty list
preserves the current standalone AgentLoop path.

The logical contract is independent of the source serialization. In the first
package format, an Agent declares Workflow files under:

```text
agents/<agent-name>/
  agent.toml
  workflows/
    <workflow-id>.yaml
```

The loader parses the files at Agent load time and stores validated canonical
objects on the Agent profile. `PyYAML` and `jsonschema` are already project
dependencies. File order has no semantic meaning; Workflow IDs must be unique.

### Workflow

The minimal Workflow contract is:

```yaml
id: complaint_resolution
input_schema:
  type: object
  required: [complaint]
  properties:
    complaint:
      type: object
start_node: classify_complaint
nodes:
  - id: classify_complaint
    execution: operation
    operation: classify_complaint
    completion: {}
    transitions:
      completed: $complete
```

Fields:

- `id`: stable Agent-local identifier;
- `input_schema`: JSON Schema applied to the caller's domain input before the
  run starts;
- `start_node`: ID of the first Node;
- `nodes`: non-empty list of Node definitions.

The Workflow schema is closed. These four names are its complete first-version
field set; any unknown field fails loading.

Workflow definition versions are not author-managed objects in the first
version. The loader computes a canonical definition fingerprint. A new run pins
that fingerprint in durable state.

### Node

Common Node fields are:

```yaml
id: classify_complaint
execution: operation
operation: classify_complaint
inputs: {}
completion: {}
transitions:
  completed: $complete
```

- `id`: unique within the Workflow;
- `execution`: exactly `operation` or `autonomous`;
- `inputs`: optional literal values or constrained references resolved when the
  Node starts;
- `completion`: the output and semantic completion contract;
- `transitions`: an event-name-to-target map.

Execution-specific fields do not create new object kinds:

- an `operation` Node requires `operation`;
- an `autonomous` Node requires `goal` and may narrow `capabilities` and
  `limits`.

The loader rejects fields belonging to the other execution strategy. For
example, an `operation` Node cannot declare `goal`, and an `autonomous` Node
cannot declare `operation`.

All author-facing schemas are closed:

- common Node fields: `id`, `execution`, `inputs`, `completion`,
  `transitions`;
- operation-only field: `operation`;
- autonomous-only fields: `goal`, `capabilities`, `limits`;
- completion fields: `output_schema`, `validator`, `require`;
- capabilities fields: `tools`;
- limits fields: `max_steps`;
- reference fields: `$ref` only.

Unknown fields are errors rather than ignored metadata. In particular,
`edges`, `fallback`, `dynamic_subgraph`, `reasoning`, `human`, and alternate
transition fields cannot be smuggled into a loaded definition.

## Canonical configuration

### Operation Node

```yaml
- id: classify_complaint
  execution: operation
  operation: classify_complaint
  inputs:
    complaint:
      $ref: "#/workflow/input/complaint"
  completion:
    output_schema:
      type: object
      required: [classification]
  transitions:
    simple: resolve_directly
    complex: investigate
    failed: manual_review
```

The registered Operation may be ordinary code, a tool-backed action,
model-backed structured reasoning, an interaction, or validation. The Workflow
Runtime only observes its normalized result.

### Autonomous Node

```yaml
- id: investigate
  execution: autonomous
  goal: Find the complaint's direct cause and responsibility owner.
  inputs:
    complaint:
      $ref: "#/workflow/input/complaint"
    classification:
      $ref: "#/nodes/classify_complaint/output"
  completion:
    output_schema:
      type: object
      required: [root_cause, evidence, confidence]
      properties:
        root_cause: {type: string}
        evidence: {type: array}
        confidence: {type: number}
    validator: validate_investigation
    require: [root_cause, evidence, confidence]
  capabilities:
    tools: [get_order, search_messages, read_contract]
  limits:
    max_steps: 20
  transitions:
    completed: review_investigation
    failed: manual_review
```

The Agent may decide how to decompose the goal, which allowed tools to call,
when to revise its plan, and when to ask for information. It cannot change the
goal, input snapshot, completion contract, capability boundary, limits, or
declared transitions.

### Terminal targets

`transitions` targets either another declared Node ID or one of two reserved
targets:

```text
$complete   successfully finish the Workflow
$fail       finish the Workflow as failed
```

These are targets, not Nodes. They do not introduce a terminal Node type.

The output of the Node that transitions to `$complete` becomes the candidate
Workflow output and passes through the Agent's existing Output Controller
before the Workflow is committed as completed.

Only an event emitted after successful Node completion validation may target
`$complete`: `completed` or a named successful Operation outcome. A `failed`
transition may never target `$complete`. In particular, an autonomous Node
cannot finish the Workflow successfully without an accepted `complete_node`.

A terminal transition is not an intermediate durable state. The source attempt
result, selected `$complete` or `$fail` target, final output or error, Workflow
revision, and Workflow terminal status are committed in one transaction. There
is no externally visible `finalizing` state in the first version.

## Input binding

The first version supports only literals and RFC 6901-style JSON Pointer
references under a fixed runtime root:

```text
#/workflow/input/...       immutable validated Workflow input
#/nodes/<node-id>/output   committed output of a completed Node
```

It does not support operators, predicates, interpolation, functions, or access
to uncommitted state. A mapping value containing only `$ref` is a reference;
all other JSON values are literals.

Before resolving inputs, the runtime durably creates a Node attempt in
`resolving` state with its attempt number and idempotency scope. It then either
atomically stores the resolved snapshot and marks the attempt `running`, or
stores a structured resolution error, marks the attempt `failed`, and commits
the declared failed transition. Resume of a `resolving` attempt may rerun only
the pure reference-resolution step; it may not create a new attempt.

Missing references are runtime input-resolution failures. They emit the Node's
`failed` event; if no `failed` transition exists, the Workflow fails. The
loader may reject statically impossible references, but it must not pretend to
prove path-dependent data availability.

Inputs are resolved once per Node attempt and stored with that attempt. An
autonomous Agent sees the resolved snapshot and cannot mutate Workflow input or
another Node's committed output.

## Operation result and transition semantics

All Operation implementations normalize to the existing runtime operation
record plus these observable semantics:

```text
status: completed | waiting | failed
outcome: string | null
output: JSON value | null
error: structured error | null
```

This normalization is a runtime result, not a new author-facing configuration
object.

For an `operation` Node:

1. `waiting` keeps the same Node active and checkpoints the pending interaction
   or judgment; it does not select a transition.
2. `failed` selects `transitions.failed`.
3. `completed` validates the Node completion contract.
4. after validation, a non-empty `outcome` selects a transition with the same
   name;
5. if no outcome-specific transition exists, `transitions.completed` is the
   fallback;
6. if neither exists, the Workflow fails with `transition_not_declared`.

This supports a classifier returning `simple` or `complex` without adding a
condition expression language.

For an `autonomous` Node:

- only Harness acceptance of `complete_node` emits `completed`;
- terminal execution failure or budget exhaustion emits `failed`;
- requests for information or judgment place the Workflow in `waiting` without
  leaving the Node.

The model cannot emit arbitrary transition labels for an autonomous Node.
An autonomous Node must declare `completed`; it may also declare `failed`, and
it may declare no other transition key. Reachability analysis uses only these
events. Operation Nodes may declare `completed`, `failed`, and Operation-owned
outcome labels; `waiting` is never a transition key.

## Runtime Operation adapter boundary

The repository currently has a `ToolRegistry` and a small set of internal
`RuntimeOperationProposal.kind` values; it does not yet have a general
Operation Registry. Workflow needs a name-to-existing-runtime adapter, not a
second execution gateway.

The first version adds an internal `RuntimeOperationAdapterRegistry`. This is
runtime wiring and not a fourth author-facing configuration object. Each
registered adapter has:

```text
id                       Node.operation lookup name
version                  explicit behavior-contract version
node_selectable          whether Workflow authors may use this ID
proposal_kind            tool | output_finalize | stage_transition |
                         memory_write | workflow_control
proposal_target          existing runtime target
input_schema             adapter input contract
output_schema            normalized result contract
required_capabilities    Agent capabilities required to invoke it
side_effect              whether execution may change external state
recovery_mode            provider_idempotent | gateway_claimed |
                         manual_reconciliation
build_proposal           resolved Node input -> RuntimeOperationProposal
normalize_result         existing runtime result -> Node result semantics
recover                  invocation context -> recovery observation
```

`recover` returns `not_started`, `running`, `succeeded`, `failed`, or
`unknown`, plus a result reference when one exists. `manual_reconciliation`
adapters return `unknown` for the ambiguous crash window and trigger the human
or adapter-specific reconciliation path.

Mapping rules are closed:

- a tool-backed adapter must point to a `ToolRegistry` entry and builds
  `RuntimeOperationProposal(kind="tool", target=<tool name>)`;
- model-backed structured reasoning, ordinary code, and human interaction use
  tool/protocol handlers already behind the Tool Gateway, not a direct Workflow
  executor;
- Harness internal adapters may target the existing `output_finalize`,
  `stage_transition`, or `memory_write` paths, but author visibility is an
  explicit adapter decision and defaults to false;
- every `workflow_control` adapter and every adapter targeting the standalone
  `output_finalize` path has `node_selectable=false`;
- `complete_node` is the only `workflow_control` adapter in the first version
  and is exposed only by active autonomous embedded scope;
- an Operation Node may reference only `node_selectable=true` adapters;
- a business "finalize" Operation must be an ordinary selectable adapter that
  produces a candidate result artifact; only a `$complete` transition may run
  final output validation and commit the Workflow terminal state;
- unknown kinds or targets fail registration/loading.

The Workflow invocation ID and request hash travel in trusted gateway execution
context and `ToolCallProposal.metadata`, never as model-authored tool arguments.
An adapter that promises provider idempotency must accept that context and pass
the key to its provider. Backward-compatible legacy handlers may omit the
context only when the Operation has no external side effect or uses
`manual_reconciliation`.

`ToolSpec.idempotent` alone is not proof of durable provider idempotency. A
side-effecting legacy Tool defaults to `manual_reconciliation` until an adapter
explicitly implements provider-key propagation or gateway claim/query.
Adapter and completion-validator versions are mandatory stable identifiers;
maintainers must bump them whenever behavior changes in a way that can alter a
result or recovery decision.
Regardless of adapter type, execution still enters the existing
RuntimeOperation/Tool/Action Gateway path and its alignment, authority, policy,
trace, and checkpoint controls.

## Completion contract

Every loaded Node has a resolved completion contract. For an `operation` Node,
an omitted contract defaults to successful Operation completion with no extra
output constraints. In the first version, an autonomous Node must declare both
an `output_schema` and a trusted `validator`. Schema proves shape; the validator
proves that the result actually satisfies the Node goal and evidence contract.

Supported first-version fields are:

```yaml
completion:
  output_schema: {}       # JSON Schema, optional for operation Nodes
  validator: string       # registered trusted validator, optional
  require: [string]       # required top-level output fields, optional shorthand
```

`require` is a convenience constraint, not an expression language. When both
`require` and `output_schema.required` exist, the required fields are the
union. If `require` is present without `output_schema`, the loader synthesizes
an object schema. If a declared `output_schema` does not constrain the instance
to an object, using `require` is a load error.

### JSON Schema profile

All Workflow input and Node output schemas use a fixed, deterministic profile:

- JSON Schema Draft 2020-12;
- `$ref` may resolve only to a fragment in the same schema document, normally
  under `$defs`;
- network URLs, file paths, and an implicit resolver registry are forbidden;
- recursive references are rejected in the first version;
- `format` is not supported in the first version; any use is a load error
  rather than an annotation whose behavior varies by checker installation;
- canonical schema size, schema nesting, instance size, and instance nesting
  have Harness defaults of 64 KiB, 32 levels, 1 MiB, and 64 levels
  respectively; deployment settings may tighten but not loosen those safety
  ceilings in the first version.

The same validator implementation and profile are used at load, initial input
validation, Node completion, resume, and terminal output validation. Schema
evaluation never performs network or filesystem I/O.

A completion validator is a trusted, side-effect-free Harness callable. It
receives the immutable Node goal, resolved inputs, candidate output, evidence
references, and relevant recorded operation results. It returns:

```text
valid: bool
reason: string
repairable: bool
details: JSON value | null
```

Validator exceptions are Harness failures, never successful completion. A
validator that needs an external side effect is incorrectly modeled and must be
split into an Operation followed by a pure completion validator.

## `complete_node`

### Purpose

`complete_node` is the only new Workflow control operation. It lets the Brain
propose that the active autonomous Node has been solved.

It is represented through the existing `RuntimeOperationProposal` path with a
narrow `workflow_control` operation kind and target `complete_node`. It is an
internal protocol capability, not a user-defined business tool.

Consistent with the existing Brain-Agent Loop contract, Brain does not generate
new business content inside this proposal. `complete_node` does not accept an
inline candidate output. It references one immutable, committed result artifact
whose payload contains the exact candidate output, evidence references, and
provenance. If several sources must be synthesized, the Agent first invokes a
domain Operation that materializes that result artifact. The proposal then asks
Harness to accept the recorded artifact as the Node result.

```text
Brain proposes complete_node
  -> Loop validates StepDecision
  -> Harness validates active Workflow scope
  -> validate output schema
  -> run completion validator
  -> verify plan/evidence/effect closure
  -> validate declared transition
  -> atomically commit Node output and transition
```

### Proposal arguments

```yaml
kind: workflow_control
target: complete_node
arguments:
  protocol_version: 1
  workflow_id: complaint_resolution
  node_id: investigate
  node_attempt: 1
  result_ref: artifact://operation/01ABC
  idempotency_key: 01J...
```

The Workflow Runtime also injects server-authoritative Workflow run scope. Any
caller-supplied ID or attempt that does not exactly match the active scope is
rejected. The Agent cannot complete a different Node or an earlier attempt.

### Validation sequence

The Harness checks, in order:

1. the Workflow run is active on the same autonomous Node, with any prior wait
   formally resumed;
2. the `complete_node` proposal belongs to the active AgentLoop and Node
   attempt;
3. no other completion is being committed for that attempt;
4. `result_ref` resolves to an immutable committed artifact from the active
   Node attempt or an explicitly bound prior Node output;
5. the runtime reads the candidate output and evidence/provenance from that
   artifact and verifies its content hash; Brain-supplied content is never used;
6. candidate output satisfies the effective JSON Schema and required fields;
7. every evidence reference exists, is readable by this run, has the declared
   provenance, and is relevant according to the trusted completion validator;
8. the completion validator passes;
9. a non-empty scoped `TaskPlan` exists and every item is completed; no item is
   pending, in progress, or blocked;
10. no RuntimeOperation, approval, interaction, or judgment remains pending;
11. all consequential side effects are recorded and have passed the existing
   Action Gateway and policy path;
12. the `completed` transition target exists;
13. if the target is `$complete`, evaluate the candidate with the existing
    Output Controller; validation continues, review persists a pending
    completion and waits, and rejection follows the closed output mapping;
14. for a non-terminal target, the completion record, Node output, transition,
    new current Node, and Workflow revision are committed atomically;
15. for `$complete` or `$fail`, the completion/attempt result, Node output,
    terminal transition, final output or error, Workflow revision, and terminal
    Workflow status are committed atomically.

The completion validator may mark a semantic failure as repairable. Schema,
missing-task, missing-evidence, and repairable validator failures return a
structured rejection to the AgentLoop and allow it to continue within its
remaining budget. Scope mismatch, stale attempt, forged evidence, or violated
authority boundaries are non-repairable integrity failures: they mark the Node
attempt failed and terminate the Workflow directly rather than following a
business `failed` transition.

The canonical completion-request hash covers protocol version, Workflow/Node
scope, attempt, `result_ref`, resolved result-artifact content hash, and all
other arguments. Repeated proposals with the same idempotency key and complete
request hash return the original result. Reusing the key for any different
request fails with `completion_idempotency_conflict`. A different completion
proposal after the Node has transitioned is rejected as stale and cannot mutate
Workflow state.

## Runtime architecture

```text
Agent
  -> WorkflowRouter
  -> WorkflowRuntime
       |- operation Node
       |    -> RuntimeOperation
       |
       `- autonomous Node
            -> AgentLoop
                 -> TaskPlan
                 -> Brain
                 -> StepDecision
                 -> RuntimeOperation
                 -> StepRecord
                 -> complete_node

All operations
  -> Alignment / Action Gateway / Policy
  -> Checkpoint / Trace / Output Controller
```

`WorkflowRouter` and `WorkflowRuntime` are runtime services, not author-facing
configuration objects.

### Workflow selection

The first version has no model router. Selection is deterministic:

1. if the caller supplies `workflow_id`, select that Workflow or return
   `workflow_not_found`;
2. if no ID is supplied and the Agent has exactly one Workflow, select it;
3. if the Agent has multiple Workflows and no ID is supplied, reject the run
   with `workflow_required` and list available IDs;
4. if the Agent has no Workflows, use the current standalone AgentLoop path.

The control field is separate from domain input. Python session methods gain an
optional `workflow_id` keyword, and CLI/API adapters map their control envelope
to it before applying `Workflow.input_schema` to the task payload.

### Workflow Runtime responsibilities

The Workflow Runtime:

- validates and pins the Workflow definition;
- owns active Node selection and Node attempt counters;
- resolves immutable Node inputs;
- invokes an Operation Node or starts/resumes a scoped AgentLoop;
- handles normalized operation events;
- validates and commits `complete_node`;
- records Node outputs and transitions;
- checkpoints every durable boundary;
- invokes final output validation at `$complete`;
- exposes progress and failure state to adapters.

It does not plan autonomous steps, execute tool internals, make policy
decisions, or interpret business content.

### Operation Node execution

```text
enter Node
  -> create and persist Node attempt(status=resolving)
  -> resolve inputs
  -> atomically persist input snapshot and mark attempt running
  -> propose/invoke configured RuntimeOperation
  -> existing alignment, action, policy, trace path
  -> waiting: checkpoint and remain on Node
  -> failed: take failed transition or fail Workflow
  -> completed: validate completion and choose declared outcome
  -> atomically store output and move to target
```

An Operation that needs human input uses the existing interaction or judgment
protocol. Resume continues the same operation attempt; it does not re-enter the
Node as a new attempt unless the Operation has definitively failed and a
declared Workflow transition returns to it.

For an Operation Node, Workflow Runtime synthesizes the
`RuntimeOperationProposal` from the trusted Node definition and resolved input;
Brain does not choose its target. The proposal carries Node-attempt lineage and
enters the same operation gateway as a Brain-proposed operation.

Trusted configuration is not an authorization bypass. At load time, the
configured Operation must belong to the Agent's effective registered capability
set. At every invocation and resume, the gateway re-evaluates current Agent
visibility, Workflow/Node scope, intent alignment, authority, and policy.
Approval/review pauses the Node; a final denial follows the authoritative
failure matrix.

### Autonomous Node execution

```text
enter Node
  -> create and persist Node attempt(status=resolving)
  -> resolve inputs
  -> atomically persist input snapshot and scoped AgentLoop state
  -> expose Node goal, inputs, completion contract, and narrowed capabilities
  -> Brain creates/revises TaskPlan and proposes one StepDecision at a time
  -> RuntimeOperations use existing execution path
  -> interactions/judgments checkpoint and wait inside the Node
  -> Brain proposes complete_node
  -> Harness accepts and transitions, or rejects and returns feedback
```

The AgentLoop is embedded, not globally repurposed. Its standalone entry point
continues to work. Embedded Loop state is bound to:

```text
workflow_run_id + workflow_id + node_id + node_attempt
```

The binding appears in `StepContext` and every `StepRecord` so steps from one
Node cannot be replayed as evidence for another.

### Embedded AgentLoop state mapping

Embedded mode is explicit Loop scope, not a prompt convention. It overrides
standalone terminal behavior while leaving standalone AgentLoop unchanged.

| Embedded Loop event/request | Loop state | Node/Workflow action |
| --- | --- | --- |
| Valid ordinary Step requests `continue` | `active` | remain on active attempt |
| Ask, judgment, approval, or reconciliation is pending | `waiting` | atomically mark attempt and Workflow `waiting` |
| Pending input/judgment is validly resumed | `active` | atomically restore attempt and Workflow `active` |
| Brain proposes `step_kind=finish` without accepted `complete_node` | `active` | record `embedded_finish_not_allowed`, return structured feedback |
| Brain requests bare `continuation=stop` | `active` | record `embedded_stop_not_allowed`, return structured feedback |
| Brain proposes `output_finalize` | `active` | record `embedded_output_finalize_not_allowed`, return structured feedback |
| Loop produces `cancel` without an authorized Workflow cancellation event | `active` | record `embedded_cancel_not_allowed`, return structured feedback |
| `complete_node` is repairably rejected | `active` | record result and completion feedback; remain on Node |
| `complete_node` is accepted for a non-terminal transition | `completed` | atomically complete Loop/attempt and enter successor Node |
| Terminal completion needs output review | `waiting` | keep a pending completion bound to candidate hash and wait for judgment |
| Terminal completion is validated or its bound review is approved | `completed` | atomically complete Loop/attempt/Workflow and commit final output |
| Accepted completion targets `$fail` | `completed` | atomically complete Loop/attempt and fail Workflow as declared |
| Autonomous budget is exhausted | `failed` | atomically fail Loop/attempt and emit Node `failed` |
| Unrecoverable Loop/Node failure | `failed` | atomically fail Loop/attempt, then follow matrix path |
| Workflow cancellation becomes terminal | `cancelled` | atomically cancel Loop, attempt, and Workflow |

In embedded mode, current standalone behavior that treats `finish`, bare
`stop`, `output_finalize`, an unscoped `cancel`, or step-limit exhaustion as run
completion/waiting must not run. The embedding adapter applies this table
before the standalone continuation verdict. A Loop `cancel` verdict is valid
only as the projection of an already-authorized Workflow cancellation event.

Every Brain decision that creates a `StepRecord`, including a rejected
embedded terminal request and each `complete_node` proposal, consumes one Node
step. Merely resuming an already-recorded interaction or output review does not.
If structured rejection consumes the remaining budget, the Loop immediately
takes the budget-exhaustion row rather than asking the user what to do next.
An output review opened by the last allowed Step may still be approved and
committed; if that review is rejected, no further planning Step is available
and the Node follows budget exhaustion.

### Plan semantics inside an autonomous Node

Each autonomous Node always enables a scoped Task Plan protocol, regardless of
the Agent's standalone `task_protocol` setting. A non-empty plan must exist
before the first consequential business Operation. Clarification may occur
before planning, but `TaskPlan is None` or an empty plan is a repairable
`complete_node` rejection.

The Agent may revise the plan as results arrive. This requires a narrow
extension to the current Task Plan protocol:

- completed and in-progress items keep their IDs, status, and recorded history;
- revision may add, remove, or reorder pending items;
- a blocked item may return to pending only with a new input/result reference
  explaining what unblocked it;
- revision cannot mark an item completed or rewrite a completed summary;
- every revision increments `TaskPlan.version` and remains append-only in
  history;
- task completion still occurs only through the existing task-completion
  protocol operation.

- A plan is not a Workflow and has no durable inter-Node edges.
- A plan is discarded from active control when the Node transitions, but its
  final form remains in trace/checkpoint history.
- Returning to the same Node through a declared Workflow transition creates a
  new Node attempt and a new plan, while preserving prior attempt records.
- `complete_node` is invalid unless every Task Plan item is completed.

### Capability and budget narrowing

An autonomous Node's effective tools are the intersection of:

```text
Agent tools
intersect Node capabilities.tools (when present)
intersect active Skill restrictions
intersect policy visibility
```

An absent Node tool list inherits the Agent's visible tools. An empty list
allows no business tools but still exposes required internal protocol
operations such as Task Plan control and `complete_node`.

Node limits can only narrow runtime limits. In the first version the only
author-facing Node limit is `max_steps`. The effective limit is the minimum of
the Node limit, the Agent Loop limit, and the remaining run budget.

Workflow-level transition count is bounded by a Session/Harness runtime policy,
not another Workflow config field. Explicit cycles are allowed, but exceeding
the runtime transition cap fails with `workflow_transition_limit_exceeded`.

### Output finalization

`complete_node` completes a Node; it is not `output_finalize`.

Within a Workflow run, an embedded AgentLoop cannot independently finish the
entire Agent run. Only a declared transition to `$complete` can do so. Before
committing that transition, the Workflow Runtime passes the candidate output
through the existing Agent Output Controller.

For an autonomous terminal Node, a repairable Output Controller rejection is
returned to its AgentLoop as completion feedback. For an operation terminal
Node, output validation failure emits `failed` and follows the declared failed
transition when one exists; otherwise the Workflow fails.

The mapping from the existing `OutputValidationResult` is closed:

| Output status/result | Workflow action |
| --- | --- |
| `validated` or `final` | perform the atomic `$complete` commit |
| `needs_review` | persist a candidate-bound judgment and set Loop/attempt/Workflow to `waiting` |
| `rejected` with only ordinary contract issues | autonomous: repairable completion feedback; operation: emit `failed` |
| `rejected` with any integrity issue or unknown error code | fail Workflow directly |

First-version ordinary issue codes are `schema.unparseable_json`,
`schema.missing_field`, `schema.type_mismatch`, `citation.missing`, and
`risk_label.missing`. Integrity codes are `forbidden_content`,
`prompt_injection_warning`, `security_authorization_missing`, and
`denied_side_effect_claimed`. An unclassified future error code is treated as
integrity-sensitive until the mapping is deliberately updated. Warning/info
issues do not alter the status selected by Output Controller.

For `needs_review`, the runtime writes a durable `PendingJudgment` bound to
Workflow revision, Node attempt, `result_ref`, candidate content hash, output
contract hash, and reviewer authority. No Node output or terminal transition is
committed yet. Resume must match the pending judgment and hashes and must
re-run schema/integrity validation. Approval satisfies only the review
obligation for that exact candidate and then performs the atomic terminal
commit. A changed hash is `output_review_integrity_error` and terminates
directly. Rejection returns revision feedback to an autonomous Loop or emits
the Operation Node's `failed` event; cancellation follows Workflow cancellation
semantics.

## Durable state and audit records

The author-facing object model stays minimal, but correct recovery requires
internal durable records.

### Workflow state

The persisted Workflow state contains at least:

```text
workflow_run_id
run_id
agent_name
workflow_id
definition_fingerprint
execution_contract_fingerprint
status: active | waiting | completed | failed | cancelled
revision
current_node_id
current_node_attempt
workflow_input
node_outputs
active_loop_id
pending_operation_ref
pending_completion_ref
transition_count
last_event_id
effect_reconciliation
cancellation_pending
failure
```

`node_outputs` is keyed by Node ID and attempt. The simple input reference
`#/nodes/<id>/output` resolves to the latest successfully committed attempt.
Earlier attempts remain available in audit storage but are not exposed through
the first-version binding language.

### Node attempt and transition records

For each attempt, trace/checkpoint storage records:

- Node ID, execution strategy, and attempt number;
- resolved input snapshot and its hash;
- start/end timestamps and status;
- embedded Loop ID when autonomous;
- operation, completion, output, and error references;
- selected event and declared transition target;
- Workflow revision before and after commit.

Attempt status is one of `resolving`, `running`, `waiting`, `completed`,
`failed`, or `cancelled`.

These records are append-only. `WorkflowState` is the current projection.
Existing `StepRecord`s remain the detailed progress spine inside an autonomous
attempt.

### Definition and execution-contract pinning

The loader canonicalizes a Workflow and computes its definition fingerprint,
but YAML alone does not determine execution. At run creation, the runtime also
builds an immutable execution-contract snapshot containing:

- canonical Workflow definition and schema-profile version;
- every referenced adapter's ID, explicit version, visibility, proposal
  kind/target, input/output schemas, required capabilities, side-effect flag,
  and recovery mode;
- every referenced completion validator's ID and explicit version;
- the canonical Agent OutputContract;
- the effective capability upper bound visible to this run at creation;
- effective Node/Loop step limits, Workflow transition limit, output repair
  budget, and protocol versions that affect control flow.

The canonical snapshot is hashed as `execution_contract_fingerprint` and stored
with the snapshot in durable run metadata. Resume recomputes it before applying
an event or dispatching an Operation. A Workflow-only change returns
`workflow_definition_changed`; any other mismatch returns
`workflow_execution_contract_changed`. Both preserve the checkpoint and refuse
automatic continuation. The first version does not guess a migration or
execute against a mixed contract.

Current policy, authority, and Agent configuration may tighten access after a
run starts, but effective capability is always:

```text
creation-time capability upper bound
intersect current Agent/Skill visibility
intersect current authority and policy
```

It can never expand beyond the creation snapshot, even if a restarted process
loads broader Agent permissions. A later explicit migration mechanism may map
old state to a reviewed new execution contract.

## Checkpoint, idempotency, and concurrency

Durable boundaries are:

- Workflow created and start Node selected;
- Node attempt created in `resolving` state;
- Node input snapshot or resolution failure committed;
- every existing AgentLoop Step boundary;
- interaction, judgment, or approval entered/resolved;
- Operation invocation prepared before dispatch;
- RuntimeOperation result recorded;
- completion attempt recorded;
- Node output and transition atomically committed;
- for a terminal target, source attempt, terminal transition, terminal result,
  and Workflow status committed together.

### Operation dispatch and external effects

Before dispatch, the runtime durably writes an Operation invocation record
containing a stable invocation ID, Workflow/Node scope, target, canonical
argument hash, authority context, recovery mode, and status `prepared`.
Invocation status is closed:

```text
prepared -> dispatching -> terminal
                      `-> reconciliation_required -> terminal
prepared -> terminal(cancelled before dispatch)
```

Dispatch may begin only after `prepared` is checkpointed. The dispatcher then
uses Workflow revision/CAS to atomically claim `prepared -> dispatching` while
rechecking that the Workflow/attempt is active and cancellation is not pending.
It may call the provider only after that claim succeeds. A failed/stale claim
must not dispatch.

Cancellation can atomically turn a `prepared` invocation into terminal
cancelled and then cancel the Workflow. Once an invocation is `dispatching`, it
is conservatively considered possibly in flight; cancellation sets
`cancellation_pending`, changes the invocation to
`reconciliation_required`, and waits. This closes the race where a dispatcher
could act after terminal cancellation.

The Runtime Operation adapter registry declares one of three recovery modes as
adapter metadata, not Node configuration:

- `provider_idempotent`: the provider accepts the invocation ID as an
  idempotency key and durably returns the same result for the same request;
- `gateway_claimed`: the Action Gateway can durably claim, execute, and query
  the effect under the invocation ID;
- `manual_reconciliation`: the provider cannot prove safe automatic replay.

For the first two modes, the Action Gateway/provider must bind the invocation
ID to the exact scope, target, and argument hash. Reuse with the same payload
returns the durable claim/result; reuse with different scope or payload fails
with `operation_idempotency_conflict`.

The difficult crash window is “external effect may have succeeded, but the
Harness result was not checkpointed.” Recovery must query the durable provider
or gateway claim before considering redispatch. Redispatch is allowed only when
the same provider idempotency key makes it safe. A
`manual_reconciliation` Operation instead places the Workflow in waiting with
`effect_reconciliation_required`; it is never automatically issued again. An
authorized human or Operation-specific reconciliation adapter records whether
the effect occurred and supplies the result needed to continue or fail.

The runtime therefore does not claim universal exactly-once delivery. It
guarantees that idempotent/claimed effects are not duplicated by recovery and
that uncertain non-idempotent effects are never blindly replayed.

Adapter recovery mode also constrains the existing Tool Gateway retry policy;
`ToolSpec.retry` and `ToolSpec.idempotent` may never broaden it:

- `manual_reconciliation + side_effect` forces one provider dispatch. Timeout,
  connection uncertainty, or an exception that may leave the handler running
  immediately enters `reconciliation_required`; the gateway must not start an
  overlapping or later automatic attempt;
- `provider_idempotent` may retry only with the identical invocation ID and
  request hash, relying on provider-side deduplication even if an earlier
  handler returns late;
- `gateway_claimed` may retry execution only when its durable claim/query
  contract proves that doing so is safe; otherwise it queries or reconciles;
- a side-effect-free Operation may use the narrower effective Tool retry
  policy because replay cannot duplicate an external effect.

The gateway records effective retry policy and each provider attempt beneath
the single Workflow invocation ID. A handler timeout never implies that the
handler stopped.

### Event receipts

Every input, resume, approval, judgment, Operation callback, and completion
event is first stored in a durable receipt ledger with event ID, type,
scope, payload hash, and internal status `received`. `last_event_id` is only a
projection; deduplication consults the ledger.

Event handling is two-phase but cannot lose the event:

1. durably insert `received` before applying it;
2. evaluate it against the pinned definition, pending reference, and expected
   Workflow revision;
3. in one transaction, commit all resulting Workflow/Loop/attempt changes, the
   new Workflow revision, the recorded event result, and receipt status
   `applied`.

A crash after step 1 resumes the same `received` event and continues step 2; it
does not treat receipt existence as successful application. A crash after step
3 returns the recorded result. Stale and rejected events are also finalized as
`applied` with their non-mutating result, so they do not remain ambiguous.

The same applied event ID and payload returns the recorded result. The same ID
with a different payload or scope fails with `event_idempotency_conflict`. A
delayed or out-of-order event is accepted only when it matches the currently
pending event reference; otherwise it is applied as stale and cannot mutate
state.

Only one Node is active in a Workflow run. State commits use the Workflow
revision as an optimistic concurrency check. A stale resume, duplicated event,
or concurrent completion receives the previously committed result when its
idempotency key matches, or `workflow_revision_conflict` otherwise.

The runtime must never execute a successor Node before the predecessor's output
and transition are durably committed.

## Failure and waiting semantics

Waiting is not failure and does not select a transition. A Workflow is waiting
when its active Operation or embedded AgentLoop is waiting for:

- user input;
- human judgment;
- policy approval or review;
- an external resumable event.

Resume revalidates the active Workflow fingerprint, revision, Node attempt,
pending event ID, and authority before applying the response.

The following matrix is authoritative. A `failed` event means “use the declared
`transitions.failed`; if it is absent, fail the Workflow.” A direct Workflow
failure must not enter a business fallback because doing so would hide a
runtime, authority, or integrity defect.

| Source | Attempt state | Runtime action | Automatic retry | Error code |
| --- | --- | --- | --- | --- |
| Input reference or Node-input validation fails | `failed` | emit `failed` | no | `node_input_invalid` |
| Operation requests interaction, judgment, or approval | `waiting` | no transition; checkpoint | resume only | `operation_waiting` |
| Policy or authority denies an otherwise valid Operation | `failed` | emit `failed` | no | `operation_denied` |
| Operation returns `failed` | `failed` | emit `failed` | no | `operation_failed` |
| Operation returns `completed`, but Node schema or validator returns invalid | `failed` | record result, emit `failed`; do not re-execute Operation | no | `operation_completion_invalid` |
| Completion validator raises or returns an invalid validator result | `failed` | fail Workflow directly | no | `completion_validator_error` |
| Autonomous completion has a repairable schema, evidence, plan-closure, validator, or terminal output rejection | `running` | return structured feedback to AgentLoop | within remaining step budget | `node_completion_rejected` |
| Autonomous validator returns `valid=false, repairable=false` for a business-semantic reason | `failed` | emit `failed` | no | `node_completion_failed` |
| Completion has stale scope, forged evidence, authority mismatch, or another integrity violation | `failed` | fail Workflow directly | no | `node_completion_integrity_error` |
| Autonomous step budget is exhausted | `failed` | emit `failed` | no | `node_step_limit_exceeded` |
| Operation terminal output violates the ordinary Agent output contract | `failed` | emit `failed` | no | `workflow_output_invalid` |
| Any terminal output has an integrity/security violation | `failed` | fail Workflow directly | no | `workflow_output_integrity_error` |
| Workflow transition cap is exhausted | `failed` | fail Workflow directly | no | `workflow_transition_limit_exceeded` |
| Unexpected Harness exception | `failed` | fail Workflow directly and preserve maintainer trace | no | `workflow_runtime_error` |
| Declared transition target is `$fail` | preserve source attempt result | atomically fail Workflow | no | `workflow_declared_failure` |
| Authorized caller cancels with no in-flight/uncertain effect | `cancelled` | atomically cancel Workflow; no business transition | no | `workflow_cancelled` |
| Authorized caller cancels while an effect may be in flight | `waiting` | set `cancellation_pending`, require effect reconciliation | resume only | `effect_reconciliation_required` |
| Pending cancellation finishes reconciliation | `cancelled` | atomically cancel Loop/attempt/Workflow | no | `workflow_cancelled` |

Output Controller rejection is therefore contextual: an autonomous terminal
Node can repair an ordinary contract mismatch, an Operation Node can follow a
declared business failure path without repeating its successful Operation, and
an integrity/security rejection always terminates directly.

Failure classes are:

### Definition errors

Examples: duplicate IDs, missing start Node, invalid transition target,
execution-specific field conflict, invalid schema, unknown validator.

These fail Agent loading. No Workflow run starts.

### Node input errors

Examples: missing bound output or incompatible resolved input. These emit the
Node's `failed` event; absence of a failed transition fails the Workflow.

### Operation errors

Normalized failed Operation results follow `transitions.failed`. A waiting
Operation remains active. Unexpected Harness exceptions are recorded with
maintainer context and fail safely.

### Completion rejection

Repairable rejections return structured feedback to the embedded AgentLoop.
A non-repairable business-semantic validator result emits `failed`; authority,
scope, validator-execution, and integrity failures terminate directly as shown
in the matrix.

### Budget exhaustion

Autonomous `max_steps` exhaustion emits `failed`. Workflow transition-cap
exhaustion fails the Workflow directly because taking another edge would violate
the runtime bound.

### Missing transition

An emitted event with no declared matching or fallback transition fails with
`transition_not_declared`. The runtime never asks the model to invent a target.

### Cancellation

Cancellation is a caller/runtime control action, not a Workflow event. An
authorized cancellation stops new dispatch immediately. If no Operation is in
flight, or its invocation is only `prepared`, the same CAS transaction prevents
dispatch, marks the invocation/attempt terminal, and cancels the Workflow. A
`dispatching` or `reconciliation_required` invocation is possibly in flight,
so the runtime first enters effect reconciliation; it must not claim
cancellation is final or retry the Operation until the effect is known. Once
reconciled, pending interactions and judgments are closed, the terminal
cancellation commit is written, and later resume events return the recorded
terminal result. Cancellation never follows `transitions.failed` and emits
`workflow.cancelled`.

## Static validation

Agent load rejects a Workflow when:

- Workflow ID is empty or duplicated within the Agent;
- `input_schema` or an output schema violates the fixed JSON Schema profile,
  including external/recursive references, unknown formats, or resource limits;
- any Workflow, Node, completion, capabilities, limits, or reference object has
  an unknown field;
- `nodes` is empty;
- Node IDs are empty, duplicated, or use the reserved `$` prefix;
- `start_node` does not exist;
- a transition target is neither a declared Node nor `$complete`/`$fail`;
- `transitions.failed` targets `$complete`;
- `execution` is not `operation` or `autonomous`;
- an operation Node lacks `operation` or declares autonomous-only fields;
- an autonomous Node lacks `goal`, declares `operation`, or lacks either its
  completion schema or trusted validator;
- an autonomous Node lacks `transitions.completed` or declares any transition
  key other than `completed` and `failed`;
- an operation Node declares `waiting` as a transition key;
- a referenced Operation or completion validator is not registered after Agent
  capabilities are assembled, or an Operation Node target is outside the
  Agent's effective capability set;
- a referenced adapter is not `node_selectable`, including every
  `workflow_control` or standalone `output_finalize` adapter;
- a referenced adapter or validator lacks an explicit behavior-contract
  version;
- a Node capability widens rather than narrows Agent capability;
- `max_steps` is not a positive integer;
- the Agent output contract or a declared Node output schema is itself invalid.

Unreachable Nodes are errors in the first version because they almost always
indicate a misspelled transition or dead configuration. Reachability traverses
only events the execution strategy can actually emit. Cycles are allowed and
bounded at runtime. Static validation does not attempt to prove termination or
path-dependent input availability.

## Security and authority boundaries

- Workflow definitions and completion validators are trusted Agent package
  configuration, not model output.
- Node goal and limits are immutable for an attempt.
- Node capabilities only narrow Agent capabilities.
- External observations remain untrusted context and cannot redefine Workflow
  structure or completion rules.
- All consequential business operations continue through Alignment, Action
  Gateway, governance, and policy.
- Internal protocol operations are allowlisted by active runtime state; merely
  knowing the name `complete_node` does not grant authority to invoke it.
- Evidence references are resolved by durable ID and ownership, never accepted
  as arbitrary paths supplied by the model.
- Workflow completion cannot bypass the Agent output contract.

## Tracing and observability

The first version adds trace events around existing operation and Step traces:

```text
workflow.started
workflow.node.entered
workflow.node.waiting
workflow.node.operation_completed
workflow.node.completion_proposed
workflow.node.completion_rejected
workflow.node.completed
workflow.transition.committed
workflow.effect_reconciliation_required
workflow.completed
workflow.failed
workflow.cancelled
```

Each event includes `run_id`, `workflow_run_id`, `workflow_id`, definition
fingerprint, Node ID, Node attempt, Workflow revision, and relevant operation,
Step, completion, or transition references.

Progress surfaces should show the stable Workflow Node separately from the
current autonomous Task Plan item. The CLI/API does not need a visual editor;
it only needs a readable projection such as:

```text
Workflow: complaint_resolution
Node: investigate (autonomous, attempt 1)
Task: Verify refund classification (2/4)
Status: waiting for contract access approval
```

## Compatibility and migration

### Agents without Workflows

No behavior changes. They enter the current standalone AgentLoop path.

### Existing Agent packages

`agent.md`, `agent.toml`, `brain.toml`, `rules.toml`, and `stages.toml` keep
their current meanings. The loader does not synthesize Workflows from stages or
fast rules.

### AgentLoop

The existing AgentLoop API remains usable. Workflow embedding adds an optional
scope to `StepContext`, `LoopState`, and lineage records; a missing Workflow
scope means standalone execution.

### Runtime operations

Existing Operation kinds keep their behavior. The only new control surface is
`RuntimeOperationProposal(kind="workflow_control", target="complete_node")`.
Embedded Loops may not use the standalone finalization path to bypass their
Workflow.

### First adopter

After the generic runtime contracts are stable, the research assistant is a
good first adopter:

```text
validate_sources (operation)
  -> investigate (autonomous)
  -> generate_digest (operation)
  -> judge_digest (operation)
  -> finalize (operation)
```

This migration is an integration proof, not part of the core object model.
Existing research-specific fast rules should be removed only when their stable
business decisions are represented by the Workflow and equivalent behavior is
covered by tests.

## Implementation slices

### Slice 1: definitions and loading

- add canonical `Workflow` and `Node` types;
- load `workflows/*.yaml` into `AgentProfile.workflows`;
- implement static validation and fingerprints;
- add the internal Runtime Operation adapter registry and strict mapping to
  ToolRegistry/internal RuntimeOperation targets;
- add execution-contract snapshots/fingerprints and capability upper-bound
  pinning;
- add deterministic Workflow selection controls.

### Slice 2: operation-only Workflow Runtime

- add durable Workflow state and attempt/transition records;
- execute operation Nodes through the existing operation gateway;
- add trusted invocation context, recovery-mode metadata, provider/gateway
  recovery query, and manual reconciliation;
- make invocation dispatch a revision/CAS claim and make adapter recovery mode
  constrain Tool Gateway retries;
- implement input binding, normalized outcomes, waiting, failure, terminal
  targets, checkpointing, and trace events.

### Slice 3: autonomous Node embedding

- bind an AgentLoop to a Workflow Node attempt;
- implement the embedded terminal-state mapping and scoped incremental Task
  Plan rules;
- narrow context, tools, and budgets;
- keep TaskPlan and StepRecord lineage per attempt;
- add the internal `complete_node` protocol operation;
- implement completion validation, feedback, idempotency, and atomic transition.

### Slice 4: adapters and recovery

- add Python/API/CLI `workflow_id` control input;
- expose Workflow and nested Task progress;
- integrate candidate-bound Output Controller review and resume;
- verify interaction, judgment, approval, restart, duplicate event, and changed
  definition behavior.

### Slice 5: research assistant pilot

- express its stable path as a Workflow;
- keep research investigation autonomous;
- move deterministic generation, judgment, and finalization to Operation Nodes;
- remove superseded lifecycle rules only after parity tests pass.

## Testing strategy

### Definition tests

- valid operation and autonomous Node definitions load;
- every static rejection listed above has a focused test;
- definition fingerprints are stable across mapping order and file order;
- duplicate Workflow IDs across files fail clearly;
- unsupported execution kinds such as `reasoning` and `human` are rejected.
- unknown fields at every closed-schema level are rejected, including `edges`,
  `fallback`, and `dynamic_subgraph`;
- autonomous Nodes reject unknown events and require `completed`;
- external or recursive schema references, unknown formats, excessive schema
  resources, and incompatible `require` usage are rejected.
- autonomous Nodes require both schema and validator, and any
  `transitions.failed: $complete` definition is rejected;
- Operation adapter IDs resolve to a closed existing runtime kind/target, and
  missing ToolRegistry targets or unsupported recovery contracts fail load.
- Operation Nodes reject non-selectable `workflow_control` and standalone
  `output_finalize` adapters;

### Router tests

- explicit ID selects the requested Workflow;
- one Workflow defaults safely;
- multiple Workflows without an ID return `workflow_required`;
- zero Workflows preserves standalone AgentLoop execution;
- domain input excludes the control envelope before schema validation.

### Operation Node tests

- literal and reference inputs resolve and are snapshotted;
- named outcomes select only declared transitions;
- `completed` and `failed` fallback behavior is deterministic;
- waiting/resume does not create a new attempt;
- missing bindings and missing transitions fail safely;
- terminal output runs through the Output Controller.
- an Operation target outside the Agent's effective capabilities is rejected at
  load and denied if authority changes before invoke/resume;
- tool-backed adapters synthesize `kind=tool`, propagate trusted invocation
  context/metadata, and never create a second handler path;
- a side-effecting legacy Tool without provider/gateway recovery support
  defaults to manual reconciliation;
- `manual_reconciliation + side_effect` overrides a configured Tool retry to
  one dispatch, while provider-idempotent retry preserves the same invocation
  key and gateway-claimed retry requires a safe claim result;
- Operation completion schema/validator rejection follows `failed` without
  repeating the successful Operation, while validator exceptions terminate
  directly.

### Autonomous Node tests

- AgentLoop receives immutable goal, inputs, completion contract, and scope;
- effective capabilities and budgets are correctly narrowed;
- plans can be revised without changing Workflow topology;
- human input and judgment resume the same Node attempt;
- AgentLoop cannot finalize the Workflow directly;
- returning to a Node creates a new attempt and plan.
- embedded `finish`, bare `stop`, and `output_finalize` are rejected with the
  defined structured errors and consume budget;
- step exhaustion fails the Loop/Node rather than entering standalone waiting;
- accepted completion, failure, waiting, and cancellation atomically map Loop,
  attempt, and Workflow state;
- scoped TaskPlan is mandatory and non-empty; incremental revision preserves
  completed/in-progress history and only changes allowed pending/blocked items.

### Completion tests

- only the active autonomous Node may call `complete_node`;
- schema, required fields, validator, evidence, plan closure, pending effects,
  and terminal output are all enforced;
- `result_ref` supplies the exact hash-verified candidate output; legal but
  unrelated evidence and mismatched artifact content are rejected;
- repairable rejection returns feedback without transitioning;
- non-repairable business rejection emits `failed`, while scope/integrity and
  validator-execution failures terminate directly;
- ordinary and integrity Output Controller rejections follow their distinct
  matrix paths;
- duplicate idempotency keys return the original result;
- the same idempotency key with different scope or payload conflicts;
- the same completion output with different evidence/result reference has a
  different full request hash and conflicts under a reused key;
- stale attempts and concurrent commits cannot alter state;
- a model-provided transition target is ignored/rejected.

### Recovery tests

- checkpoints resume before and after every durable boundary;
- an Operation is not repeated after its result was durably recorded;
- a crash after an external effect but before Harness result persistence is
  recovered through provider/gateway claim, or enters manual reconciliation;
- a `manual_reconciliation` Operation is never automatically replayed;
- a successor never starts before transition commit;
- `$complete` and `$fail` source attempt, transition, result, revision, and
  terminal Workflow status survive fault injection as one atomic commit;
- a crash before and after Node input resolution resumes the same `resolving`
  attempt and records exactly one resolution result/transition;
- changed definitions refuse resume without damaging the checkpoint;
- changing an adapter/validator version or contract, Agent OutputContract,
  capability upper bound, runtime limit, or protocol version changes the
  execution-contract fingerprint and refuses resume;
- broader post-restart permissions cannot expand the creation-time capability
  snapshot, while current policy may still tighten it;
- Workflow transition limits and autonomous step limits are distinct;
- pending approval/interaction/judgment survives process restart.
- cancellation with no in-flight effect commits atomically; cancellation with
  an uncertain effect reconciles before becoming terminal;
- cancellation and dispatcher CAS are tested in every `prepared`/`dispatching`
  interleaving so no provider call starts after terminal cancellation;
- a side-effecting manual-reconciliation Tool with an existing retry policy is
  faulted on timeout/late return and never overlaps or starts a second attempt;
- delayed, duplicated, conflicting, and out-of-order events are checked against
  the durable receipt ledger and cannot mutate the wrong revision.
- a crash after receipt status `received` but before state application resumes
  and atomically applies the event exactly once;
- Output `needs_review` survives restart; approval is bound to the exact
  candidate/contract hashes, rejection follows the execution-specific path,
  and tampering terminates directly;

### Integration and end-to-end tests

- an operation -> autonomous -> operation Workflow completes;
- an autonomous completion rejection leads to replanning and later success;
- an autonomous failure follows its declared manual-review Operation Node;
- all business operations retain Action Gateway, policy, trace, and checkpoint
  lineage;
- the research assistant pilot produces equivalent or better validated output
  without lifecycle fast rules acting as an implicit Workflow.

## Acceptance criteria

The first version is complete when:

1. an Agent can declare and load one or more statically validated Workflows;
2. routing follows the explicit/one/default rules without model involvement;
3. the runtime executes both `operation` and `autonomous` Nodes;
4. autonomous execution demonstrably reuses AgentLoop, TaskPlan,
   StepDecision, RuntimeOperation, and StepRecord;
5. only Harness-accepted `complete_node` can leave an autonomous Node through
   `completed`;
6. checkpoint/resume and duplicate delivery cannot repeat a committed
   transition; provider-idempotent or gateway-claimed effects cannot be
   duplicated, and uncertain non-idempotent effects require reconciliation
   instead of automatic replay;
7. resume requires the same pinned execution contract, and restarted runs can
   never gain capabilities beyond their creation-time snapshot;
8. both execution strategies retain existing permission, alignment, policy,
   trace, and output validation;
9. Agents with no Workflows remain behaviorally compatible;
10. unsupported execution kinds, dynamic topology, and undeclared transitions
   have no hidden runtime path;
11. the research assistant can serve as an end-to-end pilot without requiring
    dynamic subgraphs.

## Consequences

This design makes the stable business process inspectable and deterministic
while preserving the Agent's ability to plan where the solution path is not
known in advance. It creates one new orchestration layer, but that layer has a
narrow responsibility: select a declared Node, run one of two strategies, and
commit a declared transition.

The main cost is durable Workflow state and careful resume/idempotency logic.
That cost is necessary for any trustworthy business process and is smaller than
maintaining a dynamic graph compiler or continuing to grow implicit lifecycle
rules inside Brain.

The resulting boundary is:

```text
Workflow defines where the work may go.
Autonomous Node defines what must be solved.
AgentLoop decides how to solve it.
Harness decides whether it is complete.
```
