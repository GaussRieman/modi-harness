# Modi Harness

**Autonomous agents, aligned with human intent.**

Modi Harness is a **human-centered agent runtime** for teams that want AI
agents to work independently without drifting away from human intent.

Each Agent owns one or more explicit Workflows. The Workflow runtime controls
the stable business path; an autonomous Node embeds the single AgentLoop and
Brain for bounded multi-step work. Every consequential Operation still passes
through policy, tools, checkpoints, trace, and output controls.

Most teams face a bad choice: keep agents harmless, or give them power and
micromanage every step. Modi Harness creates a third path — agents that can
plan, act, pause, adapt, and resume with bounded autonomy around human intent.

## What Modi Harness gives you

**Make the business path explicit.** Workflow Nodes and declared transitions
own stable control flow. There is no implicit standalone Agent path.

**Preserve autonomy inside one Node.** Let the Agent decompose work, choose
allowed tools, revise its plan, request input, and prove completion without
letting it rewrite the surrounding Workflow.

**Resume the exact reviewed work.** Judgment and interaction waits persist the
pending proposal, invocation, Node attempt, and collected input. Approval runs
the reviewed action; rejection cannot be silently retried.

**Explain the path afterward.** Checkpoints and incremental events connect the
Workflow, Node attempts, Brain Steps, runtime Operations, policy decisions,
tool execution, and terminal output.

## Where it fits

Modi Harness is not a visual graph builder or a personal assistant product. It
is a governed runtime for business Agents that touch real systems.

- Use LangGraph when the main problem is running a stateful agent workflow.
- Use OpenClaw when the main problem is giving users a local-first personal AI
  assistant connected to channels and tools.
- Use Modi Harness when the main problem is letting business agents act under
  explicit human intent, memory, permissions, confirmations, evidence, and
  output contracts.

The important pieces above the runtime are Agent declarations, Workflows, skills, memory
scope, tool governance, human interaction, trace evidence, and output
validation. See [Product Positioning](docs/project/positioning.md) for the
full comparison.

Plain agents are still better written on raw LangChain/LangGraph. Reach for
Modi Harness when an agent needs meaningful freedom, but that freedom has to
remain anchored to human goals, boundaries, memory, and responsibility.

## Status

**V0.8.0-dev** — mandatory explicit Workflows are the only execution path.
Operation Nodes execute trusted adapters; autonomous Nodes embed the single
AgentLoop and Brain and return through validated `complete_node`.

Current implementation covers trusted completion validators, guarded Node
completion, exact judgment/interaction resume, recovery-mode-constrained
retries, per-Operation autonomous progress bounds, incremental streaming,
workspaces, memory, and structured traces.

See the [current implementation plan](docs/superpowers/plans/2026-07-13-single-brain-mandatory-workflow-hard-cut-plan.md) and
[`CHANGELOG.md`](CHANGELOG.md) for details.

## Install & Verify

```bash
uv sync --extra dev
cp .env.example .env
uv run pytest                  # full suite
uv run pytest -m smoke         # smoke scenarios only
uv run python -m modi_harness --version
```

## Minimal Example

The public API exposes **three** top-level objects:
`ModiHarness` (capability suite), `ModiAgent` (agent declaration), and
`ModiSession` (binds harness + agents + infra; the sole execution entry point).

```python
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from modi_harness import ModiHarness, ModiSession
from modi_harness.discovery import discover_agents

# 1) Capability suite — knows nothing about specific agents.
harness = ModiHarness(
    chat_model=ChatOpenAI(model="gpt-4o-mini"),
    rule_packs=["default"],
)

# 2) Agent packages — agent.toml + workflows/*.yaml, or an exact factory manifest.
research_assistant = discover_agents().registry.resolve("research-assistant").agent

# 3) Session — binds harness, agents, and infra into something runnable.
session = ModiSession(
    harness=harness,
    agents=[research_assistant],
    checkpointer=MemorySaver(),
    workspace_root=".modi/workspace",
    memory_root="~/.modi/memory",
)

# 4) Execute — the sole entry point.
response = session.run_task(
    agent="research-assistant",
    input={
        "research_question": "杭州拉格朗日具身智能科技的公开背景和技术实力如何？",
    },
)
print(response)
```

To load a whole directory of agents at once, use
`ModiAgent.load_dir("agents")`, or let
`ModiSession.from_discovery(harness, agents_dir=..., plugins=...)` discover
plugin-contributed and directory agents together.

Every Agent declares at least one Workflow. A Workflow Node is either an
explicit `operation` or an `autonomous` compound Node executed by the single
AgentLoop/Brain path. The model may propose `complete_node`; the Harness alone
validates and commits completion.

Runnable end-to-end demos live under [`examples/`](examples/) — each has a
`run.py` that wires a real chat model, agents, tools, and a session
(`research_assistant`, `code_auditor`).

## CLI

```bash
modi agents list
modi agents show research-assistant
modi research-assistant
modi info
modi --version
```

`modi AGENT` discovers project, plugin, user, and explicit Agent sources.
Interactive terminals use the live task renderer; `--stream-format plain|jsonl`
provides stable log and machine-readable forms. See [the CLI guide](docs/guides/cli.md).

## Architecture in 10 Seconds

```
ModiSession
  -> WorkflowSessionAdapter
      -> WorkflowRuntime
          -> operation Node -> ActionGateway -> ToolGateway
          -> autonomous Node -> AgentLoop -> Brain -> RuntimeOperation
                                  -> complete_node -> completion validation
      -> Policy / Hooks / Output Controller
      -> Checkpointer
  -> Workspace Manager (run-scoped storage)
  -> Trace Recorder (JSONL, redaction, replay)

ModiHarness  (capability suite: policy, hooks, output, model, builtins)
ModiAgent    (agent declaration: Workflows, scoped tools, skills)
```

Trust boundary: `system / agent / skill / memory / user message` are trusted;
everything else (tool result / workspace file / reference) is untrusted,
wrapped in `<untrusted>` blocks at the prompt boundary, and validated by
Output Controller against denied side-effect claims.

## Documentation

- [Documentation Index](docs/README.md)
- [Current Implementation Plan](docs/superpowers/plans/2026-07-13-single-brain-mandatory-workflow-hard-cut-plan.md)
- [Architecture Overview](docs/architecture/README.md)
- [Authoritative Types Reference](docs/reference/types.md)
- [Examples](examples/)
- [Changelog](CHANGELOG.md)

## License

MIT
