---
name: query-planning
description: Plan current, entity-aware public-Web queries before Research Assistant searches.
risk_notes:
  - Query plans are search instructions, not factual evidence.
tags:
  - research
  - query
---

# Query Planning

## Sequence

- Call `public_web_search` directly. Runtime obtains current time and injects a
  fresh single-use `time_token` for every search invocation, including a
  follow-up. Never author or reuse the token.

## Structured Searches

- For `public_web_search`, provide `searches`, not a flat keyword list.
- Create one search item per concrete target. A comparison between Tesla Model
  Y and Xiaomi YU7 normally uses two items in the same batch. Two distinct
  companies or subtopics may share one broader entity category, but their
  queries and aliases must remain target-specific.
- Each item contains `query`, `entity`, `aliases`, and one `dimension`.
- Preserve the exact entity phrase. Treat `Model Y`, `Model 3`, and similar
  short model names as complete phrases; never reduce `Model Y` to `tesla` and
  `model`, and never use `Y` alone as an identity keyword.
- Add useful aliases across languages and spacing variants, for example:
  `Tesla Model Y`, `Model Y`, `特斯拉 Model Y`; `小米 YU7`, `小米YU7`,
  `Xiaomi YU7`.
- Quote exact product names in query text where provider syntax permits it.
- Keep one dimension per query. Prefer a small precise query such as
  `"Tesla Model Y" 中国 2026 车身尺寸 轴距` over a long list of every possible
  specification.
- Do not mechanically pin stale years. Use the current year for current product
  configuration, price, software, service, or market questions. Use historical
  years only when the question explicitly asks for them.

## Follow-Up

- Read the first search's sources and limitations before planning a follow-up.
- Target the concrete missing entity, source type, metric, geography, or date.
- Do not repeat the original query with extra synonyms.
- Prefer official or primary sources for exact specifications and prices, then
  reputable automotive media, regulatory filings, or established databases.
