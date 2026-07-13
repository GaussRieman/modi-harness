# Research Assistant Agent

Research Assistant is a factory-discovered `ModiAgent` for public-information
research. `agent.toml` only points discovery at `agent:build_agent`; `agent.py`
binds the Workflows, Skill, permissions, and two trusted Operations.

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
  -> investigate: autonomous (public_web_research, up to 6 calls)
  -> synthesize: autonomous
  -> $complete
```

The first Node asks one necessary question only when scope is genuinely
missing. The investigation Node may search distinct dimensions. The final Node
has no tools and synthesizes the evidence already collected.

`reject_unsupported` never searches:

```text
reject: operation(reject_research_request)
  -> $complete
```

Weather, translation, coding, reminders, web actions, and general chat belong
on this path.

## Evidence Boundary

`public_web_research` owns provider queries, search health, candidate ranking,
page fetching, and raw source records. Autonomous Nodes may summarize usable
source content and carry URL citations forward. They do not copy
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

The Trace starts with `workflow_selected`, whose payload records the chosen
Workflow and strategy (`model`, `explicit`, or `sole`). It then records Node,
Operation, completion, and terminal events. Trace is execution evidence, not
Agent memory.
