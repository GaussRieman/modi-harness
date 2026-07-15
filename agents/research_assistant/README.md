# Research Assistant Agent

Research Assistant is a factory-discovered `ModiAgent` for public-information
research. `agent.toml` only points discovery at `agent:build_agent`; `agent.py`
binds the Workflows, Skill, permissions, and four trusted Operations.

## Routing

When the caller does not specify `workflow_id`, the Agent Router asks the model
to choose exactly one declared Workflow and construct its validated input:

```text
user request
  -> quick_lookup       clear, narrow lookup
  -> deep_research      broad, comparative, evaluative, or vague research
  -> reject_unsupported non-research request
```

The routing descriptions live in `workflows/*.yaml`. A caller may still pin a
Workflow explicitly; checkpoint resume always uses the already selected ID.

## Workflows

`quick_lookup` is the default path for a concrete entity or narrow question:

```text
search: operation(public_web_research)
  -> answer: autonomous
  -> $complete
```

It performs exactly one retrieval dispatch. The answer Node has no tools and
returns only a concise `executive_summary`, citations, and optional limitations.

`deep_research` is reserved for work that justifies additional latency:

```text
confirm_scope: autonomous
  -> investigate: autonomous (search, evaluate, resolve)
  -> $complete
```

The first Node produces a reviewed TaskPlan containing 2–4 concrete research
questions. `completion.review: required` makes the Harness pause and show the
scope in the CLI; the user may start, revise, or cancel before research begins.
The scope Node submits its draft directly to that review and cannot create a
second confirmation prompt. Raw model narration is hidden before the review
panel, so the user sees one canonical scope interaction.
The investigation Node binds one batch of 1–2 complementary queries to each
`task_id`; providers and page fetches run in parallel inside that single Search
Operation. Search only collects evidence. The Agent closes the question with
`record_research_finding` after evidence is sufficient. If the bounded search
remains insufficient, the Agent records a limited finding and immediately
continues. The unresolved question appears in the final limitations; users can
provide additional material in a follow-up research request.

`reject_unsupported` never searches:

```text
reject: operation(reject_research_request)
  -> $complete
```

Weather, translation, coding, reminders, web actions, and general chat belong
on this path.

## Evidence Boundary

`public_web_research` performs strict entity lookup for `quick_lookup`.
`public_web_search` performs question-oriented discovery for `deep_research`
without requiring every candidate to contain one exact entity name. Both own
provider queries, search health, candidate ranking, page fetching, and raw
source records. Autonomous Nodes may summarize usable source content and carry
URL citations forward. They do not copy
`search_records`, provider statuses, or fetch records into `complete_node`.
Those trusted Operation results remain available in runtime state and Trace.

`record_research_finding` binds one resolved question to its direct conclusion,
user implication, confidence, and claim-level evidence. Each evidence item
classifies its source and records a relevant date when available. The final
completion gate checks TaskPlan coverage and requires every key finding to
match that recorded evidence; limited questions remain uncited and appear in
the final limitations.

The model's final completion contains only `direct_answer` and overall
limitations. The Harness constructs `key_findings` and the exact union of cited
source URLs directly from recorded findings. The CLI numbers sources
and places the corresponding number beside each evidence claim, rather than
printing an unrelated URL bucket after one dense paragraph.

There is no custom completion validator. Each autonomous Node uses a minimal
JSON Schema containing only the fields required for the next stable handoff.

## Execution and Trace

```bash
modi research-assistant
```

```python
response = session.run_task(
    agent="research-assistant",
    input={"prompt": "全面分析中控技术的竞争壁垒和风险"},
    thread_id="research-001",
)

for event in session.get_trace("research-001"):
    print(event["event_type"], event["payload"])
```

The CLI keeps deep research deliberately quiet: one scope review, one live
question-progress view, and the final report. Workflow, Node, Operation, repair,
and model narration remain available in Trace but are not printed as
user-facing progress. Trace is execution evidence, not Agent memory.
