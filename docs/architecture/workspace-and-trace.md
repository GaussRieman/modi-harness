# Workspace and Trace

## Workspace

`WorkspaceManager` owns run-scoped files under
`<workspace_root>/<run_id>/`. Kind directories are created lazily:

```text
input/  state/  references/  artifacts/  drafts/  logs/
```

All paths are resolved beneath the run root. Parent traversal and symlink
escape are rejected. Files are represented by `WorkspaceRef`, including trust,
kind, path, MIME type, and optional artifact identity.

Child runs live below the parent run's `sub/` directory and retain their own
run identity. Workspace is for inputs, intermediate files, drafts, artifacts,
state, and logs; it is distinct from reusable Memory.

## Trace

Graph nodes append `TraceEvent` values to pending state. `TraceMiddleware`
drains them after transitions, and `TraceRecorder` writes append-only
`logs/trace.jsonl`.

Every event carries run, root-run, parent-run, thread, type, timestamp, and
payload identity. Sensitive keys are recursively redacted. Oversized payloads
spill into `logs/payloads/` and the event retains a reference.

Trace captures model timing and usage, context-size estimates, Tool decisions,
hooks, interactions, task transitions, Memory operations, validation, and
submission. It records what happened; it is not fed back as Memory.

## Intent lineage

Trace must prove *alignment*, not just execution: a maintainer should be able to
answer "which intent version and stage produced this action, and what decided
it?" from the recorded trace alone. The intent-aligned runtime emits a lineage
event stream for that:

- `intent_initialized` / `intent_clarity_estimated` / `autonomy_scope_derived` —
  the opening of a run: the intent field, its model-estimated clarity, and the
  autonomy scope derived from that clarity.
- `action_proposed` — a normalized `ActionProposal` entered alignment. Carries
  `action_id`, `kind`, `tool_name`, `intent_version`, `stage_id`.
- `alignment_decision` — the `AlignmentKernel` verdict. Carries
  `alignment_decision_id`, `decision`, `reason`, `boundary_hits`, and
  `model_judged` (whether the model produced the semantic judgment or only the
  deterministic floor ran).
- `intent_lineage_recorded` — the compact join across the above:
  `action_id`, `alignment_decision_id`, `intent_version`, `stage_id`,
  `judgment_id`, `boundary_hits`. This is the record `trace/lineage.py` reads.
- `judgment_requested` / `judgment_resolved` — a human judgment was solicited and
  then resolved; the resolution carries the resulting `intent_version`.
- `intent_updated` — a judgment edited the intent; the version bumps and clarity
  / autonomy are recomputed.
- `output_submitted` carries `intent_version` and `stage_id` so the final output
  is itself traceable to the intent it was produced under.

Lineage events carry only join keys — never raw tool arguments — so the trace
proves alignment without becoming a new secret-leak path. `trace/lineage.py`
provides `read_lineage` (extract the `IntentLineage` records from an event
stream), `group_by_intent_version` / `group_by_stage`, and `lineage_for_action`
for reading and grouping after the fact.

## Runtime caches

Loader caches use file modification times. Model adapters cache per model
specification. Memory recall caches are per Session/run and invalidate on
committed writes. Caches change cost, not architectural authority.

## Source entry points

- `workspace/manager.py`
- `trace/recorder.py`, `trace/lineage.py`, `graph/trace_middleware.py`
- `agents/loader.py`, `skills/loader.py`
- `models/cache.py`, `memory/recall_cache.py`

