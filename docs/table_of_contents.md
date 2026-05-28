# Modi Harness — Documentation Index

Modi Harness is a LangChain + LangGraph runtime kernel for governed, locally-defined agents. This index is the **map** of the documentation tree. Start here when you arrive at the repo.

## Read in This Order

If you are about to **develop** Modi Harness:

1. [Architecture Overview](./architecture/README.md) — what the system is and why.
2. [Authoritative Types Reference](./types-reference.md) — the contracts between modules; the source of truth for all types.
3. [Implementation Overview](./implement/README.md) — packaging, dependencies, dependency direction, implementation order.
4. [Implementation Order](./implement/README.md#implementation-order) — what to build first.
5. [Project Foundation](./implement/00-project-foundation.md) — settings, layout, `.env` keys, `uv` workflow.
6. Walk each module's architecture doc → its implementation doc, in implementation order:
   - 01 → 11 core modules
   - 14 Memory Store, 15 Hook System
   - 12 Permission Mode, 15 Untrusted Content (cross-cutting rules)
7. [Evaluation and Quality](./implement/13-evaluation-and-quality.md) — smoke scenarios that V0.1 must pass.
8. [Agents](./agents/README.md) and [Scenarios](./scenarios/README.md) — multi-domain examples that exercise the harness end-to-end.

If you are **using** Modi Harness as a downstream developer:

1. [Architecture Overview](./architecture/README.md) — what governance you get for free.
2. [Agents](./agents/README.md) — examples of reusable agent definitions.
3. [Scenarios](./scenarios/README.md) — examples of end-to-end runs.
4. [Harness API](./architecture/08-harness-api.md) — what to call.
5. [Permission Mode](./architecture/14-permission-mode.md) — when to use which mode.

## Authority

When documents disagree:

1. [`types-reference.md`](./types-reference.md) is authoritative for types.
2. [`architecture/`](./architecture/) is authoritative for contracts.
3. [`implement/`](./implement/) is authoritative for packaging, settings, and tests.

## Architecture (Module Contracts)

Core modules:

- [Agent Loader](./architecture/01-agent-loader.md)
- [Skill Loader](./architecture/02-skill-loader.md)
- [Context Manager](./architecture/03-context-manager.md)
- [Runtime Adapter](./architecture/04-runtime-adapter.md)
- [Tool Gateway](./architecture/05-tool-gateway.md)
- [Policy Gate](./architecture/06-policy-gate.md)
- [Workspace Manager](./architecture/07-workspace-manager.md)
- [Harness API](./architecture/08-harness-api.md)
- [Model Adapter](./architecture/09-model-adapter.md)
- [Output Controller](./architecture/10-output-controller.md)
- [Trace Recorder](./architecture/11-trace-recorder.md)

Cross-cutting subsystems:

- [Memory Store](./architecture/12-memory-store.md)
- [Hook System](./architecture/13-hook-system.md)
- [Permission Mode](./architecture/14-permission-mode.md)
- [Untrusted Content Boundary](./architecture/15-untrusted-content.md)

Future modules (deferred from V0.1):

- [Input Router](./architecture/future/input-router.md)
- [Subagent Runtime](./architecture/future/subagent-runtime.md)

## Implementation Design

- [Project Foundation](./implement/00-project-foundation.md)
- [Agent Loader](./implement/01-agent-loader.md)
- [Skill Loader](./implement/02-skill-loader.md)
- [Context Manager](./implement/03-context-manager.md)
- [Runtime Adapter](./implement/04-runtime-adapter.md)
- [Tool Gateway](./implement/05-tool-gateway.md)
- [Policy Gate](./implement/06-policy-gate.md)
- [Workspace Manager](./implement/07-workspace-manager.md)
- [Harness API](./implement/08-harness-api.md)
- [Model Adapter](./implement/09-model-adapter.md)
- [Output Controller](./implement/10-output-controller.md)
- [Trace Recorder](./implement/11-trace-recorder.md)
- [LangChain/LangGraph Integration](./implement/12-langchain-langgraph-integration.md)
- [Evaluation and Quality](./implement/13-evaluation-and-quality.md)
- [Memory Store](./implement/14-memory-store.md)
- [Hook System](./implement/15-hook-system.md)

## Agents (Reusable Role Definitions)

See [`agents/README.md`](./agents/README.md) for authoring guidance.

- [support-bot](./agents/support-bot/agent.md) — conversational, free-form, multi-turn, memory.
- [research-assistant](./agents/research-assistant/agent.md) — investigative, citations, plan mode demo.
- [case-reviewer](./agents/case-reviewer/agent.md) — structured business review, review-required output.
- [release-coordinator](./agents/release-coordinator/agent.md) — ops coordination, hooks, coding rule pack.

## Scenarios (End-to-End Run Fixtures)

See [`scenarios/README.md`](./scenarios/README.md) for authoring guidance.

- [support-bot-default](./scenarios/support-bot-default/scenario.md)
- [research-assistant-default](./scenarios/research-assistant-default/scenario.md)
- [case-reviewer-default](./scenarios/case-reviewer-default/scenario.md)
- [release-coordinator-default](./scenarios/release-coordinator-default/scenario.md)

## References

- [Claude Code system prompt reference](./claude_code_system_prompt_原文.md) — design inspiration source.
