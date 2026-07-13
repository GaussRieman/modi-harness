# Modi Harness

**Autonomous agents, aligned with human intent.**

Modi Harness is a **human-centered agent runtime** for teams that want AI
agents to work independently without drifting away from human intent.

It gives agents autonomy inside an intent field: the human goal, boundaries,
responsibilities, success criteria, and stage-level judgment that define what
the work is for. The AgentLoop owns that intent's life cycle, the Brain decides
the next semantic Step, and the runtime keeps every consequential operation
attached to the purpose.

Most teams face a bad choice: keep agents harmless, or give them power and
micromanage every step. Modi Harness creates a third path — agents that can
plan, act, pause, adapt, and resume with bounded autonomy around human intent.

## What Modi Harness gives you

**Align on intent, not every step.** Capture the goal, constraints, success
criteria, and responsibility behind a task. The agent should not need a human
for every move; it should need a clear field in which to move.

**Preserve autonomy inside clear boundaries.** Let agents decompose work,
choose tools, handle intermediate failures, and produce artifacts without
constant supervision. Boundaries shape autonomy; they do not replace it.

**Escalate at judgment points.** Bring people in when the goal is ambiguous, a
stage boundary is reached, a responsibility shift is implied, or an action
would leave the declared intent field. Human input should update the run, not
just approve a button.

**Explain the path afterward.** Checkpointed execution lets the agent continue
after human input instead of restarting from scratch. Traces connect Loop,
Brain decisions, Step records, runtime operations, policy gates, tool
execution, and final output.

## Where it fits

Modi Harness is not a LangGraph wrapper or a personal assistant product. It
uses LangGraph as the execution kernel, then adds the runtime layer business
agents need when they are allowed to touch real systems.

- Use LangGraph when the main problem is running a stateful agent workflow.
- Use OpenClaw when the main problem is giving users a local-first personal AI
  assistant connected to channels and tools.
- Use Modi Harness when the main problem is letting business agents act under
  explicit human intent, memory, permissions, confirmations, evidence, and
  output contracts.

The important pieces above the graph are agent declarations, skills, memory
scope, tool governance, human interaction, trace evidence, and output
validation. See [Product Positioning](docs/project/positioning.md) for the
full comparison.

Plain agents are still better written on raw LangChain/LangGraph. Reach for
Modi Harness when an agent needs meaningful freedom, but that freedom has to
remain anchored to human goals, boundaries, memory, and responsibility.

## Status

**V0.8.0-dev** — `AgentLoop`, `Brain`, and `Step` are first-class runtime
concepts. `brain_step` is the graph control node; slow Brain uses structured
`StepDecision` planning, while tools, memory writes, stage transitions, and
final output run as `RuntimeOperation`s through the Harness path.

Current implementation covers governed runtime operations, judgment interrupts,
checkpointed resume, workspaces, memory, output validation, and structured
traces. The product direction is stronger intent life-cycle execution:
declarative Agent packages, tighter Brain contracts, auditable Step records,
and cost attribution per successful aligned task.

See [`docs/superpowers/plans/development-plan.md`](docs/superpowers/plans/development-plan.md) and
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
        "research_question": "这篇论文的核心贡献是什么？",
        "source_urls": ["https://arxiv.org/abs/1706.03762"],
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
- [Development Plan](docs/superpowers/plans/development-plan.md)
- [Architecture Overview](docs/architecture/README.md)
- [Authoritative Types Reference](docs/reference/types.md)
- [Examples](examples/)
- [Changelog](CHANGELOG.md)

## License

MIT
