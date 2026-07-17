# Evidence-Driven Research Pipeline Design

> **As-built note (post-implementation):** two parts of the original design
> below did not survive contact with the Harness core and were changed during
> implementation. See "Deviations from the original design" at the end for
> what changed and why. The rest of this document (confidence engine,
> `verify_claim_evidence`, `record_research_finding` schema, `finalize_report`)
> matches what was actually built.

## Decision

`deep_research`'s `investigate` Node stops being a Brain-driven step loop
(pick one task, search, evaluate, record, repeat) and gains a structured
sequence of capabilities, plus one new deterministic Node after it:

```text
confirm_scope (task ids + titles only; no per-task metadata survives review)
  -> investigate (autonomous, unchanged Node id)
       per-task judgment of verification_method (Brain-decided when the task starts,
         not persisted from confirm_scope)
       per-task search (public_web_search, up to 2 calls per task)
       two-stage verification (Operation pre-filter + Brain annotation + Operation re-check)
       gap/contradiction-driven re-search (targeted, still per-task)
       re-verification
       record_research_finding (evidence tagged supporting/contradicting; confidence
         computed inside the Operation handler, not supplied by the Brain)
  -> finalize_report (new operation Node: builds the Mermaid evidence graph)
  -> $complete
```

`quick_lookup` and `reject_unsupported` are unaffected.

## Harness-touch budget

This is the binding constraint for the whole design: **`src/modi_harness/`
does not need to change.** Every new behavior is built from extension points
that already exist and are already generic:

| Need | Existing generic mechanism used | New code lives in |
| --- | --- | --- |
| Per-task search allows a follow-up query | `OperationAdapter.max_calls_per_task` (already read by `_operation_budget_error`) | Operation spec, agent package |
| `unverifiable_flag` skips search | existing `task_resolution == "blocked"` handling in `_finish_operation_task` | Skill instruction + Operation handler |
| Verification pre-filter / domain re-check | existing "handler raises `ValueError` -> Operation dispatch fails -> AgentLoop gets repair feedback" pattern (already used by `_normalize_finding_evidence`) | new Operation handler |
| Confidence computed, not model-supplied | the field is just an output key the Operation handler fills in; Runtime already copies whatever `record_research_finding` returns | Operation handler + new pure module |
| Missing-evidence transparency | existing `limitations` passthrough in `_assemble_task_plan_result` | Operation handler appends to `limitations` |
| Evidence graph in final output | existing generic `$ref: "#/nodes/<id>/output"` Node-input wiring, feeding a new deterministic `operation` Node | new Node in `deep_research.yaml` + new Operation handler |

Nothing here requires editing `_validate_task_plan_result`,
`_assemble_task_plan_result`, `_autonomous_step_budget`, or the budget
checker in `runtime.py`. The one thing that stays true and unavoidable: those
functions already contain research-assistant-specific field names
(`conclusion`, `implications`, `confidence`, `evidence`, `source_url`) as
pre-existing coupling. This design keeps that shape exactly as-is —
`evidence` remains a single flat array of `{claim, source_url, source_type,
as_of, ...}` objects, `confidence` remains a single string field — so none of
that existing core code needs to know anything changed.

## Boundary

Brain still decides (unchanged from before):

- per-task verification method, decided when the task starts in `investigate`
  (not at scope-confirmation time — see deviation below);
- query wording;
- whether one piece of evidence supports, contradicts, or is unrelated to a
  claim;
- whether two sources are independent or share an origin;
- whether evidence is direct or requires inference;
- the final narrative (`direct_answer`, `limitations`).

Deterministic Operation code (agent package, not core Harness) now
additionally owns:

- short-circuiting `unverifiable_flag` tasks before any search is issued;
- the independence re-check (domain-based override of the Brain's
  `independent` tag), enforced as handler-side validation;
- computing confidence from six discrete factors inside the
  `record_research_finding` handler — the Brain no longer supplies a
  `confidence` value;
- building the final Mermaid evidence graph from the already-assembled
  `key_findings` — the Brain never authors Mermaid text, for the same reason
  it never re-writes `key_findings`/`citations` today (drift between
  narrative and ledger).

The "Brain judges content, deterministic code enforces structure and
arithmetic" principle is unchanged. What moves is *where* the deterministic
code lives: agent-owned Operations, not core Runtime.

## `confirm_scope`: task ids and titles only

`confirm_scope`'s `task_plan.items[]` entries stay `{id, title}` only — no
extra per-item field. `decision_context` (string) and `constraints` (string
array) are optional top-level scope fields, free text, used only as prompt
context — not structurally validated beyond being strings.

This is a deliberate change from the original design (see deviation below):
`src/modi_harness/types.py`'s `TaskItem` is a fixed `TypedDict` with exactly
`id`/`title`/`status`/`summary`, and the Node-review path
(`runtime.py:_task_plan_from_result` -> `tasks.py:create_task_plan`) rebuilds
each item from only `id`/`title` *before* schema validation runs, for any
Node with `completion.review: required` — which `confirm_scope` is. Any
additional field the model puts in a reviewed TaskPlan item is silently
discarded and never reaches validation, regardless of what the Node's own
YAML schema declares. Workflow YAML schemas are agent-owned, but a Node's
*reviewed* TaskPlan is not — it round-trips through this fixed core shape.

## Per-task search: `public_web_search`

No new batching Operation. `public_web_search` (used by both `quick_lookup`
and `investigate`) keeps its existing one-task-per-call shape:

```json
{"task_id": "t1", "queries": ["q1", "q2"]}
```

- `max_calls_per_task` raised from `1` to `2` on the spec, so the Brain may
  issue one follow-up query per task when the first pass leaves a
  verification gap. `max_calls_per_node` is left unset — the per-task cap is
  the only constraint.
- Cross-task parallel dispatch (a single Operation call fanning out to every
  open task at once) was explored and dropped; see deviation below.

## Two-stage verification: `verify_claim_evidence`

New Operation, handler-side, no `task_id`-scoped budget needed (bounded
naturally by TaskPlan size and the existing per-Node step ceiling):

1. **Handler pre-filter**: drop duplicate URLs, drop `stance: unrelated`
   items.
2. **Brain annotation**: for each surviving evidence item against its claim,
   tag `supporting | contradicting | unrelated`, `independent | same_origin`,
   `direct | indirect`.
3. **Handler re-check**: if two items tagged `independent` share a domain,
   the handler raises `ValueError` with a specific repair message. This uses
   the existing generic path — a failing Operation handler already produces
   an operation-failure StepRecord that feeds back to the AgentLoop for a
   corrected retry, the same way bad `source_type` values are rejected
   today.

The Brain never gets the final say on independence; a plain Python check in
the handler does — no Runtime change needed to make a handler's rejection
authoritative, since Operation failures already are.

## Discrete confidence

Six factors, each discretized to `high | medium | low`:

| Factor | High | Medium | Low |
| --- | --- | --- | --- |
| source_quality | official / primary | reputable_media / industry_report | job_board / secondary |
| source_independence | >=2 independent sources (post re-check) | 1 independent + rest same-origin | all same-origin / single source |
| directness | directly supports claim | requires inference | only tangentially related |
| recency | `as_of` within 90 days | within 365 days | older or missing `as_of` |
| consistency | no contradicting evidence | contradicting exists but outweighed | contradicting >= supporting |
| coverage | verification_method's required evidence shape fully met | partially met | not met |

Combination rule: **overall confidence = the lowest of the six factors**
(ordinal `min`, `low < medium < high`). One bad factor caps the whole claim,
regardless of the other five.

This runs entirely inside the `record_research_finding` Operation handler in
`agents/research_assistant/tools/research.py`, implemented by a pure
function module, `agents/research_assistant/confidence.py`. The handler
receives the tagged evidence (from `verify_claim_evidence` output, re-passed
by the Brain as this tool's input) and the task's `verification_method`
(supplied directly on this same call, decided by the Brain at investigate
time — not threaded through from `confirm_scope`), computes the six factors,
and writes `confidence` into its own return value. The tool's input schema
**removes** the `confidence` field — the Brain cannot supply one.

## `record_research_finding` schema changes

Keep the existing flat `evidence` array shape (Runtime code already expects
this exact shape for citation extraction and signature comparison — do not
split it into separate arrays):

- each evidence item gains `stance` (`supporting | contradicting`),
  `independence` (`independent | same_origin`), and `directness`
  (`direct | indirect`) — all copied from the `verify_claim_evidence` output,
  not freely typed by the Brain at finding time;
- `verification_method` is a required input field on this call itself (the
  Brain states what method it applied to this task, at record time);
- gaps relative to `verification_method` (what was required but never found)
  are appended as plain-text entries to the existing `limitations` array —
  reusing the passthrough `_assemble_task_plan_result` already performs for
  `source.get("limitations")`, instead of inventing a new structured field;
- `confidence` stays a single required output field, computed as above; it
  is **removed from the input schema** so the Brain cannot supply it.

## `finalize_report`: evidence graph output

New deterministic `operation` Node appended after `investigate`:

```yaml
- id: finalize_report
  execution: operation
  operation: build_evidence_graph
  inputs:
    report:
      $ref: "#/nodes/investigate/output"
  transitions:
    completed: $complete
    failed: $fail
```

`investigate`'s own `transitions.completed` changes from `$complete` to
`finalize_report`. `build_evidence_graph` is a new Operation in
`agents/research_assistant/tools/research.py`: a pure function taking the
already-assembled `key_findings`/`citations` (produced by the existing,
untouched `_assemble_task_plan_result`) and returning the same object plus an
`evidence_graph` Mermaid `flowchart` string:

- one node per claim (`task_id` + short title), classed `sourced`/`limited`
  by status;
- one node per distinct evidence source URL, classed `source`;
- solid edge (`-->`) = supporting, dashed edge (`-.->`) = contradicting.

Because `finalize_report` is an `operation` Node, not `autonomous`, it does
not go through the TaskPlan-completion validators at all — those only fire
for the autonomous Node (`investigate`) where `complete_node` is called.
`finalize_report` just runs a deterministic function on already-validated
data and becomes the terminal Node output. Zero Runtime change.

`evidence_graph` never enters the CLI's live Panel rendering — CLI stays
exactly as it is today; it only appears in the final assembled result.

## Success criteria

- `unverifiable_flag` tasks never trigger a search call and are recorded
  `blocked` immediately.
- No Finding in the final result carries a Brain-supplied `confidence`; every
  confidence value traces to the six-factor table computed in the Operation
  handler.
- Independence tags that violate the domain re-check are never accepted
  without a corrected re-annotation.
- `evidence_graph` is present in the final output and every edge corresponds
  to an evidence item present in `key_findings`.
- `quick_lookup` behavior, CLI rendering, and Trace shape for unaffected
  paths are unchanged.
- `git diff src/modi_harness/` is empty for this change.
- Full tests, Ruff, and mypy pass.

## Deviations from the original design

Two parts of the design above (as originally written, before implementation)
turned out to be wrong or infeasible without touching `src/modi_harness/`,
and were changed:

1. **`verification_method` moved from scope-confirmation time to
   investigate-start time, per task.** The original design put a required
   `verification_method` on each `confirm_scope` TaskPlan item, on the
   (incorrect) assumption that "Workflow YAML schemas are already agent-owned
   and already support arbitrary extra properties per item without a core
   change." In practice, `confirm_scope` has `completion.review: required`,
   and the reviewed-TaskPlan round-trip
   (`runtime.py:_task_plan_from_result` -> `tasks.py:create_task_plan`)
   rebuilds every item from a fixed `id`/`title`-only shape *before* the
   Node's own schema validation runs — any extra field is silently dropped,
   causing an unrecoverable validation-repair loop (the model resubmits the
   same field, it gets stripped again, forever). This was caught during test
   implementation, confirmed against the user's standing "stop and confirm
   before an unavoidable Harness change" constraint, and resolved without
   touching core: `confirm_scope`'s schema no longer declares
   `verification_method`; the Brain instead judges it fresh for each task
   when `investigate` starts work on that task, and supplies it directly on
   the `record_research_finding` call (which already needed it as an input).
   Trade-off: the user reviewing the scope draft no longer sees each
   question's intended verification method up front — only task ids and
   titles. Acceptable for v1; revisit if scope-time visibility into
   verification strategy becomes a real requirement.

2. **Task-level parallel search batching (`public_web_search_batch`) was
   dropped.** The original design proposed a new Operation fanning out
   search across every open task in one call. This added meaningful
   complexity (a new task-level concurrency layer, a new Operation spec, new
   response-grouping shape) for a v1 whose explicit goal was "get the whole
   pipeline built first, keep it simple." Implementation kept the existing
   per-task `public_web_search` unchanged and only raised
   `max_calls_per_task` from `1` to `2`, giving each task room for one
   follow-up query without adding a second search primitive. Cross-task
   parallelism can be revisited later as a pure performance optimization if
   `investigate` throughput becomes a problem.
