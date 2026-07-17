# Modi Harness Architecture

The runtime has three public objects:

```text
ModiHarness   shared model, policy, hooks, output, and builtin capabilities
ModiAgent     immutable Workflows, Tools, Skills, and contracts
ModiSession   execution, storage, checkpoint, trace, and thread ownership
```

The stable business path lives in Workflow. Only an `autonomous` Node embeds
the AgentLoop:

```text
Agent -> WorkflowSessionAdapter -> WorkflowRuntime
                                  |- operation -> ActionGateway
                                  `- autonomous -> AgentLoop -> Brain
                                                           -> complete_node
```

Workflow selection, Node transitions, limits, capability ceilings, completion
contracts, and execution fingerprints are Harness authority. The Brain may plan
inside the active autonomous Node but cannot edit those boundaries.

See [Execution Runtime](./execution-runtime.md), [Agent and Skill](./agent-and-skill.md),
[Tools and Policy](./tools-and-policy.md), and the
[types reference](../reference/types.md).

The [Research Assistant architecture](./research-assistant.md) is the canonical
worked example of Agent-local Workflow routing, autonomous research Nodes,
TaskPlan progress, evidence-grounded completion, Trace, and CLI presentation.
