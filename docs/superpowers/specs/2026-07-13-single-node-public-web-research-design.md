# Single-Node Public Web Research Design

## Decision

Research Assistant becomes one autonomous compound Node:

```text
research: autonomous
  -> public_web_research
  -> complete_node
  -> $complete
```

The four-Node research Workflow and its digest/judge Operations are deleted.
They forced the model to re-plan and complete each internal phase, producing
eight or more model requests for a simple company lookup. Recent traces show
that model latency, not network execution, consumed almost all of the 75–102
second runtime.

## Boundary

The uncertain work stays with the Brain:

- interpret the user's research intent;
- decide the subject and question passed to public research;
- assess conflicts, ambiguity, and evidence strength;
- write the final concise answer;
- ask one question only when the subject itself cannot be identified.

The repeatable mechanics become one trusted Operation:

- generate bounded query variants;
- search multiple public providers;
- normalize and deduplicate candidates;
- score subject relevance;
- fetch a small number of strong candidates;
- return compact source and search records.

The Operation does not make the research judgment and does not write the final
answer.

## Public Research Operation

`public_web_research` accepts:

```json
{
  "subject": "杭州拉格朗日具身智能科技",
  "question": "该公司的公开背景和技术实力如何"
}
```

It performs at most two query variants across bounded public search providers,
deduplicates results, rejects candidates with no subject-name overlap, and
fetches at most three relevant pages. Provider failures remain explicit search
records and do not erase successful providers.

The result contains:

```text
subject
question
queries
search_records[]
candidates[]
sources[]
limitations[]
```

Each search record identifies its provider, query, search URL, results, and
error. Each source record contains its final URL, title, bounded content
excerpt, and fetch status. Raw pages and login shells are not promoted to
evidence.

## Workflow Contract

The sole `research` Node receives the original Workflow input and can select
only `public_web_research`. The Operation may be called once per input round.
The Node has four Steps so a vague input can follow:

```text
request_user_input -> public_web_research -> complete_node -> one repair
```

A clear input normally takes two model requests: one Operation proposal and one
completion proposal.

The final result keeps the existing briefing fields and adds evidence lineage:

```text
research_question
executive_summary
task_results[]
recommendations[]
source_limitations[]
sources[]
search_records[]
```

Positive claims must cite URLs declared in `sources`. A negative result must
include real search records and explicit limitations. Search failure means
"this bounded public search did not establish a reliable match"; it never
proves that a company does not exist.

## Deletions

Delete without compatibility paths:

- `frame_research`, `investigate_evidence`, `synthesize_briefing`, and
  `verify_briefing`;
- `web_search`, `fetch_url`, `generate_research_digest`, and
  `judge_research_digest` as Agent-selectable Operations;
- the old source-evaluation and briefing-structure Skills;
- digest-specific validators, documentation, and tests.

## Success Criteria

- A clear company name produces at most two model requests in the normal path.
- Search uses multiple provider records rather than treating Bing RSS as
  authoritative.
- Irrelevant search results cannot become positive evidence.
- A negative answer is scoped to the actual queries and providers attempted.
- CLI Trace shows one Node, one public research Operation, and completion.
- Full tests, Ruff, and mypy pass.
