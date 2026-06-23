# Modi Harness

**Build agents around human intent.**

Modi Harness is a **human-centered agent runtime** for teams giving AI agents
real capabilities.

It helps agents act around the people they serve: their goals, boundaries,
judgment, responsibilities, and working context. Agents can move autonomously
where the path is clear, ask for human judgment when the stakes change, explain
what they are doing, and continue without losing state.

Most teams face a bad choice: keep agents harmless, or give them power and hope
nothing goes wrong. Modi Harness creates a third path — agents that can act
with human intent built into the runtime.

## What Modi Harness gives you

**Start from human intent.** Define the goals, boundaries, responsibilities,
and operating rules that should shape an agent's work. The runtime keeps those
human commitments visible as the agent moves.

**Give agents room to work.** Let routine work flow without constant
supervision, while policy gates catch moments where human judgment, context, or
accountability matter.

**Make human judgment part of the loop.** When a run needs a person, reviewers
see what the agent intends to do, why it matters, and what context led there.
The long-term goal is not just approve/reject, but review, modify, approve,
reject, and keep the run coherent.

**Continue with confidence.** Checkpointed execution lets the agent continue
after a decision instead of restarting from scratch. Traces connect agent
intent, policy decisions, human judgment, tool execution, and final output.

Under the hood, Modi Harness builds this human-aligned runtime layer on
LangChain + LangGraph:

- governed tool execution with policy gates and approvals
- checkpointed run state for pause/resume workflows
- run-scoped workspace persistence
- typed cross-run memory
- output validation against denied side-effect claims
- structured, redacted JSONL traces

Plain agents are still better written on raw LangChain/LangGraph. Reach for
Modi Harness when an agent is about to touch real systems, trigger side
effects, or create decisions that someone may need to audit later.

## Status

**V0.7.1** — discovered Agents are dynamic commands with Agent-driven interactive
startup. Project Agents are found from `modi.toml`; task-aware Agents expose
truthful checkpointed progress to CLI, API, and other clients.

Current implementation covers governed execution, approval interrupts,
checkpointed resume, workspaces, memory, output validation, and structured
traces. The product direction is a more human-centered runtime: editable
reviews, stronger action integrity, richer decision trails, clearer cost
attribution per governed task, and better ways to keep agents aligned with
human goals and boundaries.

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
support = ModiAgent.from_markdown(
    "agents/research_assistant/agent.md",
    tools=[
        ToolBinding(
            spec={
                "name": "search",
                "description": "Search a knowledge base.",
                "input_schema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
                "risk_level": "L1",
                "side_effect": False,
            },
            handler=lambda q: {"hits": []},
        ),
    ],
)

# 3) Session — binds harness, agents, and infra into something runnable.
session = ModiSession(
    harness=harness,
    agents=[support],
    checkpointer=MemorySaver(),
    workspace_root=".modi/workspace",
    memory_root="~/.modi/memory",
)

# 4) Execute — the sole entry point.
response = session.run_task(
    agent="research-assistant",
    input={"goal": "Summarize the latest transformer research."},
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
