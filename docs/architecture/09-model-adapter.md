# Model Adapter

> **V0.4a status:** Model Adapter adds per-agent provider override (agent YAML
> `model:` block with `${VAR}` env expansion), single-hop fallback after retries
> on transient errors, normalized error codes (`ModelErrorCode` enum +
> `ModelError` exception + `classify_error`), and `ModelAdapterCache` keyed by
> `(provider, name, base_url)`.
>
> **V0.3 status:** Model Adapter supports async (`acall`, `astream`),
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
- Honor per-agent provider overrides through `ModelAdapterCache` (see Per-Agent Provider Override).
- Fall back to a configured secondary provider on transient errors after retries are exhausted (see Fallback).
- Normalize provider exceptions into a flat `ModelErrorCode` enum and raise `ModelError` (see Error Normalization).

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
- Retries apply only to **transient** errors as classified by `classify_error`:
  `ModelErrorCode.TIMEOUT`, `RATE_LIMITED`, `SERVER_ERROR`. They never apply to
  `AUTH_FAILED`, `CONTENT_FILTERED`, `CONTEXT_LENGTH_EXCEEDED`, or `UNKNOWN`.
- Retries do not apply to malformed tool calls, refusals, or non-transient 4xx.
- Tool-call malformation is **not** retried by the adapter. It is surfaced as `ToolCallProposal.malformed=True` and `parse_error`; Runtime Adapter decides whether to repair through another model step.

## Per-Agent Provider Override (V0.4a, IMPLEMENTED)

An individual agent may override the global model settings via an optional
`model:` block in its frontmatter:

```yaml
model:
  provider: anthropic
  name: claude-sonnet-4-20250514
  api_key: ${ANTHROPIC_API_KEY}
  base_url: ""
  fallback:
    provider: openai
    name: gpt-4o
    api_key: ${OPENAI_API_KEY}
```

- All fields are optional. Missing fields inherit from the global `ModelSettings`.
- `${VAR_NAME}` strings are expanded against `os.environ` at parse time; missing
  variables expand to the empty string.
- Agent Loader stores the parsed (env-expanded) dict at
  `AgentProfile["metadata"]["model"]`.

The `model_turn` graph node reads `profile["metadata"].get("model")` and
delegates to `ModelAdapterCache.get_or_create(...)` to obtain the effective
adapter for that agent.

### `ModelAdapterCache`

```python
class ModelAdapterCache:
    def __init__(self, global_settings: ModelSettings, *, default_adapter=None) -> None: ...
    def get_or_create(self, agent_model_config: dict | None) -> ModelAdapter
```

- Caches adapters by `(provider, name, base_url)` so repeated agent invocations
  reuse the same `ModelAdapter` instance (no per-call construction cost).
- When `agent_model_config` is `None` or empty, returns the cached global
  default adapter.
- Otherwise merges the per-agent dict over global defaults (agent values
  override when non-empty) and constructs the adapter via `create_chat_model`.
- The cache lives on `GraphDeps.model_cache`.

## Fallback (V0.4a, IMPLEMENTED)

After primary retries are exhausted on a **transient** error, the adapter may
attempt a single hop to a configured secondary provider.

Configuration sources (checked in order):

1. Per-agent fallback: `model.fallback` sub-block in the agent YAML (same
   shape as the primary model block, minus a nested `fallback`).
2. Global fallback: `MODI_MODEL_FALLBACK_PROVIDER`, `MODI_MODEL_FALLBACK_NAME`,
   `MODI_MODEL_FALLBACK_API_KEY`, `MODI_MODEL_FALLBACK_BASE_URL`.

If neither is configured, the original `ModelError` is raised.

Behavior:

- Trigger condition: primary call exhausted retries on a transient error
  (`TIMEOUT`, `RATE_LIMITED`, `SERVER_ERROR`). Non-transient codes
  (`AUTH_FAILED`, `CONTENT_FILTERED`, `CONTEXT_LENGTH_EXCEEDED`) never trigger
  fallback.
- The fallback model is constructed via `create_chat_model` and called once.
  No retry on the fallback attempt; no chaining (single hop).
- On success, `ModelResult["fallback_used"]` is set to `True` and the
  `model_turn` node emits a `model_fallback` trace event with payload
  `{fallback_provider, fallback_name}`.
- On fallback failure, the fallback exception is classified and raised as
  `ModelError` (with the fallback's `code`, `provider`, and `original`).

## Error Normalization (V0.4a, IMPLEMENTED)

Provider exceptions are mapped to a flat enum so retry, fallback, and error
reporting share one classification.

### `ModelErrorCode`

```python
class ModelErrorCode(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    CONTENT_FILTERED = "content_filtered"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"
```

### `ModelError`

```python
class ModelError(Exception):
    code: ModelErrorCode
    original: Exception | None
    provider: str
    message: str
```

Raised by Model Adapter when a model call fails after retries (and fallback,
if configured) are exhausted.

### `classify_error`

```python
def classify_error(exc: Exception) -> ModelErrorCode
```

Maps provider exceptions to a normalized code via type checks plus
case-insensitive substring/regex matching on the exception message:

- `TimeoutError` or `"timeout"` → `TIMEOUT`
- HTTP 429 or `"rate limit"` → `RATE_LIMITED`
- HTTP 401 / 403, `"auth"`, `"permission"`, `"api key"` → `AUTH_FAILED`
- `"content filter"`, `"safety"`, `"blocked"` → `CONTENT_FILTERED`
- `"context length"`, `"token limit"`, `"max ... token"` → `CONTEXT_LENGTH_EXCEEDED`
- HTTP 5xx, `ConnectionError`, `"server error"` → `SERVER_ERROR`
- Anything else → `UNKNOWN`

The adapter's transient check is unified through this function:

```python
_TRANSIENT_CODES = {ModelErrorCode.TIMEOUT, ModelErrorCode.RATE_LIMITED, ModelErrorCode.SERVER_ERROR}

def _is_transient(self, exc: Exception) -> bool:
    return classify_error(exc) in _TRANSIENT_CODES
```

## Deferred to a future release

- Multi-hop fallback chains.
- Per-tool-call model selection.
- Cost tracking / budget enforcement.
- Streaming fallback (if a primary stream fails mid-token, no recovery).
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
- V0.4a: per-agent overrides go through `ModelAdapterCache`; `model_turn_node`
  never calls `create_chat_model` directly.
- Model Adapter does not access Workspace, Memory, or Policy Gate directly.

## Boundaries

- Context assembly: Context Manager.
- Tool execution: Tool Gateway.
- Output validation: Output Controller.
- Repair / multi-step recovery: Runtime Adapter.
