# Modi Harness Architecture

This directory is the current architecture map for maintainers. It describes
module ownership and runtime data flow. Detailed design history and task plans
remain under `docs/superpowers/`; exact shared shapes live in
[`../reference/types.md`](../reference/types.md).

> **Intent-aligned runtime:** the runtime is re-centered from governance-first
> to intent-first under
> [`../superpowers/plans/2026-06-23-intent-aligned-runtime-redesign-plan.md`](../superpowers/plans/2026-06-23-intent-aligned-runtime-redesign-plan.md)
> (spec:
> [`../superpowers/specs/2026-06-23-intent-aligned-runtime-redesign.md`](../superpowers/specs/2026-06-23-intent-aligned-runtime-redesign.md)).
> `HumanIntentContext`, `IntentClarity`, `AutonomyScope`, `ActionProposal`,
> `AlignmentDecision`, and `PendingJudgment` are the live runtime concepts;
> they have superseded the governance-first names. Intent shapes autonomy,
> alignment checks drift, governance proves safety.

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
     |                 +-> Context (intent-first) -> ModelAdapter
     |                 +-> ActionGateway -> Alignment -> Governance / Hooks
     |                 +-> OutputController
     |
     +-> Workspace / Memory / Checkpointer / Agent registry
```

Graph nodes receive collaborators through `GraphDeps`; they do not reach into
global registries. `GraphDeps.tools` is the `ActionGateway` — every
model-requested action flows through alignment first, then governance.
`ModiSession` assembles this dependency bundle and compiles the graph once.

## Run flow

```text
discover/load Agent
-> construct ModiSession
-> seed run and thread state
-> establish human intent context
-> build ContextPack
-> call model
-> execute aligned action or validate output
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
