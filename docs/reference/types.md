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

## AgentLoop

`LoopState` is scoped by Workflow run, Workflow id, Node id, and Node attempt.
Each iteration produces one closed `StepDecision` and one durable
`StepRecord`. Runtime Operation kinds are `tool`, `memory_write`, and
`workflow_control`. The sole Workflow control Operation is `complete_node`.

The model proposes completion. The Harness verifies output JSON Schema,
completion validator, execution contract, and pinned Workflow before committing
the transition.

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

Product permission modes are `auto`, `preview`, and `trust`.

## Session boundary

`ModiSession.run_task`, `stream`, and `astream` accept an Agent, Workflow input,
optional `workflow_id`, inputs, permission mode, and thread id. Responses expose
run/thread identity, normalized status, output, pending interaction fields, and
error. Checkpoints pin the Workflow across process boundaries.
