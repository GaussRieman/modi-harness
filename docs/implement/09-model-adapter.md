# Model Adapter

## Module

`modi_harness.models`

## Purpose

Normalize model calls and responses.

## Framework Choice

Use LangChain chat models for V0.1.

Model Adapter should preserve access to raw LangChain messages/results for advanced integrations while returning normalized Modi `ModelResult` to the harness.

## Design

Implement:

- `ModelAdapter`
- LangChain chat model factory
- `call(context_pack) -> ModelResult`
- provider factory from settings
- tool description binding
- LangChain message conversion
- response normalization
- usage extraction
- structured error mapping

## Rules

- Model output is a proposal.
- Model-requested tool calls are never executed directly.
- Malformed tool calls are returned as diagnostics.
- Missing model settings fail when the adapter is constructed or called.
- V0.1 supports one provider path first, preferably `langchain-openai`.
- Preserve prompts, model name, usage, and tool-call metadata for trace and evaluation.

## Tests

- context-to-message conversion
- tool binding
- tool-call extraction
- draft output extraction
- model error normalization
- usage metadata extraction
