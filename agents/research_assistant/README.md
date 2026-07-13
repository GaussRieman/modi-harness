# Research Assistant Agent

Research Assistant is a factory-discovered `ModiAgent` with one autonomous
compound Node. The Brain interprets the request and writes the answer. One
trusted Operation owns bounded public-Web retrieval mechanics.

## Package

```text
research_assistant/
├── agent.toml
├── agent.py
├── validators.py
├── tools/
│   └── research.py
├── workflows/
│   └── research.yaml
└── skills/
    └── web-research/SKILL.md
```

`agent.toml` is only the discovery manifest:

```toml
factory = "agent:build_agent"
```

`agent.py` is the composition root. It returns the actual `ModiAgent` with its
Workflow, one Tool binding, one Skill, permission profile, and completion
validator.

## Workflow

```text
research: autonomous
  -> public_web_research
  -> complete_node
  -> $complete
```

The single Node receives the original request. A clear request normally takes
two Brain Steps:

1. interpret the subject and call `public_web_research`;
2. assess its result and propose the final answer through `complete_node`.

A vague request may first call `request_user_input`. The same Node resumes with
the answer; no secondary Workflow or standalone loop is created.

The Node is intentionally not split into framing, investigation, synthesis,
and verification phases. Those were internal reasoning steps, not stable
business checkpoints. Making each a Node forced eight or more model requests
for a simple lookup.

## Public Web Research Operation

`public_web_research` performs deterministic retrieval work in one dispatch:

- generates at most two query variants;
- searches bounded Bing RSS, Baidu, and DuckDuckGo public endpoints;
- records every provider query and failure independently;
- deduplicates and ranks candidates by subject-name relevance;
- rejects unrelated title/snippet matches;
- fetches at most five candidates and returns at most three usable sources;
- limits each source excerpt to 6,000 characters;
- detects login, captcha, access-control, and empty-page shells.

The Operation never decides whether a company is credible and never writes the
final answer. That judgment remains with the Brain.

The Operation can be selected once per Node input round. `max_steps: 4` leaves
room for one clarification, the Operation, completion, and one repair.

## Completion Contract

The final result contains:

```text
research_question
executive_summary
task_results[]
recommendations[]
source_limitations[]
sources[]
search_records[]
```

Positive task results cite URLs declared in `sources`. Search titles and
snippets are discovery hints, not factual evidence. A negative result keeps
sources and evidence empty, includes records from at least two search
providers, and states the actual search limitation.

The validator rejects absolute claims such as “the company does not exist”
when the only evidence is a bounded public-search miss. The valid conclusion is
that the attempted public search did not establish a reliable match.

## Execution

Interactive input is natural language:

```bash
modi research-assistant
```

Automation input uses the same sole Workflow:

```bash
echo '{"research_question":"杭州拉格朗日具身智能科技的公开背景和技术实力如何？"}' \
  | modi run research-assistant --task - --stream-format jsonl
```

Python callers may pin the Workflow explicitly:

```python
response = session.run_task(
    agent="research-assistant",
    workflow_id="research",
    input={"research_question": "威灿科技的业务和产品是什么？"},
    thread_id="research-001",
)
```

## Trace

A normal clear-input run has this durable shape:

```text
workflow_started
node_started                 research
operation_started            public_web_research
operation_completed          public_web_research
step_completed               operation Step
step_completed               complete_node Step
completion_accepted
node_completed               $complete
workflow_completed
```

Read the Trace through the public API:

```python
for event in session.get_trace("research-001"):
    print(event["event_type"], event["payload"])
```

The same events are appended to:

```text
<workspace_root>/<run_id>/logs/trace.jsonl
```

Trace is execution evidence, not Agent memory.
