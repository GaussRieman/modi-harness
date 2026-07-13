# Research Assistant Agent

Research Assistant is a factory-discovered `ModiAgent` with one explicit
Workflow. Its four business phases are autonomous because the solution path in
each phase depends on the question and available sources. Deterministic work
such as fetching a URL, extracting a source card, checkpointing, validation,
and trace recording remains a trusted Operation or Harness responsibility.

## Package layout

```text
research_assistant/
├── agent.toml                 package discovery manifest
├── agent.py                   ModiAgent composition root
├── validators.py              trusted semantic completion predicates
├── tools/
│   ├── __init__.py            exported tool bindings
│   └── research.py            source and briefing Operations
├── workflows/
│   └── research.yaml          Workflow and Node contracts
└── skills/
    ├── source-evaluation/
    └── briefing-structure/
```

`agent.toml` is only the package manifest:

```toml
factory = "agent:build_agent"
```

Discovery sees the manifest, verifies that project factories are trusted,
imports `agent.py`, and calls `build_agent()`. The returned `ModiAgent` is the
actual Agent definition.

## Defining the Agent

`agent.py` owns only composition:

```python
return ModiAgent(
    name="research-assistant",
    description="Source-grounded autonomous research and briefing Agent.",
    instruction=GLOBAL_RESEARCH_RULES,
    workflows=workflows,
    completion_validators=RESEARCH_VALIDATORS,
    tools=tools,
    skills=skills,
    permission_profile=RESEARCH_PERMISSIONS,
)
```

The fields have closed responsibilities:

- `instruction`: global rules that apply in every Node;
- `workflows`: stable business phases and transitions;
- `tools`: executable Operations available to selected Nodes;
- `skills`: source evaluation and briefing methodology injected into Brain
  context;
- `completion_validators`: trusted semantic predicates named by Workflow
  Nodes;
- `permission_profile`: authority boundary for every Operation.

There is no Agent-level output contract or task protocol. Autonomous Nodes own
their completion contracts and use their embedded AgentLoop for local planning.

## Workflow

```text
frame_research (autonomous)
  -> investigate_evidence (autonomous)
  -> synthesize_briefing (autonomous)
  -> verify_briefing (autonomous)
  -> $complete
```

| Node | Purpose | Completion boundary | Tools |
| --- | --- | --- | --- |
| `frame_research` | Frame the question and approach | thin `plan` object | none |
| `investigate_evidence` | Fetch, filter, and cross-check evidence | source-bound evidence bundle | `fetch_url`, `source_extract` |
| `synthesize_briefing` | Turn evidence into a draft | thin digest and draft objects | `generate_research_digest` |
| `verify_briefing` | Check and revise the final answer | final briefing schema and validator | `judge_research_digest` |

Autonomy controls the path inside a Node. The completion schema controls only
the interface between Nodes. Intermediate schemas intentionally leave nested
objects open. Evidence and terminal output receive stronger validation because
source integrity is non-negotiable.

`output_schema.required` means a field must exist and satisfy its JSON Schema;
an empty collection remains valid unless the schema adds `minItems`.
`completion.require` is the stricter assertion that a field must also carry a
meaningful, non-empty value. Semantic validators are reserved for invariants
such as evidence resolving to declared source URLs.

Node inputs use committed outputs from earlier Nodes:

```yaml
inputs:
  research_plan:
    $ref: "#/nodes/frame_research/output"
  evidence_bundle:
    $ref: "#/nodes/investigate_evidence/output"
```

The Agent cannot change the Node goal, capability list, maximum steps,
completion schema, validator, or downstream transition.

## Adding a Node

Add a Workflow Node only when it represents a stable business phase with a
separate goal, capability boundary, checkpoint, or completion proof. Do not add
a Node for an individual URL fetch or model call; those are Operations inside
an autonomous Node.

1. Add the Node to `workflows/research.yaml`.
2. Point the predecessor's `completed` transition to the new Node.
3. Declare only the inputs the new phase consumes.
4. Use a thin output schema for ordinary intermediate data.
5. Add a semantic validator only for an invariant JSON Schema cannot prove.
6. Grant the smallest possible tool set and step limit.
7. Add definition, runtime, and trace tests.

Example:

```yaml
- id: compare_findings
  execution: autonomous
  goal: 比较已核验发现并明确一致点、冲突点和证据缺口
  inputs:
    evidence_bundle:
      $ref: "#/nodes/investigate_evidence/output"
  completion:
    output_schema:
      type: object
      required: [comparison]
      properties:
        comparison:
          type: object
  limits:
    max_steps: 8
  transitions:
    completed: synthesize_briefing
    failed: $fail
```

## Execution

Structured automation input is the most explicit CLI path:

```bash
echo '{
  "research_question": "What changed in the new runtime?",
  "source_urls": ["https://example.test/release"]
}' | uv run modi run research-assistant --task - --stream-format jsonl
```

Because the Agent has one Workflow, it is selected automatically. Python
callers may pin it explicitly:

```python
response = session.run_task(
    agent="research-assistant",
    workflow_id="research",
    input={
        "research_question": "What changed in the new runtime?",
        "source_urls": ["https://example.test/release"],
    },
    thread_id="research-001",
)
```

For live execution:

```python
async for event in session.astream(
    agent="research-assistant",
    workflow_id="research",
    input=research_input,
    thread_id="research-001",
):
    print(event["event_type"], event["payload"])
```

Schema or semantic completion rejection returns feedback to the same Node and
AgentLoop attempt. An external judgment or input checkpoints the exact pending
work and resumes that work rather than asking the model to recreate it.

## Trace

Live stream events and durable Trace use the same execution vocabulary:

```text
workflow_started
node_started
operation_started
operation_completed
step_completed
completion_rejected | completion_accepted
node_completed
interaction_requested | approval_request
interaction_resolved
workflow_completed | workflow_failed | workflow_cancelled
```

Join keys make one run explainable without reading model prose:

- `run_id`, `thread_id`, and `workflow_id` identify the run;
- `node_id` and `node_attempt` identify the business phase attempt;
- `step_id` and `step_index` identify an AgentLoop decision;
- `invocation_id`, `adapter_id`, and `tool_call_id` identify an Operation;
- `event` and `target` identify a committed Workflow transition.

Read checkpointed Trace through the public API:

```python
for event in session.get_trace("research-001"):
    print(event["event_type"], event["payload"])
```

The same events are appended to:

```text
<workspace_root>/<run_id>/logs/trace.jsonl
```

Useful inspection:

```bash
jq -c 'select(.event_type == "completion_rejected")' \
  .modi/workspace/<run_id>/logs/trace.jsonl

jq -c 'select(.payload.node_id == "investigate_evidence")' \
  .modi/workspace/<run_id>/logs/trace.jsonl
```

Trace is append-only execution evidence. It is not Agent memory and is not
automatically fed back into model context.
