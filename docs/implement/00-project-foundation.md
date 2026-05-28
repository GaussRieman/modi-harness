# Project Foundation

## Goal

Create a Python package that uses LangChain and LangGraph as the default agent framework while adding Modi Harness governance around them.

Modi Harness wraps and extends those frameworks rather than hiding them. Advanced users can still bring their own LangChain tools, chat models, and LangGraph components into Modi Harness through adapters.

Types reference: [`../types-reference.md`](../types-reference.md).

## Tooling

- `uv` for environment, dependency, and command management.
- `pyproject.toml` as the project manifest.
- `.env` for local configuration.
- `src/modi_harness/` package layout.
- `.env.example` checked in.
- Sample files under `examples/`.

## Core Dependencies

V0.1:

```text
langchain
langgraph
langchain-openai
pydantic
pydantic-settings
python-dotenv
pyyaml
jsonschema
ulid-py
```

Optional dependencies are added only when a module needs them.

## Package Entry Points

V0.1 exposes:

- Python import: `from modi_harness import ModiHarness`
- CLI smoke entry: `python -m modi_harness`
- Test command: `uv run pytest`

## Settings

Implement `modi_harness.config.Settings` using `pydantic-settings`.

Authoritative settings keys (consolidated from all module docs):

```text
# model
MODI_MODEL_PROVIDER
MODI_MODEL_NAME
MODI_MODEL_API_KEY
MODI_MODEL_BASE_URL
MODI_MODEL_FALLBACK
MODI_MODEL_RETRY_ATTEMPTS
MODI_MODEL_RETRY_BACKOFF

# runtime
MODI_PERMISSION_MODE
MODI_MAX_STEPS
MODI_REPAIR_BUDGET

# storage
MODI_WORKSPACE_ROOT
MODI_WORKSPACE_SNAPSHOT_LIMIT
MODI_TRACE_ROOT
MODI_TRACE_REDACT_KEYS
MODI_TRACE_PAYLOAD_INLINE_LIMIT_BYTES

# loaders
MODI_AGENT_PROJECT_DIR
MODI_AGENT_USER_DIR
MODI_SKILL_PROJECT_DIR
MODI_SKILL_USER_DIR

# tools
MODI_TOOL_TIMEOUT_DEFAULT
MODI_TOOL_RESULT_INLINE_LIMIT_BYTES

# policy
MODI_POLICY_RULE_PACKS

# memory
MODI_MEMORY_ROOT
MODI_MEMORY_PROJECT_KEY
MODI_MEMORY_TOKEN_BUDGET
MODI_MEMORY_PROJECT_HORIZON_DAYS

# hooks
MODI_HOOK_USER_SETTINGS
MODI_HOOK_PROJECT_SETTINGS
MODI_HOOK_TIMEOUT_DEFAULT
MODI_HOOK_PASS_ENV
```

Rules:

- Settings are passed into modules explicitly.
- Modules must not read environment variables directly.
- Missing model config fails when Model Adapter is used, not at import time.

## Core Types

All shared types live in `modi_harness/types.py`, mirroring [`../types-reference.md`](../types-reference.md). Other modules import from there; no module redefines a shared type.

Boundary types (API request/response, settings) use Pydantic models for validation. Internal records use TypedDict or dataclass.

## Framework Integration

LangChain and LangGraph are first-class dependencies in:

- `models/`
- `runtime/`
- `tools/`
- `graph/`

These modules expose compatibility points for raw LangChain/LangGraph usage when safe.

Framework-independent modules:

- `agents/`
- `skills/`
- `context/`
- `policy/`
- `workspace/`
- `output/`
- `config/`
- `trace/`
- `memory/`
- `hooks/`

These modules define Modi Harness governance, storage, validation, and metadata rules.

## Planned Source Layout

```text
src/modi_harness/
├── api/
├── agents/
├── skills/
├── context/
├── runtime/
├── models/
├── tools/
├── graph/
├── policy/
├── workspace/
├── output/
├── trace/
├── memory/
├── hooks/
├── config/
└── types.py
```

## Dependency Direction

```text
api
-> runtime
-> agents / skills / context / models / tools / output
-> memory / hooks
-> policy / workspace / trace / config / types
```

Rules:

- `api/` depends on runtime, not internals.
- `runtime/` orchestrates modules; modules do not call runtime.
- `tools/` calls policy before execution.
- `context/` reads workspace and memory indexes but does not write.
- `models/`, `tools/`, `runtime/`, and `graph/` may import LangChain/LangGraph.
- Governance modules stay independent from framework internals.

## Examples

V0.1 includes minimal examples for:

- one Markdown agent
- one skill package
- one LangChain-compatible tool
- one `.env.example`
- one smoke run

See [`../agents/`](../agents/) for the multi-domain agent library and [`../scenarios/`](../scenarios/) for end-to-end run fixtures.

## Implementation Order

1. Project foundation and typed settings.
2. Core types module mirroring `types-reference.md`.
3. Agent Loader.
4. Skill Loader.
5. Workspace Manager.
6. Memory Store.
7. Trace Recorder.
8. Hook System.
9. Policy Gate.
10. Tool Gateway.
11. Context Manager.
12. Model Adapter.
13. Output Controller.
14. Runtime Adapter.
15. Harness API.
16. Evaluation fixtures and smoke examples.

## Developer Workflow

Expected local commands:

```text
uv sync
uv run pytest
uv run python -m modi_harness
```

V0.1 includes `.env.example`, sample agents, sample skills, and a runnable smoke test.

## Framework Policy

- Runtime graph: LangGraph first.
- Model access: LangChain first.
- Tool integration: LangChain-compatible tools first.
- Checkpointing and interrupts: LangGraph primitives where practical.
- Custom implementation acceptable for loaders, config, policy, workspace, output validation, memory, hooks, and trace.
- Custom implementation also acceptable when a framework abstraction would make the harness harder to reason about.
