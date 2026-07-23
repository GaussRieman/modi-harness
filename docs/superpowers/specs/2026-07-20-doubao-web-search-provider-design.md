# Doubao Web Search Provider Design

## Decision

Add Volcengine Ark Web Search as an optional fourth provider inside the
Research Assistant's existing search fan-out. The provider is active only when
`MODI_DOUBAO_SEARCH_API_KEY` is configured. Bing RSS, Baidu HTML, and
DuckDuckGo remain available and continue to carry the request when Doubao is
unconfigured or fails.

Doubao is a candidate-discovery source, not a trusted answer generator. Only
URLs and titles from response citation annotations enter the existing ranking
pipeline. Every selected URL must still be fetched by Modi and pass the
existing evidence-verification boundary.

## Scope

Included:

- typed `.env` configuration under `ToolSettings`;
- a small raw-HTTP Ark Responses adapter without a new SDK dependency;
- optional provider activation in both `public_web_research` and
  `public_web_search`;
- citation extraction, usage metadata, provider health, and failure isolation;
- regression tests for configuration, request construction, response parsing,
  secret isolation, provider merging, and fallback behavior.

Excluded:

- replacing the current free providers;
- treating Doubao-generated prose as evidence;
- exposing a new model-selectable Operation;
- adding Douyin, Moji, Toutiao, image search, or user-location controls;
- retrying a possibly billable POST after an ambiguous transport failure;
- changing Task Graph, search budgets, ranking policy, or evidence schemas.

## Configuration

Add these entries to `ToolSettings` and `.env.example`:

```text
MODI_DOUBAO_SEARCH_API_KEY=
MODI_DOUBAO_SEARCH_MODEL=doubao-seed-2-1-pro-260628
MODI_DOUBAO_SEARCH_BASE_URL=https://ark.cn-beijing.volces.com/api/v3/responses
MODI_DOUBAO_SEARCH_TIMEOUT=20
MODI_DOUBAO_SEARCH_MAX_KEYWORD=1
MODI_DOUBAO_SEARCH_LIMIT=6
MODI_DOUBAO_SEARCH_MAX_TOOL_CALLS=1
```

The API key alone enables the provider. Other values have bounded defaults.
Validation requires an HTTPS endpoint without credentials or fragments,
rejects non-positive timeouts, and rejects values outside the documented Ark
ranges.

The Agent factory resolves its project root from `PACKAGE_DIR`, loads
`Settings(_env_file=project_root / ".env")` once, and converts the relevant
`ToolSettings` fields into an immutable `DoubaoSearchConfig`. It binds that
config to the two research handlers through wrappers whose public signatures
remain the declared Operation schemas. The handler closure and config are not
part of ToolSpec, execution-contract, checkpoint, or trace serialization.
This avoids process-working-directory lookup and preserves the existing
`.env` versus real `MODI_*` precedence even when Modi starts in a subdirectory.
Direct calls to the unbound research functions use a disabled config, keeping
unit tests and library calls deterministic unless a caller binds one.

The key is used only to construct the `Authorization: Bearer` header. It must
not appear in a request record, response record, exception message,
checkpoint, trace, or test assertion.

The HTTP client rejects every redirect rather than forwarding an authenticated
request. Any 3xx response becomes a provider failure. Combined with mandatory
HTTPS, this prevents an endpoint from redirecting the bearer credential to a
different origin or downgrading it to plaintext transport.

## Provider Boundary

Create `agents/research_assistant/tools/doubao.py` with three isolated duties:

1. represent the already-loaded immutable Doubao search configuration;
2. build and send one Ark Responses request;
3. parse a JSON response into the existing provider-record result shape.

`research.py` remains responsible for provider fan-out, candidate ranking,
page fetching, source limits, and evidence-facing operation output.
`agent.py` alone owns project-root configuration loading and handler binding;
the provider adapter never constructs `Settings()` itself.

The active provider list is computed once per search Operation:

```text
bing_rss + baidu + duckduckgo
  + doubao only when API key is non-empty
```

One structured Modi query maps to one Ark response request. The request uses:

```json
{
  "model": "<configured model>",
  "stream": false,
  "tools": [{"type": "web_search", "max_keyword": 1, "limit": 6}],
  "max_tool_calls": 1,
  "input": [
    {
      "role": "system",
      "content": [{"type": "input_text", "text": "Use web_search once and cite sources."}]
    },
    {
      "role": "user",
      "content": [{"type": "input_text", "text": "<exact Modi query>"}]
    }
  ]
}
```

`max_keyword=1` and `max_tool_calls=1` prevent one provider job from silently
expanding into an unbounded multi-query research loop. No `caching` parameter
is sent because the reference API rejects it.

## Response Normalization

The adapter scans `output[]` for:

- `web_search_call` items, including the actual query when present;
- `message.content[]` output text blocks;
- `annotations[]` whose type is `url_citation`.

Both direct citation fields and a nested `url_citation` object are accepted to
cover compatible Responses serializers. A citation becomes:

```json
{"title": "source title", "url": "https://...", "snippet": ""}
```

The model-authored answer is never copied into `snippet` or source content.
URLs are restricted to HTTP(S), deduplicated in response order, and capped at
the configured limit. The normal Research Assistant canonicalization and
ranking logic performs the next deduplication boundary across providers.

Provider status is:

- `ok`: at least one valid citation;
- `empty`: a web search call completed but exposed no valid citation;
- `failed`: the model did not invoke search, the response was malformed, or a
  transport/server error occurred;
- `blocked`: Ark returned 401, 403, or 429.

The provider record may include non-secret `usage.tool_usage`,
`usage.tool_usage_details`, and actual query metadata for cost observability.
It must never include generated answer text or the API key.

## Failure And Cost Policy

Doubao failure is isolated exactly like a free-provider failure. It contributes
one failed provider record and does not raise out of `_run_searches`. Healthy
free results continue through ranking and fetching.

The adapter does not retry POST requests. A response may be billable even when
the client loses the connection, so an automatic retry could duplicate cost.
The existing per-Task follow-up search budget remains the only higher-level
retry boundary.

Doubao does not receive a ranking override merely because it is paid. Its
citations compete under the same entity, dimension, domain-diversity, and
source-quality rules as all other candidates.

## Testing

Tests use mocked HTTP responses and never call Ark. Coverage must prove:

- settings defaults, `.env` loading, environment precedence, and bounds;
- no API key means exactly the existing three providers;
- a configured key adds one Doubao job per query;
- request limits are fixed to the configured bounded values;
- direct and nested citation annotations normalize correctly;
- generated answer text is not accepted as source evidence;
- invalid and duplicate URLs are discarded;
- 401/403/429 are blocked, other HTTP/JSON failures are failed;
- a Doubao failure leaves healthy free-provider results usable;
- operation summaries report the actual active-provider health;
- API keys never appear in returned records or exception text.

The full suite, Ruff, and mypy are unconditional completion gates. When the
user has supplied a key and authorizes a billable network call, one opt-in live
smoke test also runs with one keyword and one tool call. Absence of paid
credentials does not block normal completion. A live test must not persist the
key or raw generated answer.
