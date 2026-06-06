# Modi Harness

**An AI-native agent harness, engineered for token efficiency.**

Running an agent is easy. Running it *efficiently* — at scale, across
providers — is the hard part. Modi Harness is the execution layer that makes
token efficiency real.

It is the execution end of the **Modi project**, an enterprise
token-efficiency platform — in production and still evolving — that chains a
provider gateway → request logging → cost analysis → token-policy optimization
→ **Modi Harness**. The upstream stages observe spend and decide *how* to use
fewer tokens; Modi Harness is where those decisions run, turning optimization
policy into executing agents.

**Efficiency by design.** Modi Harness talks to each provider's API directly —
OpenAI and Anthropic today, more in progress — and applies provider-specific
optimizations such as prompt caching, so a run costs less on every backend it
targets.

**AI-native.** A small, typed, well-documented API with lean dependencies and
explicit contracts — designed to be read, extended, and driven by coding
agents, not just people.

Because agents that spend efficiently still have to act safely, Modi Harness
ships the controls for it on top of LangChain + LangGraph:

- governed tool execution with approvals
- run-scoped workspace persistence
- typed cross-run memory
- output validation against denied side-effects
- structured, redacted JSONL traces

Plain agents are still better written on raw LangChain/LangGraph — reach for
Modi Harness when efficiency, scale, and control begin to matter.

## Status

**V0.5.0** — shipped. Public API reshaped into three objects (`ModiHarness` +
`ModiAgent` + `ModiSession`); `RuntimeAdapter` renamed to `HarnessGraphAdapter`;
plugin manifest reshaped; execution moved to `ModiSession`. Suite green.
See [`docs/development-plan.md`](docs/development-plan.md) and
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
    "examples/research_assistant_simple/agents/research-assistant.md",
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
`ModiAgent.load_dir("examples/research_assistant_simple/agents")`, or let
`ModiSession.from_discovery(harness, agents_dir=..., plugins=...)` discover
plugin-contributed and directory agents together.

Runnable end-to-end demos live under [`examples/`](examples/) — each has a
`run.py` that wires a real chat model, agents, tools, and a session
(`research_assistant`, `research_assistant_simple`, `code_auditor`).

## CLI

```bash
modi run --agent research-assistant \
    --agents-dir examples/research_assistant_simple/agents \
    --task task.json
modi info
modi --version
```

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

- [Documentation Index](docs/table_of_contents.md)
- [Development Plan](docs/development-plan.md)
- [Architecture Overview](docs/architecture/README.md)
- [Authoritative Types Reference](docs/types-reference.md)
- [Examples](examples/)
- [Changelog](CHANGELOG.md)

## License

MIT
