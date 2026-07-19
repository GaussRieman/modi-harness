# Research Trust Boundary Hardening Design

## Decision

Harden the Research Assistant at five existing boundaries without adding a new
generic Task abstraction:

1. a Task's durable `goal` remains human-readable;
2. the Research Context Builder reconstructs structured child context from the
   confirmed Intent and exact `task_id`;
3. verification methods become hard completion requirements;
4. a recorded conclusion must be the exact claim that was verified;
5. the final narrative is assembled deterministically from accepted Findings.

The CLI must also render frozen tuple-backed output and show confirmed research
constraints. These changes address the production run
`01KXVVXSJ3GM7FXZZ583HXE5QP`, where internal JSON was displayed as Task titles,
all four Findings had low confidence and explicit evidence gaps, and the CLI
hid those gaps while printing a synthesis that added unsupported claims.

## Observed Failure

The Kant/Hegel run completed successfully at the protocol level, but completion
did not mean the user saw a trustworthy result:

- Research Planner serialized `research-task-goal-v1` into `TaskRun.goal`.
  Generic Task Graph projection correctly treated `goal` as display text, so
  the CLI rendered the entire JSON object.
- Every committed Finding had `confidence=low`. Several limitations explicitly
  said that primary texts or direct Hegel sources were unavailable.
- `record_research_finding` appended a verification-method coverage warning but
  retained `status=sourced`.
- The model labelled Wikipedia as `reputable_media` and SEP as
  `official`/`primary`. Those labels were accepted as authoritative.
- `_format_terminal_output` accepted only `list`, while Workflow output is
  commonly frozen as `tuple`. Key Findings, confidence, and limitations were
  therefore omitted from the terminal.
- `synthesize_report` could write facts not present in a committed conclusion,
  such as downstream intellectual influences. Finalization copied that prose
  without an evidence-bound check.

The fix must make an unmet research contract visible and non-sourced. It must
not solve this by adding more prompt text alone.

## Scope

### Included

- Research Planner and Context Builder Task representation.
- Scope and terminal CLI rendering.
- Trusted source-type demotion for known reference and aggregation domains.
- Hard enforcement of `verification_method` coverage.
- Exact binding between verified claim and recorded conclusion.
- Deterministic final narrative from committed Findings.
- Regression coverage based on the Kant/Hegel failure shape.

### Excluded

- A universal web-source authority classifier.
- Semantic interpretation of arbitrary free-text Intent constraints.
- New generic `TaskRun.title`, metadata, or payload fields.
- A second LLM verifier that claims to prove semantic truth.
- Search-provider changes or broader retrieval infrastructure.

## Task Display And Child Context

Research Planner currently overloads `TaskRun.goal` with transport data. Stop
doing that:

```text
TaskRun.goal = candidate dimension title or question
TaskRun.task_id = candidate dimension id
```

The Research Context Builder already receives the confirmed Intent and the
persisted Task. It locates exactly one
`planning_context.candidate_dimensions[]` entry whose `id` equals
`task.task_id`, then constructs `research-task-goal-v1` inside
`ContextManifest.extensions`.

The builder rejects missing or duplicate dimension IDs. It does not fall back
to parsing JSON from `TaskRun.goal` for newly planned Tasks. A narrow legacy
fallback may remain only if existing checkpoint recovery requires it; new tests
must prove the normal path uses the confirmed Intent.

This keeps the generic Task Graph readable while preserving child context
isolation and exact task identity.

## Scope Visibility

The scope review must display:

- subject;
- goal/research question;
- candidate dimension titles;
- each dimension's verification method and authoritative-source bindings;
- non-empty constraints.

Free-text constraints remain advisory unless represented by a structured
verification method. The UI must not hide them, because a human cannot confirm
an Intent contract they cannot see. The scope prompt must instruct the model to
reflect evidence-quality requirements in each dimension's
`verification_method` and, when `official_primary_required` is selected, in an
explicit `authority_bindings` array:

```json
{
  "host": "example.gov",
  "source_type": "official"
}
```

Bindings are part of the reviewed Intent. Hosts are normalized lowercase
hostnames without paths, credentials, ports, or wildcards. The scope schema
caps each dimension at eight bindings and permits only `official` or `primary`.
The Research Planner rejects `official_primary_required` dimensions that have
neither an explicit binding nor a matching built-in trusted authority rule.

The Runtime will not attempt to parse free-text constraints into policy.

## Trusted Source-Type Demotion

`verify_claim_evidence` continues to accept a model-proposed `source_type`, but
the Operation canonicalizes it before returning trusted evidence.

V1 uses two conservative tables:

1. exact reviewed authority bindings and a small built-in trusted-authority
   registry are the only paths by which `official` or `primary` can survive;
2. a domain-cap table forces known reference and aggregation sources down to
   `secondary`.

| Domain class | Canonical maximum |
| --- | --- |
| Wikipedia and similar open encyclopedias | `secondary` |
| SEP, IEP, Britannica and comparable reference works | `secondary` |
| Generic blogs and teaching-summary sites | `secondary` |

The tables only demote; they never promote a model label. An unknown domain
claimed as `official` or `primary` is downgraded to `secondary`. A reviewed
binding is exact-host by default and may cover subdomains only through an
explicit `include_subdomains: true` field. Built-in suffix rules are limited to
unambiguous public authorities such as government TLDs; they must be enumerated
in code and tests. Registrable-domain comparison uses normalized IDNA
hostnames, not substring matching on full URLs.

This is a fail-closed authority classifier, not a universal quality ranking.
Unknown sources may still retain non-authoritative supported types such as
`reputable_media` or `industry_report`, but they can never satisfy
`official_primary_required`.

The Kant/Hegel regression must prove that Wikipedia cannot become
`reputable_media`, SEP cannot become `official` or `primary`, and an unlisted
blog cannot satisfy `official_primary_required` merely because the model says
so.

## Hard Verification Methods

After canonical source types and independence checks, coverage is evaluated
deterministically:

- `single_source_sufficient`: at least one supporting source;
- `dual_independent_required`: at least two supporting sources from distinct
  domains marked independent after the existing domain check;
- `official_primary_required`: at least one supporting source whose canonical
  type remains `official` or `primary`;
- `contradiction_sensitive`: the dual-independent requirement plus explicit
  evaluation of every usable source, which the Runtime already enforces;
- `unverifiable_flag`: no search, no evidence, and `blocked` status.

If a model requests `status=sourced` while coverage is not fully satisfied,
`record_research_finding` deterministically changes the canonical result to:

```text
status = blocked
task_resolution = blocked
confidence = low
limitations += exact coverage gap
```

Partial verified evidence and its citations remain attached so the user can see
what was found. Parent Task verification can accept this canonical limited
Finding, allowing successful siblings to remain committed.

This is a downgrade, not an Operation error: exhausting a bounded search with
partial evidence is a legitimate limited result, not an autonomous repair loop.

## Claim Binding

### Immutable task contract

The child model does not author `task_id`, `question`, or
`verification_method` at commit time. `research_dimension.yaml` supplies those
Operation arguments directly from the immutable
`ContextManifest.extensions.research_task` built from the confirmed Intent.
Only `conclusion`, `implications`, `status`, `verification_id`, and limitations
come from the child draft.

The parent Task Verifier independently resolves the exact confirmed dimension
by `task_id` and requires the canonical Finding's `task_id`, normalized
`question`, and `verification_method` to equal that dimension. It also requires
`unverifiable_flag` to match the confirmed method; a child cannot weaken any
method or switch to the no-search path.

Adversarial tests must substitute every weaker verification method and a
different question at both the child Operation boundary and parent candidate
boundary.

### Verified claim

For every researched method except `unverifiable_flag`, the
`record_research_finding` protocol requires normalized `conclusion` to equal
the normalized `claim` stored in the referenced `verify_claim_evidence`
output.

If they differ, the Operation is rejected with repair feedback. The child may:

- record the exact verified conclusion;
- run a new verification for a revised conclusion; or
- submit a limited Finding when the intended conclusion cannot be supported.

This prevents the epistemology failure where the verified claim and recorded
conclusion described materially different levels of certainty.

`implications` remains internal Finding context but is not used to construct
the final evidence-bearing answer. V1 does not pretend to semantically verify
arbitrary implication prose.

## Deterministic Finalization

Remove the free-form `synthesize_report` Node from the deep-research terminal
path:

```text
investigate(task_graph)
  -> finalize_report(build_evidence_graph)
  -> complete
```

`build_evidence_graph` receives only `committed_results` and constructs:

- `direct_answer`: ordered paragraphs from canonical Findings. A sourced
  Finding is rendered as `question: conclusion`. A blocked Finding is rendered
  only as `question: 未达到验证要求，详见限制`; its unverified conclusion is
  never asserted in the direct answer;
- `key_findings`: task ID, question, conclusion, confidence, verification
  method, status, evidence, and provenance;
- `citations`: exact ordered union of evidence URLs;
- `limitations`: exact ordered union of Finding limitations;
- `evidence_graph`: the existing deterministic graph.

It omits `implications` from the published Finding shape. No model-authored
free-form sentence survives into the final report, and a limited Finding stays
visibly limited.

The Operation may keep its legacy `report` argument as an optional compatibility
input for non-committed callers, but the deep-research Workflow must not supply
model-authored report prose.

## CLI Rendering

`_format_terminal_output` accepts `Mapping`, including `MappingProxyType`, for
the output object and every nested Finding, evidence item, and task result. It
treats both `list` and `tuple` as JSON array values for:

- key Findings;
- evidence;
- limitations;
- recommendations;
- task results;
- citations.

The rendered final output must show low confidence and limitations before the
source list. Task Graph progress must show only readable Task titles and child
status, never serialized ContextManifest or research-task JSON.

## Recovery And Compatibility

- Existing persisted Task Graph checkpoints may still contain JSON in
  `TaskRun.goal`; task-plan projection should present a safely decoded `title`
  when possible so resumed old runs remain readable.
- New Research Tasks use readable goals and Intent-based Context Builder input.
- Candidate submission, receipt, committed artifact, lease, and fencing
  semantics are unchanged.
- Quick lookup and non-research rejection paths are unchanged.

## Tests

Add focused tests for:

1. Research Planner emits readable goals while Context Builder reconstructs the
   complete exact research Task from confirmed Intent.
2. Missing or duplicate candidate dimension IDs fail closed.
3. Task Graph projection decodes a legacy structured research goal for display
   without changing persisted state.
4. Scope review prints constraints once.
5. Terminal output renders the actual runtime-frozen shape: tuple-backed arrays
   containing `MappingProxyType` Findings and evidence, including confidence
   and limitations.
6. Source canonicalization demotes Wikipedia, SEP/IEP, Britannica, and generic
   teaching summaries.
7. Every verification method's satisfied and unsatisfied cases, plus attempts
   to replace the confirmed method with every weaker method and
   `unverifiable_flag`.
8. An unmet method becomes a committed limited Finding without losing partial
   evidence.
9. A conclusion that differs from the verified claim is rejected.
10. Finalization ignores supplied free-form prose, publishes only committed
    sourced conclusions, and never asserts a blocked conclusion in
    `direct_answer`.
11. The Kant/Hegel fixture cannot finish as four clean sourced Findings when
    its evidence has the source mix observed in the production trace.
12. An unknown blog cannot satisfy an authoritative method through a claimed
    `official` or `primary` type.
13. One full integration test runs confirmed Intent through child recording,
    parent verification and commit, deterministic finalization, session
    response, and CLI rendering using runtime-frozen values. It attempts method
    and question substitution, authority elevation, partial evidence, and
    supplied report prose, then asserts exact canonical types, qualified
    limited output, exact limitations, and absence of implications or supplied
    prose.
14. Full Workflow, Task Graph recovery, Research Assistant, CLI, Ruff, and mypy
    gates remain green.

## Success Criteria

- No new Research Task renders structured JSON as its title.
- A user sees all confirmed constraints before approving scope.
- Frozen output cannot hide confidence or limitations.
- A known secondary/reference domain cannot satisfy an official/primary
  requirement through model labelling.
- An unknown domain cannot satisfy an official/primary requirement without a
  confirmed authority binding or built-in trusted rule.
- A child cannot weaken or replace the confirmed Task verification method,
  question, or ID.
- An unmet verification method is always published as `limited`, never
  `sourced`.
- A published conclusion exactly matches the verified claim that produced its
  evidence.
- A limited conclusion is never asserted as a fact in `direct_answer`.
- The final answer contains no prose outside committed canonical conclusions.
- Existing parent/child checkpoint recovery and sibling-commit behavior remain
  unchanged.
