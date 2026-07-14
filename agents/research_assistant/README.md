# Research Assistant Agent

Research Assistant is a factory-discovered `ModiAgent` for public-information
research. `agent.toml` only points discovery at `agent:build_agent`; `agent.py`
binds the Workflows, Skill, permissions, and three trusted Operations.

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
  -> investigate: autonomous (public_web_search, one call per question)
  -> $complete
```

The first Node produces a reviewed TaskPlan containing 2–4 concrete research
questions. `completion.review: required` makes the Harness pause and show the
scope in the CLI; the user may start, revise, or cancel before research begins.
The investigation Node binds each discovery query to one `task_id` and produces
the final report itself. A sourced Operation completes that question. A
`no_evidence` or `unavailable` result pauses the Workflow: the user may provide
a better query or URL, type `skip` to continue with an explicit limitation, or
cancel. The CLI removes resolved questions from the pending list.

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

The CLI renders `workflow_selected` before execution, so the simple/deep route
is visible. Deep research additionally emits `task_plan_created`,
`task_started`, and `task_completed` as confirmed questions are resolved. The
Trace records the same Workflow, Node, Operation, interaction, completion, and
terminal evidence; it is not Agent memory.
