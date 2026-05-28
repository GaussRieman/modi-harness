# Modi Harness Implementation Design

This folder defines how Modi Harness should be implemented.

Architecture docs define module contracts. Implementation docs define project layout, dependencies, internal types, execution boundaries, and LangChain/LangGraph integration points.

## Runtime Position

Modi Harness is a Python agent harness built to maximize LangChain and LangGraph capabilities.

LangChain and LangGraph are the default foundations for agent execution, model integration, tool binding, checkpointing, streaming, and state graph orchestration. Modi Harness adds a governed engineering layer: Markdown agents, skill loading, context discipline, permission policy, workspace persistence, output validation, and tracing.

Users should still be able to build and run simple agents directly with LangChain and LangGraph. Modi Harness is for cases that need stronger governance, reusable skills, durable workspace state, approvals, and auditability.

## Product Scope

Use raw LangChain/LangGraph when the task is a simple agent, prototype, or single workflow without durable governance needs.

Use Modi Harness when the agent needs:

- reusable Markdown agent definitions
- reusable skill packages
- governed tools and approval flow
- persistent workspace artifacts
- output validation
- traceable execution and audit records

V0.1 succeeds when a developer can define one Markdown agent, load one skill, register one LangChain-compatible tool, run a single-agent LangGraph loop, interrupt for approval, resume, and inspect workspace plus trace files.

Default rule:

- Use LangGraph for the runtime loop.
- Use LangChain for chat models, tool binding, and provider integrations.
- Keep Modi Harness contracts stable so advanced users can still interoperate with raw LangChain/LangGraph objects.
- Implement from scratch only for modules that are governance or storage concerns rather than framework concerns.

## Project Defaults

- Language: Python.
- Environment manager: `uv`.
- Config source: `.env`.
- Package style: `src/modi_harness/`.
- Tests: `tests/`.
- Runtime workspace: `.modi/workspace/` by default.
- Local traces: `.modi/traces/` or run-local `workspace/<run_id>/logs/`.

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
├── config/
└── types.py
```

## Dependency Direction

```text
api
-> runtime
-> agents / skills / context / models / tools / output
-> policy / workspace / trace / config / types
```

Rules:

- `api/` depends on runtime, not internals.
- `runtime/` orchestrates modules; modules do not call runtime.
- `tools/` calls policy before execution.
- `context/` reads workspace indexes but does not write workspace files.
- `models/`, `tools/`, `runtime/`, and `graph/` may import LangChain/LangGraph.
- governance modules stay independent from framework internals.

## Config

`.env` provides runtime configuration. Code reads it through a typed settings object.

Expected keys:

```text
MODI_MODEL_PROVIDER=
MODI_MODEL_NAME=
MODI_MODEL_API_KEY=
MODI_MODEL_BASE_URL=
MODI_WORKSPACE_ROOT=.modi/workspace
MODI_TRACE_ROOT=.modi/traces
MODI_PERMISSION_MODE=ask
MODI_MAX_STEPS=20
```

## Developer Workflow

Expected local commands:

```text
uv sync
uv run pytest
uv run python -m modi_harness
```

V0.1 should include `.env.example`, sample agent, sample skill, and one runnable smoke test.

## Implementation Order

1. Project foundation and typed settings.
2. Core dataclasses / TypedDicts.
3. Agent Loader.
4. Skill Loader.
5. Workspace Manager.
6. Trace Recorder.
7. Policy Gate.
8. Tool Gateway.
9. Context Manager.
10. Model Adapter.
11. Output Controller.
12. Runtime Adapter.
13. Harness API.
14. Evaluation fixtures and smoke examples.

## Framework Policy

- Runtime graph: LangGraph first.
- Model access: LangChain first.
- Tool integration: LangChain-compatible tools first.
- Checkpointing and interrupts: use LangGraph primitives where practical.
- Custom implementation is acceptable for loaders, config, policy, workspace, output validation, and trace recording.
- Custom implementation is also acceptable when a framework abstraction would make the harness harder to reason about.

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
