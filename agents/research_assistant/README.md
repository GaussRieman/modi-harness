# Research Assistant Agent

完整的架构、执行协议、证据边界和扩展方式见
[`docs/architecture/research-assistant.md`](../../docs/architecture/research-assistant.md)。

Research Assistant is a factory-discovered `ModiAgent` for public-information
research. `agent.toml` only points discovery at `agent:build_agent`; `agent.py`
binds the public Workflows, the internal child Workflow, Skills, permissions,
Task Graph components, and seven trusted Operations.

## Routing

When the caller does not specify `workflow_id`, the Agent Router asks the model
to choose exactly one declared Workflow and construct its validated input:

```text
user request
  -> quick_lookup       clear, narrow lookup
  -> deep_research      broad, comparative, evaluative, or vague research
  -> reject_unsupported non-research request

deep_research
  -> research_dimension  internal child Workflow, selected only by the Task Graph
```

The routing descriptions live in `workflows/*.yaml`. A caller may still pin a
Workflow explicitly; checkpoint resume always uses the already selected ID.

## Workflows

`quick_lookup` is the default path for a concrete entity or narrow question:

```text
current_time: operation(get_current_time)
  -> search: operation(public_web_research)
  -> answer: autonomous
  -> $complete
```

It reads the current time and then performs exactly one retrieval dispatch. The
search consumes a short-lived, single-use `time_token`. The answer Node has no
tools and returns only a concise `executive_summary`, citations, and optional
limitations.

`deep_research` is reserved for work that justifies additional latency:

```text
confirm_scope: autonomous + required review
  -> investigate: task_graph
      -> independent dimension Tasks in isolated research_dimension children
  -> finalize_report: operation(build_evidence_graph)
  -> $complete
```

The first Node produces a confirmed research Intent with a goal, desired
outcome, success criteria, constraints, assumptions, and candidate dimensions.
`completion.review: required` makes the Harness pause and show the Intent in
the CLI; the user may start, revise, or cancel before any child is launched.
The scope Node submits its draft directly to that review and cannot create a
second confirmation prompt. Raw model narration is hidden before the review
panel, so the user sees one canonical scope interaction.

The Task Graph Planner creates one immutable Task per dimension. Independent
Tasks are scheduled concurrently; declared dependencies remain serial. Each
Task receives a pinned `research-dimension` template and runs in an isolated
child checkpoint and workspace. A child executes the bounded search protocol,
then returns one candidate Finding. The parent Task Verifier accepts that
Finding only when its evidence, provenance, and exact Task identity are valid.
Completed sibling Tasks remain committed if another dimension becomes limited
or fails.

Every child search calls `get_current_time` immediately before the search and
passes the fresh single-use token. Search only collects evidence. The child
verifies every usable URL from every current `search_id`, and records the
Finding with the latest `verification_id`. Provenance retains a normalized
evaluation entry for every usable URL, including those classified unrelated,
so a candidate cannot silently omit contradicting evidence. The parent binds
that manifest to the exact verification output persisted in the child
checkpoint, rather than trusting a candidate-supplied digest. A bounded
evidence gap becomes a `blocked`/limited Finding and does not erase already
accepted sibling results.

After the parent Goal Verifier passes, `build_evidence_graph` receives only the
parent's accepted `committed_results`. It deterministically assembles the direct
answer, limitations, Findings, citations, provenance, and evidence graph. A
blocked Finding contributes only an explicit unverified placeholder to the
direct answer, never its draft conclusion; no model-authored synthesis survives
this boundary.

After a time read, the planner surfaces the fresh token as an explicit next-step
prerequisite and temporarily hides the clock tool, preventing repeated time
calls. When a Finding is recorded, the Runtime resolves `verification_id` and
injects the normalized evidence and provenance itself; the model never needs to
copy that JSON. Confidence is scored against the persisted search date, so a
checkpoint resumed across midnight cannot change an already computed rating.

`reject_unsupported` never searches:

```text
reject: operation(reject_research_request)
  -> $complete
```

Weather, translation, coding, reminders, web actions, and general chat belong
on this path.

## Evidence Boundary

`public_web_research` performs strict entity lookup for `quick_lookup`.
`public_web_search` performs entity-aware discovery for each deep-research
dimension. Each search item declares an exact entity, aliases, one dimension,
and its query. Candidates are ranked per entity and fetch slots are allocated
round-robin, so `Tesla Model Y` does not lose its short model identity and
comparison entities do not crowd each other out. Both search Operations own
provider queries, search health, page fetching, and raw source records.
Children never receive the parent transcript or unrelated Task histories.

`record_research_finding` binds one resolved dimension to its direct conclusion,
user implication, confidence, claim-level evidence, and search provenance. Each
evidence item classifies its source and records a relevant date when available.
The parent Task Verifier checks the canonical Finding before the Task can
complete. Limited dimensions remain visibly limited; any verified partial
evidence and citations stay attached, along with the exact coverage gap.

Finalization has no synthesis-model completion. The Harness constructs
`direct_answer`, `limitations`, `key_findings`, provenance, and the exact union
of cited source URLs from accepted `committed_results`; internal `implications`
are not published. The CLI numbers sources and places the corresponding number
beside each evidence claim, rather than printing an unrelated URL bucket after
one dense paragraph.

The Task Graph Node uses the registered `research-task-graph-result` completion
validator. Each autonomous Node still uses a minimal JSON Schema containing
only the fields required for the next stable handoff.

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
Task Graph progress view with nested child status, and the final report.
Workflow, Node, Operation, repair, child checkpoint, and model narration remain
available in Trace but are not printed as user-facing progress. Trace is
execution evidence, not Agent memory.
