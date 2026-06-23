# Modi Harness Architecture

This directory is the current architecture map for maintainers. It describes
module ownership and runtime data flow. Detailed design history and task plans
remain under `docs/superpowers/`; exact shared shapes live in
[`../reference/types.md`](../reference/types.md).

## Position

LangChain provides model and tool integration. LangGraph provides graph
execution, checkpointing, streaming, and resume. Modi Harness adds the governed
layer around them: Agent and Skill declarations, context, policy, workspace,
memory, output validation, hooks, and trace.

The public runtime has three objects:

```text
ModiHarness   shared capability suite; no Agent or storage ownership
ModiAgent     immutable role, Skills, Tools, contracts, and subagents
ModiSession   Harness + Agents + checkpointer + storage; sole execution owner
```

## Dependency direction

```text
API / CLI / Discovery
        |
        v
ModiSession -> HarnessGraphAdapter -> LangGraph nodes
     |                 |
     |                 +-> Context -> ModelAdapter
     |                 +-> ToolGateway -> Policy / Hooks
     |                 +-> OutputController
     |
     +-> Workspace / Memory / Checkpointer / Agent registry
```

Graph nodes receive collaborators through `GraphDeps`; they do not reach into
global registries. `ModiSession` assembles this dependency bundle and compiles
the graph once.

## Run flow

```text
discover/load Agent
-> construct ModiSession
-> seed run and thread state
-> build ContextPack
-> call model
-> execute governed tool or validate output
-> pause for interaction when required
-> persist checkpoint, run files, and trace
-> return or resume on the same thread
```

## Documents

- [Agent and Skill](./agent-and-skill.md)
- [Execution Runtime](./execution-runtime.md)
- [Context and Memory](./context-and-memory.md)
- [Tools and Policy](./tools-and-policy.md)
- [Workspace and Trace](./workspace-and-trace.md)
- [Output and Hooks](./output-and-hooks.md)

## Source map

| Concern | Source |
|---|---|
| Public objects | `src/modi_harness/api/` |
| Discovery and loaders | `discovery/`, `agents/`, `skills/` |
| Runtime graph | `graph/` |
| Model integration | `models/` |
| Tool governance | `tools/`, `policy/`, `hooks/` |
| Context and memory | `context/`, `memory/` |
| Durable run data | `workspace/`, `trace/`, `checkpoint/` |
| Output validation | `output/` |
