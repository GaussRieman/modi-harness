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
`get_current_time` call. This is enforced with an opaque, short-lived,
single-use token rather than prompt wording.

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
- issuing search and verification receipts;
- preventing a task with usable sources from bypassing verification;
- rejecting stale verification after a follow-up search.

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
- Tokens expire 60 seconds after issuance.
- A token may authorize exactly one `public_web_research` or
  `public_web_search` invocation.
- A search handler atomically consumes the token before dispatching provider
  requests.
- Missing, unknown, expired, or previously consumed tokens raise `ValueError`
  with a repairable message instructing the Brain to call
  `get_current_time` again.
- Invalid search arguments do not consume a token until basic schema-level
  normalization succeeds. Provider failures do consume it because a real
  search attempt occurred.
- Old tokens are removed opportunistically to bound process memory.

The token is a freshness gate, not a source of truth about publication dates.
Evidence dates still come from source content.

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

## Search and Verification Receipts

Process-global state keyed only by `task_id` is insufficient because task IDs
may repeat across runs. Replace provenance gates with opaque receipts.

### Search receipt

Every successful search handler invocation returns a `search_receipt` bound to:

- the Operation type;
- task ID, when present;
- consumed time token and issued timestamp;
- structured search intent;
- exact usable source URLs returned by that invocation;
- a monotonically increasing per-task search generation.

The receipt is stored in bounded in-process memory and exposed as an opaque
string to the Brain.

### Verification receipt

`verify_claim_evidence` accepts one or more search receipts plus evidence
annotations. It validates every URL against the union of those receipts and
returns a `verification_receipt` bound to:

- task ID;
- claim;
- normalized verified evidence;
- evaluated URLs, including items marked unrelated;
- the latest search generation covered.

The Operation continues returning the normalized evidence array for model
visibility, but the receipt is authoritative.

### Finding gate

`record_research_finding` requires `verification_receipt` for every researched
task except `unverifiable_flag`.

- `sourced`: evidence must exactly match the receipt's normalized supporting or
  contradicting evidence.
- `blocked` with usable sources: a verification receipt must cover the latest
  search generation. The Finding may have no supporting evidence, but its
  limitation must explain why evaluated sources were unrelated, insufficient,
  stale, or failed the selected verification method.
- a follow-up search invalidates an earlier verification receipt for final
  recording because the task generation advances;
- `unverifiable_flag`: blocked without search or receipt remains valid.

This prevents the observed path of collecting usable sources and immediately
recording blocked without verification.

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
- search receipt ID.

For verification, record:

- task ID and claim;
- evaluated/supporting/contradicting/unrelated counts;
- covered search generation;
- verification receipt ID.

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
- Verification receipt stale after follow-up search: reject Finding and require
  re-verification.
- Receipt unknown or expired from bounded memory: repeat the relevant search or
  verification rather than accepting unverifiable provenance.

## Files and Components

Expected implementation scope:

- `agents/research_assistant/skills/query-planning/SKILL.md`: new semantic
  query-planning Skill;
- `agents/research_assistant/skills/web-research/SKILL.md`: protocol sequencing
  and receipt use;
- `agents/research_assistant/tools/research.py`: time tokens, structured search,
  entity ranking, receipts, and Finding gates;
- `agents/research_assistant/tools/__init__.py`: exports;
- `agents/research_assistant/agent.py`: bind the new Operation and load the new
  Skill;
- `agents/research_assistant/workflows/quick_lookup.yaml`: deterministic time
  node;
- `agents/research_assistant/workflows/deep_research.yaml`: time capability and
  protocol wording;
- `src/modi_harness/workflow/session.py` or the nearest generic dispatch/trace
  boundary: optional bounded Operation summary propagation;
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

- search receipt contains only URLs returned by that invocation;
- verification rejects URLs outside supplied search receipts;
- Finding rejects missing verification receipt after a search;
- blocked Finding with usable sources requires current-generation verification;
- follow-up search makes old verification stale;
- sourced Finding evidence must match the verification receipt;
- `unverifiable_flag` remains the only no-search exception.

### Trace and integration

- trace exposes query, time, entity, health, source, and receipt summaries;
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
