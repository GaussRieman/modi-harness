# DuckDuckGo Search Health Design

## Problem

`public_web_research("威灿科技")` reported that DuckDuckGo returned no results,
while the same unquoted query in DuckDuckGo HTML search returned the official
杭州威灿科技有限公司 site as the first result, followed by matching company and
recruiting pages. The official site was reachable and contained company,
solution, and project information.

The query and current `result__a` parser both match the live result page. The
remaining failure boundary is the HTTP search response: a degraded, blocked,
or otherwise non-result page is currently indistinguishable from a healthy
zero-result response. `_search_provider` collapses every parsed empty list into
`search returned no results`, and the final validator counts provider records
without proving those providers returned healthy search pages.

## Constraints

- Keep the one-Node Workflow and sole `public_web_research` Operation.
- Keep search zero-key and dependency-free.
- Use DuckDuckGo public HTML endpoints; do not add a browser runtime to the CLI.
- Preserve bounded queries, fetches, timeouts, and source excerpts.
- Do not add compatibility aliases for the current internal record shape.

## Design

### DuckDuckGo transport

Use a normal browser-compatible request profile for public HTML search:

- a mainstream desktop `User-Agent`;
- `Accept: text/html,...`;
- `Accept-Language: zh-CN,zh;q=0.9,en;q=0.7`;
- no compressed-response negotiation that the standard-library client does not
  explicitly decode.

Search `html.duckduckgo.com` first. If its response is blocked, malformed, or
missing both result and recognized no-result structures, retry once against
`lite.duckduckgo.com`. A confidently recognized healthy empty page does not
retry.

### Search record health

Every search record adds a required status:

```text
ok       parsed at least one search result
empty    healthy provider page explicitly reported no results
blocked  login, CAPTCHA, bot challenge, or access-control response
failed   network, decode, XML, or unrecognized response failure
```

`blocked` and `failed` records retain a specific error. An empty parsed list is
never sufficient to produce `empty`; the adapter must recognize the provider's
normal no-results response. The record's `search_url` is the endpoint that
produced the final status, including the Lite fallback when used.

The Operation summary includes `healthy_provider_count`, counting distinct
providers with at least one `ok` or `empty` record.

### Query variants

Preserve the original subject in both bounded variants:

```text
威灿科技
"威灿科技" 公司
```

When the user asks a specific dimension, replace `公司` in the second query
with the compact dimension. Do not reduce a short brand such as `威灿科技` to
`威灿`, because that loses the strongest identity token and increases noise.

### Completion safety

Positive completion remains source-bound: factual results cite fetched URLs in
the final `sources` list.

A negative match conclusion requires records from at least two distinct healthy
providers. `blocked` and `failed` providers do not count. If fewer than two
providers are healthy, the Agent may complete only with an inconclusive search
limitation such as “本次搜索服务不可用，无法判断是否存在可靠公开匹配”; it may not say
that the bounded search found no match.

The validator checks required status values and enforces the healthy-provider
rule. The Skill tells the Brain to distinguish `search unavailable` from
`healthy search found no relevant match`.

## Data Flow

```text
subject
  -> two identity-preserving queries
  -> provider adapter
       -> browser-compatible request
       -> response health classification
       -> DuckDuckGo Lite fallback when unhealthy
  -> status-bearing SearchRecords
  -> relevance ranking and bounded source fetch
  -> source-bound answer or qualified inconclusive result
  -> complete_node validator
```

## Tests

- Parse a fixture matching the live DuckDuckGo `result__a` redirect structure
  and recover the `hitopking.com` target for `威灿科技`.
- Assert DuckDuckGo requests carry the browser-compatible headers.
- Assert a degraded HTML shell triggers the Lite fallback.
- Assert a recognized no-result page produces `empty` without fallback.
- Assert challenge and unrecognized pages produce `blocked` or `failed`, never
  `empty`.
- Assert short-name query variants preserve `威灿科技`.
- Assert negative completion rejects two failed provider records and accepts
  two healthy provider records with no relevant match.
- Keep the existing single-Operation/two-Brain-Step runtime test and run the
  complete suite.
- Perform a live CLI check with `威灿科技`; the expected minimum result is a
  fetched, cited match to `http://www.hitopking.com/`.

## Out of Scope

- Search API keys or paid providers.
- Browser automation inside the Agent runtime.
- More Workflow Nodes or Agent-selectable search/fetch tools.
- General Web crawling, semantic reranking, or dynamic provider plugins.
