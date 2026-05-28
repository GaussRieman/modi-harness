# Tool Gateway

## Module

`modi_harness.tools`

## Purpose

Validate and govern model-requested tool calls.

## Framework Choice

Use LangChain tool interfaces by default.

Tool Gateway should accept LangChain tools, expose LangChain-compatible tool descriptions, and still support local callables through `ToolSpec`.

## Design

Implement:

- `ToolRegistry`
- `ToolGateway`
- `register_tool(spec, handler)`
- `register_langchain_tool(tool, spec_overrides=None)`
- `execute_tool_call(tool_call, state) -> ToolResult`
- schema validation
- policy call
- timeout handling
- result normalization

## Rules

- Unknown tools fail closed.
- LangChain tools still pass through Modi policy before execution.
- All tool execution passes through Policy Gate.
- Tool results are untrusted observations.
- Hook blocks return structured errors.
- Prompt-injection findings are surfaced, not executed.
- Denied-retry check runs before execution.

## Tests

- unknown tool
- invalid schema
- allowed tool execution
- policy denial
- approval interrupt
- normalized tool error
