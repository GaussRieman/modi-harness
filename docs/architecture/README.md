# Modi Harness Architecture

Modi Harness is a LangChain + LangGraph runtime kernel for locally-defined,
tool-using agents.

Harness is model-first: the model is the reasoning center, and Harness is the
execution substrate around it. Harness assembles context, exposes tools,
persists run files, provides model-accessible memory, validates outputs, records
traces, and enforces policy boundaries. It should extend model capability, not
act as a parallel decision-maker.

Start with [Core Concepts](./00-core-concepts.md). It defines the current
model-first vocabulary for Context, Tools, Workspace, Memory, Trace, Session,
Thread, Run, and Store.

## Position

Modi Harness sits **around** the model and **on top of** LangChain and
LangGraph. Simple agents should remain easy to build with raw framework code.
Modi adds execution surfaces when the user needs reusable Markdown agents, skill
packages, tools, approvals, run files, memory, hooks, output validation, and
trace. These are support surfaces for model action, not replacements for model
reasoning.

## Non-Goals (V0.1)

- Modi does not provide a vector database. Memory is rule-and-tag based.
- Modi does not provide a coding-specific agent. It ships rule packs (incl. an opt-in `coding` pack) but stays domain-neutral.
- Modi does not provide a frontend, dashboard, or hosted service.
- Modi does not own model fine-tuning, prompt optimization, or eval harnesses beyond the Modi-internal trace replay.
- Modi V0.1 does not run multi-agent or subagent flows. Subagent Runtime is deferred (see `future/`).

## V0.1 Runtime

```text
Harness API
-> Agent Loader
-> Skill Loader
-> Memory Store
-> Context Manager
-> Runtime Adapter (LangGraph)
   ├── Hook System (event dispatch)
   ├── Model Adapter (LangChain chat)
   ├── Tool Gateway (LangChain tools)
   ├── Policy Gate (mode-aware decisions)
   └── Output Controller
-> Workspace Manager
-> Trace Recorder
```

V0.1 is single-agent. `Input Router` and `Subagent Runtime` are deferred (see `future/`).

## Flow

```text
run_task
-> load agent (resolve sources, normalize contract)
-> load skills (multi-source)
-> select memory (scope-ordered, budgeted)
-> build context (deterministic, trust-annotated)
-> call model (LangChain, untrusted-wrapped)
-> validate output OR route tool call
-> apply permission mode + policy
-> hooks (pre/post)
-> execute / interrupt / deny / require review
-> update state, workspace, memory, trace
-> continue or return
```

## Core Contracts

- **Agent**: executable role, default tools, default skills, constraints, output contract.
- **Skill**: loadable capability package; instruction + optional assets.
- **Workspace**: application-defined work boundary; run files live under it.
- **Run**: one execution attempt with input, state, files, trace, and status.
- **Thread**: continuity across related runs.
- **Memory Record**: compact reusable fact, preference, rule, method, or pointer that may be selected into future context.
- **Context Pack**: deterministic model input assembled from trusted instructions and selected task material.
- **Tool Gateway**: the only path from model-requested tool calls to execution.
- **Policy Gate**: the authority for side effects, approval, denial, and review; mode-aware.
- **Permission Mode**: run-scoped switch (`ask` / `auto` / `plan` / `bypass`) shifting Policy defaults.
- **Run files**: run-scoped input, state, artifacts, drafts, logs, and references stored inside the workspace.
- **Trace**: structured timeline of decisions, tool calls, approvals, denials, outputs, errors.
- **Hook**: user-defined shell or Python callback invoked at well-defined lifecycle events.

Authoritative type definitions live in [`../types-reference.md`](../types-reference.md).

## Runtime Rules

- Tool calls run under an explicit permission mode.
- Risky or hard-to-reverse actions interrupt for approval.
- A denied action is not retried unchanged (defense in depth: Runtime + Tool Gateway).
- Tool results, external files, references, skill assets, and user documents are untrusted content.
- Untrusted content is wrapped in `<untrusted>` blocks; it never overrides system, agent, skill, memory, or policy instructions.
- Hook feedback is treated as user feedback and can block or redirect execution.
- Review-required output is preserved as draft, not returned as final.
- Destructive or abusive security actions are denied without clear authorization.
- Large or sensitive payloads stay in run files; context and trace use references when possible.
- Memory is selected context material, not an instruction override; raw tool output cannot be promoted to memory without a reviewed path.

## Documents

Core modules:

- [Core Concepts](./00-core-concepts.md)
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

Cross-cutting subsystems:

- [Memory](./12-memory-store.md)
- [Hook System](./13-hook-system.md)
- [Permissions / Modes / Execution](./permissions.md) — **start here for the conceptual model**
- [Permission Mode (legacy 4-mode design)](./14-permission-mode.md)
- [Untrusted Content Boundary](./15-untrusted-content.md)

Future modules (deferred from V0.1):

- [Input Router](./future/input-router.md)
- [Subagent Runtime](./future/subagent-runtime.md)
