# Project Foundation

## Goal

Create a Python package that uses LangChain and LangGraph as the default agent framework while adding Modi Harness governance around them.

Modi Harness should be easy to use for advanced users who already know LangChain and LangGraph. It should wrap and extend those frameworks, not hide them unnecessarily.

## Tooling

- Use `uv` for environment, dependency, and command management.
- Use `pyproject.toml` as the project manifest.
- Use `.env` for local configuration.
- Use `src/modi_harness/` package layout.
- Provide `.env.example`.
- Provide sample files under `examples/`.

## Core Dependencies

Preferred V0.1 dependencies:

```text
langchain
langgraph
langchain-openai
pydantic
pydantic-settings
python-dotenv
pyyaml
```

Optional dependencies should be added only when a module needs them.

## Package Entry Points

V0.1 should expose:

- Python import: `from modi_harness import ModiHarness`
- CLI smoke entry: `python -m modi_harness`
- test command: `uv run pytest`

## Settings

Implement `modi_harness.config.Settings`.

Settings should load:

- model provider, name, API key, base URL
- workspace root
- trace root
- permission mode
- max runtime steps

Rules:

- Settings are passed into modules explicitly.
- Modules must not read environment variables directly.
- Missing model config should fail when Model Adapter is used, not during import.

## Core Types

Put shared contracts in `types.py` or small focused files:

- `AgentProfile`
- `LoadedSkill`
- `ContextPack`
- `AgentState`
- `ToolSpec`
- `PolicyDecision`
- `WorkspaceRef`
- `ModelResult`
- `OutputValidationResult`
- `TraceEvent`

Use Pydantic models when validation matters at boundaries. Use dataclasses or TypedDicts for simple internal records.

## Framework Integration

LangChain and LangGraph are first-class dependencies in:

- `models/`
- `runtime/`
- `tools/`
- `graph/`

These modules should expose compatibility points for raw LangChain/LangGraph usage where useful.

Framework-independent modules:

- `agents/`
- `skills/`
- `context/`
- `policy/`
- `workspace/`
- `output/`
- `config/`
- `trace/`

These modules stay independent because they define Modi Harness governance, storage, validation, and metadata rules.

## Examples

Add minimal examples for:

- one Markdown agent
- one skill package
- one LangChain-compatible tool
- one `.env.example`
- one smoke run
