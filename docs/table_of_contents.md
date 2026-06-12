# Modi Harness — Documentation Index

Modi Harness is a LangChain + LangGraph runtime kernel for governed, locally-defined agents. This index is the **map** of the documentation tree. Start here when you arrive at the repo.

## Read in This Order

If you are about to **develop** Modi Harness:

1. [Development Plan](./development-plan.md) — milestones, conventions, task tracking, exit criteria. **Start here during V0.1.**
2. [Core Concepts](./architecture/00-core-concepts.md) — Workspace, Session, Thread, Run, Store, Context, Memory, Trace.
3. [Architecture Overview](./architecture/README.md) — what the system is and why.
4. [Authoritative Types Reference](./types-reference.md) — the contracts between modules; the source of truth for all types.
5. [Implementation Overview](./implement/README.md) — packaging, dependencies, dependency direction, implementation order.
6. [Project Foundation](./implement/00-project-foundation.md) — settings, layout, `.env` keys, `uv` workflow.
7. Walk each module's architecture doc → its implementation doc, in milestone order from the development plan.
8. [Evaluation and Quality](./implement/13-evaluation-and-quality.md) — smoke scenarios that V0.1 must pass.
9. [Agents](./agents/README.md) and [Scenarios](./scenarios/README.md) — multi-domain examples that exercise the harness end-to-end.

If you are **using** Modi Harness as a downstream developer:

1. [Core Concepts](./architecture/00-core-concepts.md) — what Workspace, Run, Thread, Context, Memory, and Trace mean.
2. [Architecture Overview](./architecture/README.md) — what governance you get for free.
3. [CLI Guide](./cli.md) — `modi run` / `modi resume`, streaming output, approval keystrokes, TTY auto-detection.
4. [Agents](./agents/README.md) — examples of reusable agent definitions.
5. [Scenarios](./scenarios/README.md) — examples of end-to-end runs.
6. [Harness API](./architecture/08-harness-api.md) — what to call.
7. [Permission Mode](./architecture/14-permission-mode.md) — when to use which mode.
8. [Plugin Author Guide](./plugins.md) — *new in V0.4c*. How to ship a `pip install`-able package that contributes agents, skills, and tools.
9. [Builtin Tools](./builtins.md) — *new in V0.4d*. Kernel-level workspace and memory primitives implicitly available to every agent.

## Authority

When documents disagree:

1. [`types-reference.md`](./types-reference.md) is authoritative for types.
2. [`architecture/`](./architecture/) is authoritative for module contracts.
3. [`development-plan.md`](./development-plan.md) is authoritative for V0.1 scope, milestones, and conventions.
4. [`implement/`](./implement/) is authoritative for packaging, settings, and tests.

## Architecture (Module Contracts)

Core modules:

- [Core Concepts](./architecture/00-core-concepts.md)
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

- [Memory](./architecture/12-memory-store.md)
- [Hook System](./architecture/13-hook-system.md)
- [Permission Mode](./architecture/14-permission-mode.md)
- [Untrusted Content Boundary](./architecture/15-untrusted-content.md)
- [Subagent Runtime](./architecture/16-subagent-runtime.md) — *new in V0.2*
- [Checkpointer](./architecture/17-checkpointer.md) — *new in V0.2*

Future modules (deferred):

- [Input Router](./architecture/future/input-router.md)

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
- [Memory](./implement/14-memory-store.md)
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
- [release-coordinator-with-research](./scenarios/release-coordinator-with-research/scenario.md) — *new in V0.3*

## Process

- [Development Plan](./development-plan.md) — V0.1 roadmap, milestones, TDD conventions, push policy.

## References

- [Claude Code system prompt reference](./claude_code_system_prompt_原文.md) — design inspiration source.
