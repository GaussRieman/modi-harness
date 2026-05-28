# Model Adapter

## Module

`modi_harness.models`

## Purpose

Normalize model calls and responses. Sole owner of `ContextPack` → provider-message conversion.

Contract: see [`../architecture/09-model-adapter.md`](../architecture/09-model-adapter.md).
Types: see [`../types-reference.md`](../types-reference.md).
Untrusted wrapping: see [`../architecture/15-untrusted-content.md`](../architecture/15-untrusted-content.md).

## Framework Choice

Use LangChain chat models for V0.1.

Model Adapter preserves access to raw LangChain messages/results for advanced integrations while returning normalized Modi `ModelResult` to the harness.

## Design

Implement:

- `ModelAdapter`
- `call(context_pack, options) -> ModelResult`
- `stream(context_pack, options) -> Iterator[StreamEvent]`
- LangChain chat model factory keyed by provider
- `to_langchain_messages(context_pack) -> list[BaseMessage]` (sole conversion entry point)
- tool description binding
- prompt-cache prefix marking when supported
- response normalization
- tool-call extraction (malformed tagged, not retried here)
- structured-output extraction
- usage and cost extraction
- error normalization
- retry policy for transient errors only
- optional single-hop fallback to a secondary provider

## Conversion Rules

- Untrusted blocks wrapped according to the contract document.
- Trusted blocks emitted in order: system + agent + skill + memory.
- Tool descriptions bound to the model in provider-native form.
- The same `ContextPack` always produces the same provider message list (modulo provider non-determinism).

## Rules (impl-specific)

- Model output is a proposal.
- Model-requested tool calls are never executed here.
- Malformed tool calls are returned as `ToolCallProposal.malformed=True`; Runtime Adapter decides repair.
- Missing model settings fail when the adapter is constructed or called.
- V0.1 supports `langchain-openai` first; provider import is lazy.
- Preserve prompts, model name, usage, tool-call metadata for trace and evaluation.
- Retry is per-call and bounded; non-transient errors are not retried.

## Settings

```text
MODI_MODEL_PROVIDER=openai
MODI_MODEL_NAME=
MODI_MODEL_API_KEY=
MODI_MODEL_BASE_URL=
MODI_MODEL_FALLBACK=
MODI_MODEL_RETRY_ATTEMPTS=2
MODI_MODEL_RETRY_BACKOFF=1.5
```

## Tests

- context-to-message conversion correctness
- untrusted blocks wrapped, trusted blocks unwrapped
- tool binding
- tool-call extraction including malformed
- draft output extraction (structured)
- usage and cost extraction
- error normalization for transient vs non-transient
- retry bounded
- fallback single hop with trace event
- stream terminal event equals non-stream result
