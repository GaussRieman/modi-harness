# Task Graph Projection And Brain Context Recovery Design

## Decision

Apply four bounded corrections to the existing long-task runtime:

1. Task Graph display preserves the order of active Tasks in the persisted
   graph. Scheduling continues to use priority, dependencies, required status,
   and stable IDs independently.
2. Workflow checkpoints retain complete Operation results, while the Brain
   receives a bounded projection of prior Steps. The projection preserves the
   latest actionable evidence but summarizes older or oversized outputs.
3. A model response with neither visible content nor tool calls receives one
   structured repair invocation. A second empty response remains a planning
   failure.
4. When a child reaches a terminal state, the parent persists that terminal
   observation before projecting the failed Task Graph state.

This is a recovery hardening change, not a new Task, Workflow, or child-agent
abstraction.

## Observed Failure

Production root run `01KXYHR2VJ6B0G6S2KYFZS9695` exposed four related defects:

- the confirmed research dimensions were persisted as `dim-ke`, `dim-ef`,
  `dim-dc`, `dim-hp`, but the Task Graph projection sorted equal-priority Tasks
  by ID and displayed `dim-dc`, `dim-ef`, `dim-hp`, `dim-ke`;
- child `01KXYHVNRG6AD63YETS56FG87J` completed time, search, time, and search
  Steps before its fifth planning turn;
- the two search outputs were approximately 41 KB and 47 KB, and full Step
  records placed approximately 95 KB of recent history back into the Brain
  context;
- the fifth provider response normalized to no content and no tool calls, so
  the child failed immediately;
- the child checkpoint was `failed` at revision 6, while the parent Task Graph
  still rendered the child's last observed state as `running` at revision 1.

The exact provider finish reason was not persisted. The fix therefore must
avoid claiming a provider-specific root cause and must record enough
non-sensitive diagnostics to distinguish empty, truncated, and tool-only
responses in future traces.

## Display Order

`TaskGraphRun.tasks` is an ordered, persisted tuple. The Task Graph projection
must filter this tuple by `active_task_refs` without sorting the surviving
Tasks. Dynamic Tasks are appended by validated graph patches and therefore
receive a deterministic display position.

This change applies only to human-facing plan projection. Ready-Task selection
continues to use the scheduler's deterministic order:

```text
priority descending
required before optional
task_id ascending
task_revision ascending
```

Display order must remain stable as Tasks transition between pending, running,
completed, and failed states.

## Bounded Brain History

The authoritative Workflow checkpoint continues to store full Step and
Operation records for replay, verification, provenance, and debugging. Before
constructing a `StepContext`, the runtime derives a separate Brain history
projection.

Each projected Step retains control information needed for the next decision:

- Step identity, index, kind, status, Operation target, and the complete scalar
  Operation arguments needed for task budgets and prerequisite routing;
- a deterministic Operation-argument fingerprint, task_id scalar, and an
  explicit human-input reset marker;
- compact failure or completion information;
- a bounded representation of `state_delta.operation_output`.

Small outputs pass through unchanged. Oversized structured outputs are reduced
with deterministic, schema-agnostic limits on collection size, nesting, string
length, per-Step size, and total recent-history size. The newest Steps are
budgeted first so the Brain sees the most recent evidence. Truncation is marked
explicitly and includes the original value's fingerprint, allowing the model
and trace readers to distinguish a projection from the authoritative output.

The generic projection must not contain research-specific field names. It may
prefer a trusted `operation_summary` supplied by an Operation, but it must
retain all scalar prerequisite values needed by the next Operation.

Research search has an evidence-preserving projection exception at the
Operation boundary: its compact summary retains every `search_id`, every
usable URL (including unrelated candidates), URL title, stance-independent
source metadata, and a bounded excerpt for claim drafting. It may remove
duplicated provider records and long fetch bodies. The complete search result
and verification records remain in the checkpoint and artifact store. This
preserves the existing runtime checks that require all search IDs and all
usable URLs while keeping the Brain payload bounded; a fingerprint alone is
never a retrieval handle.

## Empty Response Repair

`ModelStructuredPlanner` treats the first response containing neither visible
content nor tool calls as a malformed structured response. It sends one repair
request using the same bounded context and permitted tool set. The repair
prompt states that the previous response contained no executable Operation or
completion result.

The repair result follows the normal selection and validation path. If it is
also empty, the planner raises a deterministic error. This preserves the
Workflow rule that an exhausted Brain planning failure terminates the active
Node; it does not hide repeated provider failure or create an unbounded retry
loop.

The failure trace records non-sensitive response diagnostics when available:
finish reason, usage counters, sanitized content-block types, tool-call count,
and whether the failure followed repair. The adapter must distinguish a
provider-supplied finish reason from an absent one; absent values are recorded
as `unknown`, never as synthetic `stop`. Content-block types are extracted as
sanitized names before the raw provider message is discarded. Raw hidden
reasoning and provider payloads are not persisted.

## Child Terminal Observation

When `advance_child` reports a terminal failure, the child bridge exposes the
latest child checkpoint revision and status with the error. The child
checkpoint is committed first. The parent then performs one root CAS that
contains the current Attempt's `child_observation_revision` and
`child_observation_status`, the failed Attempt/Task/Graph transition, and the
resulting Task Graph projection. There is no durable parent state in which the
child is terminal but the parent projection still claims the old running
revision after that CAS succeeds.

The projected child status must therefore agree with the durable child
checkpoint at the observation boundary. This change does not automatically
retry failed Tasks and does not alter lease fencing or submission verification.

## Error Handling

- Projection failure is fail-closed in tests and must not mutate authoritative
  Step records.
- A bounded projection always declares truncation; it never silently drops an
  entire recent Step.
- One empty response can be repaired; two consecutive empty responses fail.
- Missing response diagnostics do not change the failure classification.
- Parent state is not allowed to claim a child terminal revision it has not
  loaded from the checkpoint store.

## Verification

Tests must prove:

1. Task Graph display follows persisted Task order across mixed statuses while
   scheduler order remains unchanged.
2. Multiple large Operation outputs remain complete in the checkpoint but
   produce a bounded Brain context with deterministic fingerprints.
3. Control metadata, task IDs, arguments, human-input reset markers, all
   research search IDs, and every usable URL survive projection exactly.
4. The projection handles frozen tuples and mappings used by restored state.
5. An empty response followed by a valid tool call succeeds.
6. Two empty responses produce one deterministic planning failure.
7. A failed child Task displays the child's terminal status and revision after
   one parent CAS; reload and crash-style replay converge to the same state.
8. The Kant/Hegel failure shape can proceed past two large searches without
   resending unbounded history to the model while preserving exact all-URL
   verification input.

Full Workflow, Task Graph, Research Assistant, Ruff, and mypy suites remain
required before commit and push.
