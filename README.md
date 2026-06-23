# Modi Harness

**Autonomous agents, aligned with human intent.**

Modi Harness is a **human-centered agent runtime** for teams that want AI
agents to work independently without drifting away from human intent.

It gives agents autonomy inside an intent field: the human goal, boundaries,
responsibilities, success criteria, and stage-level judgment that define what
the work is for. The agent chooses the path; the runtime keeps the path
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
after human input instead of restarting from scratch. Traces connect intent,
stage decisions, policy gates, tool execution, and final output.

Under the hood, Modi Harness builds this alignment layer on LangChain +
LangGraph:

- governed tool execution with policy gates and approvals
- checkpointed run state for pause/resume workflows
- run-scoped workspace persistence
- typed cross-run memory
- output validation against denied side-effect claims
- structured, redacted JSONL traces

Plain agents are still better written on raw LangChain/LangGraph. Reach for
Modi Harness when an agent needs meaningful freedom, but that freedom has to
remain anchored to human goals, boundaries, and responsibility.

## Status

**V0.7.1** — discovered Agents are dynamic commands with Agent-driven interactive
startup. Project Agents are found from `modi.toml`; task-aware Agents expose
truthful checkpointed progress to CLI, API, and other clients.

Current implementation covers governed execution, approval interrupts,
checkpointed resume, workspaces, memory, output validation, and structured
traces. The product direction is stronger intent alignment: explicit human
intent context, stage-level alignment, editable reviews, action integrity,
decision trails, and cost attribution per successful aligned task.

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

V0.5 splits the old God-Object `ModiHarness` into **three** top-level objects:
`ModiHarness` (capability suite), `ModiAgent` (agent declaration), and
`ModiSession` (binds harness + agents + infra; the sole execution entry point).

```python
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from modi_harness import ModiHarness, ModiAgent, ModiSession, ToolBinding

# 1) Capability suite — knows nothing about specific agents.
harness = ModiHarness(
    chat_model=ChatOpenAI(model="gpt-4o-mini"),
    rule_packs=["default"],
)

# 2) Agent declarations — markdown- or code-constructed, equivalent.
research_assistant = ModiAgent.from_markdown(
    "agents/research_assistant/agent.md",
    tools=[
        ToolBinding(
            spec={
                "name": "fetch_url",
                "description": "Fetch a URL and return cleaned source text.",
                "input_schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "format": "uri"}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
                "risk_level": "L1",
                "side_effect": False,
                "idempotent": True,
            },
            handler=lambda url: {"url": url, "title": url, "content": ""},
        ),
    ],
)

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

Runnable end-to-end demos live under [`examples/`](examples/) — each has a
`run.py` that wires a real chat model, agents, tools, and a session
(`research_assistant`, `code_auditor`, `support_triage`).

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
ModiSession  (binds harness + agents + infra; sole execution entry point)
  -> HarnessGraphAdapter (modi → LangGraph; owned by the session)
      -> Agent Loader / Skill Loader
      -> Memory Store
      -> Context Manager
      -> Model Adapter (LangChain chat models)
      -> Tool Gateway (harness builtins + per-agent scoped tools)
          -> Hook System
          -> Policy Gate (mode-aware decisions, rule packs)
      -> Output Controller
  -> Workspace Manager (run-scoped storage)
  -> Trace Recorder (JSONL, redaction, replay)

ModiHarness  (capability suite: policy, hooks, output, context, model, builtins)
ModiAgent    (immutable agent declaration: profile, scoped tools, skills, subagents)
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
