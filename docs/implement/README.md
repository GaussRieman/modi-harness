# Modi Harness Implementation Design

This folder defines **how** Modi Harness is implemented. Architecture docs define **what** the contracts are; this folder defines packaging, dependencies, settings, internal layout, and test surface.

When this folder and architecture docs disagree, architecture wins. When this folder and `types-reference.md` disagree, `types-reference.md` wins.

## Runtime Position

Modi Harness is a Python package built to maximize LangChain and LangGraph reuse.

LangChain and LangGraph are the default foundations for agent execution, model integration, tool binding, checkpointing, streaming, and state graph orchestration. Modi Harness adds a governed engineering layer: Markdown agents, skill loading, memory, hooks, context discipline, permission policy, workspace persistence, output validation, and tracing.

Users should still be able to build and run simple agents directly with LangChain and LangGraph. Modi Harness is for cases that need stronger governance, reusable skills, durable workspace state, cross-run memory, user hooks, approvals, and auditability.

## Product Scope

Use raw LangChain/LangGraph when the task is a simple agent, prototype, or single workflow without durable governance needs.

Use Modi Harness when the agent needs:

- reusable Markdown agent definitions
- reusable skill packages
- governed tools and approval flow
- persistent workspace artifacts
- typed cross-run memory
- user-configurable hooks
- output validation
- traceable execution and audit records

V0.1 succeeds when a developer can: define one Markdown agent, load one skill, register one LangChain-compatible tool, run a single-agent LangGraph loop, interrupt for approval, resume, inspect workspace + trace files, write a memory record, and configure a hook.

Default rules:

- LangGraph for the runtime loop.
- LangChain for chat models, tool binding, and provider integrations.
- Modi Harness contracts (see `types-reference.md`) stay stable so advanced users can still interoperate with raw LangChain/LangGraph objects.
- Implement from scratch only for modules that are governance, storage, memory, or hooks concerns rather than framework concerns.

## Project Defaults

- Language: Python.
- Environment manager: `uv`.
- Config source: `.env`.
- Package style: `src/modi_harness/`.
- Tests: `tests/`.
- Runtime workspace: `.modi/workspace/` by default.
- Memory roots: `~/.modi/memory/` (user/agent) + `.modi/memory/` (project) + `<workspace>/threads/<thread_id>/memory/` (conversation).
- Local traces: `<workspace_root>/<run_id>/logs/trace.jsonl` (authoritative); optional async mirror to `MODI_TRACE_ROOT`.

## Planned Source Layout

```text
src/modi_harness/
├── api/
├── agents/
├── skills/
├── context/
├── runtime/
├── models/
├── tools/
├── graph/
├── policy/
├── workspace/
├── output/
├── trace/
├── memory/
├── hooks/
├── config/
└── types.py
```

## Dependency Direction

```text
api
-> runtime
-> agents / skills / context / models / tools / output
-> memory / hooks
-> policy / workspace / trace / config / types
```

Rules:

- `api/` depends on runtime, not internals.
- `runtime/` orchestrates modules; modules do not call runtime.
- `tools/` calls policy before execution.
- `context/` reads workspace and memory indexes but does not write.
- `models/`, `tools/`, `runtime/`, `graph/` may import LangChain/LangGraph.
- `agents/`, `skills/`, `context/`, `policy/`, `workspace/`, `output/`, `trace/`, `memory/`, `hooks/`, `config/` stay framework-independent.

## Implementation Order

1. Project foundation and typed settings.
2. Core types mirroring `types-reference.md`.
3. Agent Loader.
4. Skill Loader.
5. Workspace Manager.
6. Memory Store.
7. Trace Recorder.
8. Hook System.
9. Policy Gate.
10. Tool Gateway.
11. Context Manager.
12. Model Adapter.
13. Output Controller.
14. Runtime Adapter.
15. Harness API.
16. Evaluation fixtures and smoke examples.

## Framework Policy

- Runtime graph: LangGraph first.
- Model access: LangChain first.
- Tool integration: LangChain-compatible tools first.
- Checkpointing and interrupts: LangGraph primitives where practical.
- Custom implementation acceptable for loaders, config, policy, workspace, output validation, memory, hooks, and trace recording.
- Custom implementation also acceptable when a framework abstraction would make the harness harder to reason about.

## Design Documents

- [Project Foundation](./00-project-foundation.md)
- [Agent Loader](./01-agent-loader.md)
- [Skill Loader](./02-skill-loader.md)
- [Context Manager](./03-context-manager.md)
- [Runtime Adapter](./04-runtime-adapter.md)
- [Tool Gateway](./05-tool-gateway.md)
- [Policy Gate](./06-policy-gate.md)
- [Workspace Manager](./07-workspace-manager.md)
- [Harness API](./08-harness-api.md)
- [Model Adapter](./09-model-adapter.md)
- [Output Controller](./10-output-controller.md)
- [Trace Recorder](./11-trace-recorder.md)
- [LangChain/LangGraph Integration](./12-langchain-langgraph-integration.md)
- [Evaluation and Quality](./13-evaluation-and-quality.md)
- [Memory](./14-memory-store.md)
- [Hook System](./15-hook-system.md)
