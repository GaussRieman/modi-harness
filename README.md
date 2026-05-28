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

V0.1 — under active development. See [docs/development-plan.md](docs/development-plan.md)
for the milestone roadmap.

## Quickstart

```bash
uv sync
cp .env.example .env
uv run pytest
uv run python -m modi_harness
```

## Documentation

- [Documentation Index](docs/table_of_contents.md)
- [Development Plan](docs/development-plan.md)
- [Architecture Overview](docs/architecture/README.md)
- [Authoritative Types Reference](docs/types-reference.md)
- [Sample Agents](docs/agents/README.md)
- [Sample Scenarios](docs/scenarios/README.md)

## License

MIT
