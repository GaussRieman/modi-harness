# Time-Aware Query Planning Design

## Decision

Research Assistant will add a dedicated `query-planning` Skill and harden the
tool protocol around it. The Skill owns semantic query planning. Deterministic
Operations own time freshness, entity coverage, candidate ranking, evidence
provenance, and completion gates.

The design addresses four failures observed in the Tesla Model Y versus Xiaomi
YU7 run:

1. free-form queries were overly long and mechanically pinned to stale years;
2. generic tokenization discarded the short `Y` model identifier;
3. results from two entity queries competed in one global candidate pool;
4. tasks with usable sources were closed as blocked without any evidence
   verification.

Every public-Web search invocation must be preceded by a fresh
`get_current_time` call. This is enforced with a run-scoped, short-lived,
single-use token validated against persisted Workflow execution records rather
than prompt wording or process-local memory.

## Scope

This change covers both Research Assistant search paths:

- `quick_lookup` and its `public_web_research` Operation;
- every initial and follow-up `public_web_search` call in `deep_research`.

One `time_token` authorizes one search Operation invocation. A single
`public_web_search` invocation may contain up to two parallel structured search
items; they share the timestamp because they are one search batch. A follow-up
search is a new invocation and therefore requires a new time token.

The change does not add an LLM call inside a search Operation. The existing
Brain uses the new Skill to produce structured search intent. Search execution
remains deterministic and locally testable.

## Responsibility Boundaries

### `query-planning` Skill

The Skill is responsible for:

- extracting exact entities such as `Tesla Model Y` and `小米 YU7`;
- producing aliases such as `Model Y`, `特斯拉 Model Y`, `Xiaomi YU7`, and
  `小米YU7`;
- preserving short model identifiers as part of an entity phrase rather than
  treating `Y` as a standalone keyword;
- producing one search item per entity instead of combining both entities into
  one long query;
- limiting each query to one research dimension;
- using the current time returned by `get_current_time` to select an appropriate
  freshness window;
- quoting exact product names when supported by the provider query syntax;
- choosing a targeted follow-up query from an identified evidence gap rather
  than appending synonyms to the original query.

The Skill is advisory. Tool schemas and handlers enforce the structural and
safety requirements when the Brain fails to follow it.

### Deterministic Operations

Operations are responsible for:

- issuing and consuming time tokens;
- rejecting missing, expired, or reused time tokens;
- validating structured search items;
- ranking candidates independently per search item;
- reserving candidate capacity for each entity;
- recognizing exact normalized entity and alias phrases;
- issuing search and verification IDs in persisted outputs;
- preventing a task with usable sources from bypassing verification;
- rejecting stale verification after a follow-up search.

The Workflow Runtime owns the authoritative run-scoped prerequisite checks. It
does not interpret research content; it only matches opaque IDs and exact URLs
against prior Operation outputs in the same `run_id`.

## Current-Time Protocol

### Operation

Add a trusted L0 Operation:

```text
get_current_time() -> {
  utc_time,
  local_time,
  timezone,
  current_date,
  current_year,
  time_token,
  expires_at
}
```

`local_time` uses `Asia/Shanghai`, matching the active user environment. UTC is
also returned for stable logging and comparison.

### Token rules

- Tokens are generated with a cryptographically random opaque identifier.
- Tokens expire 120 seconds after issuance. This covers normal model latency
  while still requiring a new time read for every search invocation.
- A token may authorize exactly one `public_web_research` or
  `public_web_search` invocation.
- The Runtime validates that the exact token was produced by an earlier
  successful `get_current_time` invocation in the same run, remains within its
  TTL, and has not appeared in any previous search invocation arguments.
- The Runtime marks it consumed by persisting the search InvocationRecord before
  provider dispatch. A retry of that same prepared invocation follows existing
  recovery semantics; a newly planned search must obtain a new token.
- Missing, unknown, expired, or previously consumed tokens raise `ValueError`
  with a repairable message instructing the Brain to call
  `get_current_time` again.
- Invalid search arguments do not consume a token until basic schema-level
  normalization succeeds. Provider failures do consume it because a real
  search attempt occurred.
- Token validation is reconstructable from persisted Workflow state and
  InvocationRecords. A process restart therefore does not lose or revive a
  token.

The token is a freshness gate, not a source of truth about publication dates.
Evidence dates still come from source content.

This is implemented as generic Operation prerequisite metadata rather than a
Research Assistant-specific global dictionary. Search adapters declare the
token argument, issuer Operation, and TTL. The Runtime performs exact matching;
other agents may reuse the mechanism without acquiring research semantics.

## Workflow Changes

### Quick lookup

Keep quick lookup deterministic by adding a time Operation node rather than
turning the path into a free planning loop:

```text
current_time (operation: get_current_time)
  -> search (operation: public_web_research, time_token from current_time)
  -> answer (autonomous, no tools)
```

`quick_lookup.start_node` changes from `search` to `current_time`.

### Deep research

Add `get_current_time` to the `investigate` capability list. The required
per-task sequence becomes:

```text
choose verification method
  -> get_current_time
  -> public_web_search
  -> verify_claim_evidence
  -> optional get_current_time
  -> optional follow-up public_web_search
  -> verify_claim_evidence again
  -> record_research_finding
```

`unverifiable_flag` remains the only path that may record a blocked Finding
without a time call or search.

The existing 40-step Node ceiling remains unchanged for this change.

## Structured Query Contract

Replace the free-form `queries: string[]` input of `public_web_search` with:

```json
{
  "task_id": "design_dimensions",
  "time_token": "opaque-token",
  "searches": [
    {
      "query": "\"Tesla Model Y\" 中国 车型参数 车身尺寸 轴距",
      "entity": "Tesla Model Y",
      "aliases": ["Model Y", "特斯拉 Model Y"],
      "dimension": "车身尺寸与轴距"
    },
    {
      "query": "\"小米 YU7\" 官方 车型参数 车身尺寸 轴距",
      "entity": "小米 YU7",
      "aliases": ["小米YU7", "Xiaomi YU7"],
      "dimension": "车身尺寸与轴距"
    }
  ]
}
```

Rules:

- one or two search items per invocation;
- every item requires non-empty `query`, `entity`, and `dimension`;
- aliases are optional, deduplicated, and bounded;
- at most six aliases are accepted, each at most 120 characters;
- `query` and `entity` are at most 240 characters; `dimension` is at most 120;
- two items may not declare the same normalized entity;
- a query should describe only its own entity;
- the handler preserves the exact structured intent in its output.

The old free-form input is removed from the tool schema so incorrect callers
fail visibly instead of silently using the old ranking path.

`public_web_research` keeps its subject/question contract and adds required
`time_token` because its entity is already explicit in `subject`.

## Entity-Aware Ranking

### Phrase normalization

Entity and alias matching uses a compact normalized form:

```text
Tesla Model Y -> teslamodely
Tesla Model-Y -> teslamodely
Tesla ModelY  -> teslamodely
Model Y       -> modely
小米 YU7      -> 小米yu7
小米YU7       -> 小米yu7
```

Normalization lowercases Latin text and removes whitespace and punctuation
between letters, digits, and CJK characters. It does not add standalone
single-character tokens.

This is the key `Model Y` fix: `Y` is never scored alone. The protected phrase
`modely` is scored as a unit, so a Model 3 or generic Tesla page cannot satisfy
it.

### Scoring

For each structured search item, candidate scoring is independent:

- exact normalized entity match: strong weight;
- exact normalized alias match: strong weight below the full entity;
- ordinary dimension-token overlap: secondary weight;
- provider result position: tie-break weight;
- agreement from multiple healthy providers: bonus.

Generic tokenization remains a fallback for dimension words. It no longer owns
product identity.

### Candidate allocation

Do not concatenate all query tokens into one global ranking set.

1. Run providers for each structured search item.
2. Rank each item's results against that item's entity, aliases, and dimension.
3. Select the top candidate from every non-empty item pool.
4. Fill remaining fetch slots round-robin from the per-item pools.
5. Deduplicate canonical URLs without removing an item's only candidate unless
   the same URL already represents that entity.

With two available pools, each entity therefore receives at least one fetch
attempt before either entity receives a second attempt.

## Search and Verification IDs

Process-global state keyed only by `task_id` is insufficient because task IDs
may repeat across runs. Replace provenance gates with run-scoped IDs validated
against persisted Operation outputs.

### Search output identity

Every successful search handler invocation returns a random `search_id` in its
ordinary output. The authoritative record is the persisted Operation output,
bound by the Runtime to:

- the Operation type;
- task ID, when present;
- consumed time token and issued timestamp;
- structured search intent;
- exact usable source URLs returned by that invocation;
- its ordering among search invocations for that task within the same run.

There is no process-global search-generation counter. The current generation is
derived from persisted StepRecords in one `run_id`, so concurrent or sequential
runs with the same `task_id` cannot affect each other.

### Verification output identity

`verify_claim_evidence` accepts one or more `search_ids` plus evidence
annotations. Before dispatch, the Runtime resolves those IDs to successful
search outputs in the same run and task. It requires the supplied annotation
items to cover every distinct usable URL returned by the selected searches,
including sources the Brain marks `unrelated`.

The Operation returns a random `verification_id` in its ordinary output. The
persisted verification output is bound to:

- task ID;
- claim;
- normalized verified evidence;
- evaluated URLs, including items marked unrelated;
- the latest search generation covered.

The Operation continues returning the normalized supporting/contradicting
evidence array for model visibility, plus `evaluated_urls` containing every URL
including unrelated items. The persisted output is authoritative.

If the selected search outputs contain zero usable URLs, `items: []` is valid.
The Runtime only permits this empty-verification path when the union of usable
URLs is actually empty. The returned verification output still records the
covered search IDs and generation, allowing a truthful blocked Finding without
invented annotations.

### Finding gate

`record_research_finding` requires `verification_id` for every researched
task except `unverifiable_flag`.

- `sourced`: evidence must exactly match the verification output's normalized
  supporting or
  contradicting evidence.
- `blocked` with usable sources: the verification output must cover every usable
  URL accumulated by the task through the latest search generation. The Finding
  may have no supporting evidence, but its limitation must explain why the
  fully evaluated source set was unrelated, insufficient, stale, or failed the
  selected verification method.
- a follow-up search invalidates an earlier verification output for final
  recording because the task generation advances;
- `unverifiable_flag`: blocked without search or verification ID remains valid.

Runtime validation resolves `verification_id` against the same run's persisted
Operation outputs, checks the task ID, covered search IDs, evaluated URL set,
and evidence equality, then permits dispatch. No ID lookup depends on process
memory, so resume after a restart does not consume an additional search
budget merely to reconstruct provenance.

This prevents both the observed path of collecting usable sources and
immediately recording blocked without verification and the weaker bypass of
annotating one irrelevant URL while ignoring other usable sources.

## Trace Visibility

Operation trace events should include bounded, non-sensitive summaries rather
than complete fetched page content.

For search Operations, record:

- current time and timezone used;
- task ID;
- structured query strings and entities;
- provider health counts;
- candidate count per search item;
- usable source URLs and titles;
- search ID.

For verification, record:

- task ID and claim;
- evaluated/supporting/contradicting/unrelated counts;
- covered search generation;
- verification ID.

The summary must exclude full excerpts and raw page bodies. If the existing
generic trace event cannot carry this summary, add a generic optional
`operation_summary` payload sourced from the dispatch result; do not add
Research Assistant-specific fields to the core trace schema.

## Failure Handling

- Time token failure: repair by calling `get_current_time` again.
- One provider blocked: continue with healthy providers and report health
  accurately.
- One entity pool empty: keep the other pool's sources, report which entity
  lacks candidates, and use the optional follow-up search for that entity.
- Candidate page blocked: continue through the round-robin fetch list.
- All usable sources unrelated: verification records the evaluated URLs;
  blocked Finding is allowed with a concrete limitation.
- Verification output stale after follow-up search: reject Finding and require
  re-verification.
- Unknown ID: reject it as not belonging to the run. Persisted IDs do not expire
  during a run and need no recovery search after process restart.

## Files and Components

Expected implementation scope:

- `agents/research_assistant/skills/query-planning/SKILL.md`: new semantic
  query-planning Skill;
- `agents/research_assistant/skills/web-research/SKILL.md`: protocol sequencing
  and persisted ID use;
- `agents/research_assistant/tools/research.py`: time tokens, structured search,
  entity ranking, output IDs, and Finding gates;
- `agents/research_assistant/tools/__init__.py`: exports;
- `agents/research_assistant/agent.py`: bind the new Operation and load the new
  Skill;
- `agents/research_assistant/workflows/quick_lookup.yaml`: deterministic time
  node;
- `agents/research_assistant/workflows/deep_research.yaml`: time capability and
  protocol wording;
- `src/modi_harness/workflow/session.py` or the nearest generic dispatch/trace
  boundary: generic prerequisite validation and optional bounded Operation
  summary propagation;
- research tool, workflow, trace, and integration tests;
- architecture documentation.

No provider, browser automation, or paid search API is added.

## Testing

### Time protocol

- current-time output contains UTC and Asia/Shanghai fields;
- missing token is rejected;
- expired token is rejected;
- reused token is rejected;
- one token authorizes one two-item search batch;
- follow-up search requires a new token;
- quick lookup executes time before search.
- a token from another run is rejected;
- a persisted unconsumed token remains usable after Runtime reconstruction;

### Query planning and ranking

- Agent loads `query-planning` and `web-research` Skills;
- tool schema requires structured search items;
- `Tesla Model Y`, `Tesla ModelY`, and `Tesla Model-Y` normalize equivalently;
- Model Y ranks above Model 3 and generic Tesla pages;
- standalone `Y` does not produce a match bonus;
- Xiaomi YU7 aliases normalize correctly;
- two entity pools each receive a fetch attempt before either receives a
  second;
- a blocked Tesla page does not prevent fetching a lower-ranked Tesla candidate
  and a Xiaomi candidate.

### Evidence gates

- search ID resolves only to URLs returned by that same-run invocation;
- verification rejects URLs outside supplied search IDs;
- verification rejects partial annotation when any usable URL is omitted;
- empty verification is accepted only when selected searches returned zero
  usable URLs;
- Finding rejects missing verification ID after a search;
- blocked Finding with usable sources requires current-generation verification;
- follow-up search makes old verification stale;
- verification after a follow-up search must cover usable URLs from both the
  initial and follow-up search outputs;
- sourced Finding evidence must match the verification output;
- `unverifiable_flag` remains the only no-search exception.

### Trace and integration

- trace exposes query, time, entity, health, source, and persisted ID summaries;
- trace excludes source excerpts;
- a Tesla Model Y versus Xiaomi YU7 scripted run performs
  time -> search -> verify -> Finding for every researched item;
- the run cannot produce “no usable sources” when its search summary reports
  usable sources without recording why verification rejected them;
- existing rejection and CLI progress behavior remains stable.

## Success Criteria

- Every public-Web search invocation demonstrably consumes a fresh time token.
- Query planning is model-semantic through a dedicated Skill, not an LLM nested
  inside deterministic search code.
- `Model Y` identity survives query planning and candidate ranking across common
  spacing and punctuation variants.
- Comparative searches preserve candidate coverage for every entity.
- A task cannot close as blocked after returning usable sources unless current
  evidence verification explains the gap.
- A future trace contains enough bounded data to reproduce why a query returned
  or rejected its candidates.
- Focused tests, the full suite, Ruff, and mypy pass.

## Operation Metadata

`get_current_time` and the two search Operations are explicitly
`idempotent: false`. `get_current_time` returns a new token on every invocation;
search consumes a single-use prerequisite token, so a newly planned identical
invocation is observably different. Existing dispatcher retry of one prepared
invocation remains governed by its InvocationRecord and does not authorize
token reuse by a second plan.
