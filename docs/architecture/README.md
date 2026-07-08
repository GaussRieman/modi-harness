# Modi Harness Architecture

This directory is the current architecture map for maintainers. It describes
module ownership and runtime data flow. Detailed design history and task plans
remain under `docs/superpowers/`; exact shared shapes live in
[`../reference/types.md`](../reference/types.md).

> **Brain-loop runtime:** the runtime is now centered on an intent execution
> life cycle: `AgentLoop` owns the run, `Brain` decides the next semantic
> `StepDecision`, `StepRecord` records progress, and consequential work runs as
> `RuntimeOperation`s through the Harness path. This extends the earlier
> intent-aligned redesign
> ([plan](../superpowers/plans/2026-06-23-intent-aligned-runtime-redesign-plan.md),
> [spec](../superpowers/specs/2026-06-23-intent-aligned-runtime-redesign.md))
> with the Brain-loop design
> ([plan](../superpowers/plans/2026-07-07-brain-agent-loop-runtime-plan.md),
> [spec](../superpowers/specs/2026-07-07-brain-agent-loop-runtime-design.md)).

## Position

LangChain provides model and tool integration. LangGraph provides graph
execution, checkpointing, streaming, and resume. Modi Harness adds the
alignment layer above them: the runtime semantics for agents that need autonomy
inside human intent.

The design center is not “more approvals.” It is **bounded autonomy within
human intent**. Humans define goals, boundaries, responsibilities, success
criteria, and judgment points. Agents plan and act within that field.
Alignment, governance, trace, and output validation preserve and prove fit when
the run approaches a boundary. Approval is one judgment kind, not the runtime
center.

Governance is therefore a support layer, not the core abstraction. The core
abstraction is the relationship between human intent, agent autonomy, runtime
state, and consequential action.

The public API still has three objects:

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
     |                 +-> AgentLoop -> Brain -> StepDecision / StepRecord
     |                 +-> RuntimeOperation -> ActionGateway -> Alignment -> Governance / Hooks
     |                 +-> Context (intent-first) -> ModelAdapter (structured slow planning)
     |                 +-> OutputController
     |
     +-> Workspace / Memory / Checkpointer / Agent registry
```

Graph nodes receive collaborators through `GraphDeps`; they do not reach into
global registries. `brain_step` is the semantic control node. The model cannot
call business tools directly in the main runtime; slow Brain exposes only the
`submit_step_decision` protocol and requests tools, stage transitions, memory
writes, or final output as runtime operations.
`ModiSession` assembles this dependency bundle and compiles the graph once.

## Run flow

```text
discover/load Agent
-> construct ModiSession
-> seed run and thread state
-> establish human intent context
-> initialize AgentLoop
-> Brain plans one StepDecision
-> record StepRecord
-> execute one RuntimeOperation when needed
-> validate output or continue the Loop
-> escalate at judgment points when required
-> persist checkpoint, run files, and trace
-> resume with updated intent context
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
