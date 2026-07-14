# Authoritative Runtime Types

Python definitions under `src/modi_harness/` are authoritative. This document
maps the supported public model without preserving removed contracts.

## Agent

`ModiAgent` is immutable and requires one or more unique Workflows. It owns its
identity, instruction, scoped Tools, Skills, output contract, permission
profile, model override, and metadata. It does not execute itself.

The only supported declarative package shape is:

```text
agent.toml
intent.toml        optional descriptive defaults
loop.toml          optional loop limits
workflows/*.yaml   one or more required Workflows
skills/            optional
```

A factory package is distinct: its `agent.toml` contains exactly
`factory = "module:function"`.

## Workflow and Node

```text
Workflow
  id
  description
  input_schema
  start_node
  nodes: Node[]

Node
  id
  execution: operation | autonomous
  completion
  transitions
```

An `operation` Node names one trusted, versioned Operation adapter. An
`autonomous` Node supplies a goal, input bindings, capability ceiling, limits,
and completion contract. It embeds the existing `AgentLoop` and single Brain.
When an Agent owns multiple Workflows and the caller does not pin one,
`description` and `input_schema` form the model-facing routing contract. The
Router selects exactly one Workflow and the Harness validates its generated
input before execution.

An autonomous completion may declare `review: required`. After Schema and
validator checks pass, the Harness persists the proposed result as a
`node_review` interaction instead of transitioning. Approval commits the exact
reviewed result, revision returns feedback to the same Node, and cancellation
terminates the Workflow. Review is a completion policy, not a third Node type.

## AgentLoop

`LoopState` is scoped by Workflow run, Workflow id, Node id, and Node attempt.
Each iteration produces one closed `StepDecision` and one durable
`StepRecord`. Runtime Operation kinds are `tool`, `memory_write`, and
`workflow_control`. The sole Workflow control Operation is `complete_node`.

The model proposes completion. The Harness verifies output JSON Schema,
completion validator, execution contract, and pinned Workflow before committing
the transition. A completion validator may expose a deterministic rejection
explanation; that precise repair feedback returns to the same autonomous Node.

## Workflow State

`WorkflowState` records the selected definition and execution-contract
fingerprints, current Node attempt, revision, committed Node outputs,
transitions, optional Loop state, Step records, terminal output, and failure.
Resume rejects a changed Workflow selection, definition, or execution contract.

## Action, Policy, and Tools

`ActionGateway` is the single governed path for Runtime Operations. It delegates
mechanical Tool concerns to `ToolGateway`: registry lookup, schema validation,
Agent visibility, denied-retry protection, hooks, Policy decision, dry-run,
execution, timeout/retry, and normalized result.

`ToolSpec.max_calls_per_node` is an optional positive integer. For an
autonomous Node it limits how many times that Operation may be selected in one
Node input round. The planner removes an exhausted Operation from the Brain's
capability list, and WorkflowRuntime enforces the same bound before dispatch.
Accepted human input starts a new input round; the Node's overall `max_steps`
remains the terminal safety limit. The bound is pinned in the execution
contract fingerprint.

Product permission modes are `auto`, `preview`, and `trust`.

## Session boundary

`ModiSession.run_task`, `stream`, and `astream` accept an Agent, request input,
optional `workflow_id`, files, permission mode, and thread id. With no explicit
ID, a sole Workflow is selected directly and multiple Workflows use the Agent
Router. Responses expose run/thread identity, normalized status, output,
pending interaction fields, and
error. Checkpoints pin the Workflow across process boundaries.
