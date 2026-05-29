# Model Adapter

> **V0.3 status:** Model Adapter now supports async (`acall`, `astream`),
> multi-provider (OpenAI + Anthropic via `create_chat_model` factory), retry
> with configurable attempts/backoff, and prompt cache marking on system prefix.

Model Adapter is the **only** module that converts Modi `ContextPack` into provider-specific messages, the only module that calls the chat model, and the only module that normalizes the response into `ModelResult`.

See [`types-reference.md`](../types-reference.md) for `ModelResult`, `ToolCallProposal`, `ModelUsage`, `SafetySignal`, `Message`.

## Responsibilities

- Convert `ContextPack` → LangChain message list (or provider-native equivalent).
- Wrap untrusted blocks according to [`15-untrusted-content.md`](./15-untrusted-content.md).
- Bind tool descriptions to the chat model.
- Call the model (sync via `call()`, async via `acall()`).
- Expose streaming variants: sync iterator via `stream()`, async iterator via `astream()`.
- Parse tool calls, structured output, and streaming events into normalized `ModelResult`.
- Extract usage and cost.
- Implement retry within bounded scope (see Retry section).
- Mark prompt cache boundaries on the system prefix (see Prompt Caching section).

## `create_chat_model` Factory

```python
model = create_chat_model(provider, model_name, **kwargs)
```

Multi-provider support (V0.3): the factory returns a configured LangChain chat
model for the requested provider. Currently supported:

- `"openai"` — via `langchain-openai`
- `"anthropic"` — via `langchain-anthropic`

Provider import remains lazy; the adapter compiles with no provider installed.
The factory validates settings at construction time.

## Conversion

Conversion is one-way at the boundary. Internal modules continue to operate on `ContextPack` and `Message`; only Model Adapter sees provider message shapes.

## Prompt Caching

When a provider supports prompt caching, Model Adapter:

- marks the trusted system+agent+skill prefix with `cache_control` as cacheable
- marks the standing untrusted-content note as cacheable
- excludes per-step state and recent messages from the cache prefix

Cache marking is implemented via `cache_control` annotations on system prefix
messages. This is active for Anthropic models; OpenAI models use the provider's
automatic prefix caching.

## Retry

- Retry policy is per-call, derived from settings and tool/call metadata.
- Configurable: `max_attempts` (default 3) and `backoff_factor` (default 1.5s exponential).
- Retries apply only to **transient** errors: timeout, rate limit, transient 5xx, connection reset. They never apply to malformed tool calls, refusals, content filtering, or non-transient 4xx.
- Tool-call malformation is **not** retried by the adapter. It is surfaced as `ToolCallProposal.malformed=True` and `parse_error`; Runtime Adapter decides whether to repair through another model step.

## Deferred to V0.4

- Fallback to a secondary provider (previously `MODI_MODEL_FALLBACK`).
- Error code normalization (mapping provider errors to a unified set).
- Request metadata preservation (model name, message hash, sampling params, seed).

## Streaming

When streaming is requested, Model Adapter exposes:

- `stream()` — sync iterator of normalized stream events.
- `astream()` — async iterator of normalized stream events (V0.3).

The terminal event always carries a complete `ModelResult`. Streaming is a transport detail; downstream modules see only the final `ModelResult` unless Harness API has subscribed.

## Rules

- Model output is a proposal, not authority.
- Model-requested tool calls are never executed by Model Adapter; they leave through `ModelResult.tool_calls`.
- Untrusted wrapping is enforced here. A trusted block must never end up inside an untrusted wrapper, and vice versa.
- Missing model settings fail at adapter construction.
- V0.3 supports OpenAI and Anthropic providers via `create_chat_model`. Provider import is lazy; adapter compiles with no provider installed.
- Model Adapter does not access Workspace, Memory, or Policy Gate directly.

## Boundaries

- Context assembly: Context Manager.
- Tool execution: Tool Gateway.
- Output validation: Output Controller.
- Repair / multi-step recovery: Runtime Adapter.
