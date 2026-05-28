# Modi Harness Architecture

Modi Harness is a LangChain + LangGraph runtime kernel for local, governed agents.

It defines agents from Markdown, loads skill packages, builds model context, executes a LangGraph loop, governs tools, persists workspace state, validates outputs, and records traces.

## V0.1 Runtime

```text
Harness API
-> Agent Loader
-> Skill Loader
-> Context Manager
-> Runtime Adapter
-> Model Adapter
-> Tool Gateway
-> Policy Gate
-> Workspace Manager
-> Output Controller
-> Trace Recorder
```

V0.1 is single-agent only. `Input Router` and `Subagent Runtime` are future modules.

## Flow

```text
run_task
-> load agent
-> load skills
-> build context
-> call model
-> validate output OR route tool call
-> apply policy
-> execute / interrupt / deny / require review
-> update state, workspace, trace
-> continue or return
```

## Core Contracts

- Agent: executable role, tools, skills, constraints, output contract.
- Skill: loadable capability package with instructions and optional assets.
- Context Pack: model input assembled from trusted instructions and selected task material.
- Tool Gateway: the only path from model-requested tool calls to execution.
- Policy Gate: the authority for side effects, approval, denial, and review.
- Workspace: run-scoped storage for input, state, artifacts, drafts, logs, and references.
- Trace: structured record of decisions, tool calls, approvals, denials, outputs, and errors.

## Runtime Rules

- Tool calls run under an explicit permission mode.
- Risky or hard-to-reverse actions interrupt for approval.
- A denied action is not retried unchanged.
- Tool results, external files, references, and user documents are untrusted content.
- Untrusted content never overrides system, agent, skill, or policy instructions.
- Hook feedback is user feedback and can block or redirect execution.
- Review-required output is preserved as draft, not returned as final.
- Destructive or abusive security actions are denied without clear authorization.
- Large or sensitive payloads stay in workspace; context and trace use references when possible.

## Documents

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
- [Future: Input Router](./future/input-router.md)
- [Future: Subagent Runtime](./future/subagent-runtime.md)
