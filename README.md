# Modi Harness

LangChain + LangGraph runtime kernel for governed, locally-defined agents.

Modi Harness sits **on top of** LangChain and LangGraph, adding a governance
layer for users who need:

- Markdown-defined agents
- skill packages
- governed tool execution with approvals
- workspace persistence
- typed cross-run memory
- user-configurable hooks
- output validation
- traceable, replayable runs

Simple agents should still be written with raw LangChain/LangGraph; Modi
Harness is for cases that need stronger governance, durable workspace state,
and audit trails.

## Status

**V0.1** — feature-complete. All 6 milestones (M0 foundation → M6 evaluation)
are done; the four sample agents and six smoke scenarios pass end-to-end.
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

```python
from langchain_openai import ChatOpenAI
from modi_harness import ModiHarness

harness = ModiHarness(
    agents_dir="docs/agents",
    workspace_root=".modi/workspace",
    memory_root="~/.modi/memory",
    chat_model=ChatOpenAI(model="gpt-4o-mini"),
)

harness.register_tool(
    {
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
    lambda q: {"hits": []},
)

response = harness.run_task(
    agent="support-bot",
    input={"customer_message": "I was charged twice.", "account_id": "acct_123"},
)
print(response)
```

The four shipped sample agents (`support-bot`, `research-assistant`,
`case-reviewer`, `release-coordinator`) live under [`docs/agents/`](docs/agents/);
their default scenarios are in [`docs/scenarios/`](docs/scenarios/).

## CLI

```bash
modi run --agent support-bot --task docs/scenarios/support-bot-default/task.json
modi run --agent research-assistant --task docs/scenarios/research-assistant-default/task.json --permission-mode plan
modi info
modi --version
```

## Architecture in 10 Seconds

```
Harness API
  -> Runtime Adapter (single-agent loop)
      -> Agent Loader / Skill Loader
      -> Memory Store
      -> Context Manager
      -> Model Adapter (LangChain chat models)
      -> Tool Gateway (LangChain tools)
          -> Hook System
          -> Policy Gate (mode-aware decisions, rule packs)
      -> Output Controller
  -> Workspace Manager (run-scoped storage)
  -> Trace Recorder (JSONL, redaction, replay)
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
- [Sample Agents](docs/agents/README.md)
- [Sample Scenarios](docs/scenarios/README.md)
- [Changelog](CHANGELOG.md)

## License

MIT
