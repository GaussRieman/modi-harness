# Model Adapter

Model Adapter is the **only** module that converts Modi `ContextPack` into provider-specific messages, the only module that calls the chat model, and the only module that normalizes the response into `ModelResult`.

See [`types-reference.md`](../types-reference.md) for `ModelResult`, `ToolCallProposal`, `ModelUsage`, `SafetySignal`, `Message`.

## Responsibilities

- Convert `ContextPack` → LangChain message list (or provider-native equivalent).
- Wrap untrusted blocks according to [`15-untrusted-content.md`](./15-untrusted-content.md).
- Bind tool descriptions to the chat model.
- Call the model.
- Parse tool calls, structured output, and streaming events into normalized `ModelResult`.
- Extract usage and cost.
- Map provider errors to a normalized set of error codes.
- Implement retry and fallback within bounded scope (see Rules).

## Conversion

Conversion is one-way at the boundary. Internal modules continue to operate on `ContextPack` and `Message`; only Model Adapter sees provider message shapes.

When a provider supports prompt caching, Model Adapter:

- marks the trusted system+agent+skill prefix as cacheable
- marks the standing untrusted-content note as cacheable
- excludes per-step state and recent messages from the cache prefix

## Retry and Fallback

- Retry policy is per-call, derived from settings and tool/call metadata.
- Retries apply only to **transient** errors: timeout, rate limit, transient 5xx, connection reset. They never apply to malformed tool calls, refusals, content filtering, or non-transient 4xx.
- Fallback to a secondary provider is allowed only when configured in settings as `MODI_MODEL_FALLBACK`. Fallback is a single hop and is recorded as a trace event.
- Tool-call malformation is **not** retried by the adapter. It is surfaced as `ToolCallProposal.malformed=True` and `parse_error`; Runtime Adapter decides whether to repair through another model step.

## Streaming

When `RunTaskRequest.options.stream` is set, Model Adapter exposes an iterator of normalized stream events. The terminal event always carries a complete `ModelResult`. Streaming is a transport detail; downstream modules see only the final `ModelResult` unless Harness API has subscribed.

## Rules

- Model output is a proposal, not authority.
- Model-requested tool calls are never executed by Model Adapter; they leave through `ModelResult.tool_calls`.
- Untrusted wrapping is enforced here. A trusted block must never end up inside an untrusted wrapper, and vice versa.
- Missing model settings fail at adapter construction.
- V0.1 supports one primary provider (`langchain-openai`). Adapter must compile with no provider installed; provider import is lazy.
- Preserve enough metadata to reproduce the request: model name, tool list, message hash, sampling params, seed when applicable.
- Model Adapter does not access Workspace, Memory, or Policy Gate directly.

## Boundaries

- Context assembly: Context Manager.
- Tool execution: Tool Gateway.
- Output validation: Output Controller.
- Repair / multi-step recovery: Runtime Adapter.
