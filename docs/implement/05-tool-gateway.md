# Tool Gateway

## Module

`modi_harness.tools`

## Purpose

Validate and govern model-requested tool calls.

Contract: see [`../architecture/05-tool-gateway.md`](../architecture/05-tool-gateway.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Framework Choice

Use LangChain tool interfaces by default. Tool Gateway accepts LangChain tools and local callables.

## Design

Implement:

- `ToolRegistry`
- `ToolGateway`
- `register_tool(spec, handler)`
- `register_langchain_tool(tool, spec_overrides=None)`
- `execute_tool_call(proposal, state) -> ToolCallRecord`
- schema validation (JSON Schema)
- visibility re-check
- denied-retry guard
- hook dispatch wrappers (`pre_tool_use`, `post_tool_use`)
- Policy Gate call
- timeout handling
- result normalization with trust annotation
- size-threshold offload to Workspace
- idempotency cache (per run, by fingerprint)
- dry-run dispatch for `plan` mode when tool supports it

## Rules (impl-specific)

- Unknown tools fail closed with a structured error.
- LangChain tool wrappers still pass through Modi policy and hooks.
- Tool results above `MODI_TOOL_RESULT_INLINE_LIMIT_BYTES` go to Workspace and are returned as `workspace_ref`.
- Hook blocks return a structured error and signal `DeniedAction` to Runtime Adapter.
- Idempotent results are cached only within the same run.

## Settings

```text
MODI_TOOL_TIMEOUT_DEFAULT=30
MODI_TOOL_RESULT_INLINE_LIMIT_BYTES=8192
```

## Tests

- unknown tool
- invalid schema
- allowed tool execution end-to-end
- policy denial
- approval interrupt
- normalized tool error
- LangChain tool through gateway
- denied-retry rejected before policy
- dry-run in plan mode
- large result offloaded to workspace
- idempotent call returns cached result
