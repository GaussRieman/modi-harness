# Long-Running Task Runtime Implementation Plan

Date: 2026-07-17

Status: Proposed

Design source:
`docs/superpowers/specs/2026-07-17-long-running-task-runtime-design.md`
(`af52366`)

## Goal

Implement the approved general long-running task runtime as five vertical
slices. The first slice must already run and recover a confirmed Intent through
an Operation-only Task Graph and independent Goal verification. Later slices
add isolated child Workflow runs, real concurrency and joins, dynamic
replanning/HITL, and finally Research Assistant migration.

Research is an application of the runtime, not a source of generic semantics.

## Delivery Rules

- Keep mandatory immutable Workflow as the outer control plane.
- A Task-Graph-enabled root has exactly one authoritative `RootRunSnapshot`;
  `TaskPlan` remains a read-only compatibility projection.
- Existing `operation` and `autonomous` Workflows retain their current
  `workflow-v1` execution-contract shape, fingerprint behavior, and LangGraph
  checkpoint path. Only Workflows containing `task_graph` use the new contract
  envelope and durable root CAS store.
- Child Flow is an ordinary pinned Workflow definition. Do not create another
  flow engine or revive the deleted legacy subagent runtime.
- Parent Runtime is the only graph writer. Children return submissions and
  discovered-work suggestions only.
- Each task below starts with failing tests, lands the smallest implementation
  that passes them, and ends with `git diff --check` plus a focused commit.
- Do not begin Research Assistant migration until the generic Operation-only,
  single-child, and concurrent-join fixtures are all green.
- Do not add Postgres Task Graph support in V1. Legacy Postgres Workflow runs
  remain supported; Task Graph construction fails closed until a durable
  Postgres CAS adapter exists.

## Slice 1: Operation-Only Vertical Runtime

### Task 1: Extend immutable Workflow definitions with `task_graph`

**Files**

- Modify `src/modi_harness/workflow/types.py`
- Modify `src/modi_harness/workflow/definition.py`
- Modify `src/modi_harness/workflow/__init__.py`
- Add `src/modi_harness/workflow/schema_registry.py`
- Modify `tests/workflow/test_definition.py`
- Modify `tests/workflow/test_loader_and_router.py`

**Work**

- Add `task_graph` to `WorkflowExecution` and add `WORKFLOW_WAIT = "$wait"`.
- Represent Task Graph fields as one frozen `TaskGraphNodeConfig` attached to
  the existing frozen `Node`; keep operation/autonomous fields unchanged.
- Close the Task Graph YAML field set exactly as defined by the design:
  planner, graph policy, context builder, Task/Group/Criterion validators,
  Goal Verifier, allowed Operations, parent-inline components, human
  contracts, child template IDs, limits, completion schema ID, and transitions.
- Add a versioned `SchemaRegistry`. Keep JSON Schema `$ref` local-only; resolve
  external structured contracts by registry ID before run creation.
- Accept `waiting: $wait` only for Task Graph Nodes. `$wait` is non-terminal and
  never participates in reachability as another Node.
- Include the normalized Task Graph config and resolved schema snapshots in the
  Workflow definition fingerprint.

**Tests**

- Parse/canonicalize/fingerprint a valid Task Graph Workflow.
- Reject unknown fields, missing required bindings, illegal `$wait` use,
  external JSON `$ref`, unknown schema IDs, and unreachable real Nodes.
- Prove existing operation/autonomous definition fingerprints do not change.

**Verify**

```bash
uv run pytest tests/workflow/test_definition.py tests/workflow/test_loader_and_router.py -q
uv run ruff check src/modi_harness/workflow tests/workflow
```

### Task 2: Add pinned component and rich verifier contracts

**Files**

- Add `src/modi_harness/workflow/components.py`
- Modify `src/modi_harness/workflow/contract.py`
- Modify `src/modi_harness/api/agent.py`
- Modify `src/modi_harness/types.py`
- Modify `src/modi_harness/workflow/session.py`
- Modify `tests/workflow/test_contract.py`
- Add `tests/workflow/test_components.py`

**Work**

- Add frozen `PinnedComponent`, Planner, Context Builder, Graph Policy,
  Task/Group/Criterion Verifier, Goal Verifier, ParentInline component, and
  HumanTaskContract definitions.
- Require explicit implementation digest, protocol version, configuration,
  input/output schema IDs, supported outcomes, and idempotency-key builder for
  every new component.
- Implement closed registries with `resolve_pinned(snapshot)`. Resume must load
  the exact digest or return `reconciliation_required`; it may not resolve the
  current component by ID alone.
- Define rich, JSON-serializable outcomes instead of overloading the existing
  boolean `CompletionValidator`.
- Add durable `PlannerInvocation` and `VerifierInvocation` records keyed by
  component fingerprint plus frozen input hash.
- Extend `build_execution_contract` with a Task Graph dependency envelope only
  when the selected Workflow contains a Task Graph Node. Embed child template
  snapshots later in Slice 2.
- Keep the legacy contract snapshot byte-for-byte stable for Workflows without
  `task_graph`.

**Tests**

- Reject duplicate IDs, invalid digests, unsupported outcomes, schema mismatch,
  capability expansion, and unknown Task Graph bindings.
- Resume the same invocation after the live registry entry changes and obtain
  the pinned result.
- Fail closed when the exact implementation digest is unavailable.
- Prove repeated invocation keys return the recorded result without calling
  the component twice.

**Verify**

```bash
uv run pytest tests/workflow/test_contract.py tests/workflow/test_components.py -q
uv run mypy src/modi_harness/workflow
```

### Task 3: Implement Intent, graph, Task, Group, Attempt, and patch values

**Files**

- Add `src/modi_harness/long_task/__init__.py`
- Add `src/modi_harness/long_task/types.py`
- Add `src/modi_harness/long_task/transitions.py`
- Add `src/modi_harness/long_task/graph.py`
- Add `tests/long_task/__init__.py`
- Add `tests/long_task/test_types.py`
- Add `tests/long_task/test_transitions.py`
- Add `tests/long_task/test_graph.py`

**Work**

- Add frozen, JSON-round-trippable values for Intent versions, criteria,
  TaskGraphRun, TaskRun revisions, Group revisions, TaskAttempt,
  CandidateReceipt, verification records, exact dependency refs, leases,
  fencing epochs, locks, and audit events.
- Keep logical IDs separate from immutable revision keys. Dependencies always
  point to exact Task/Group revisions.
- Implement the complete legal transition matrix, including wait/resume,
  repair, prelaunch cancellation, submitted cancellation, and retiring
  resources.
- Implement pure readiness, deterministic ordering, criterion coverage, graph
  terminality, and Group achievability functions. Do not persist derived ready
  queues or progress percentages.
- Implement `GraphSeed` as revision-zero `GraphPatch` and validate count/depth,
  acyclicity, authority, component IDs, exact target revisions, executable
  coverage, completed-history immutability, and patch trigger resolution.
- Implement `expand_task` as one pure transition that creates the Group and
  initial child Task revisions and rewrites only pending dependents.

**Tests**

- Round-trip every value and reject malformed or non-JSON state.
- Cover every legal and illegal Task/Attempt/Group/Graph transition.
- Reject cycles, stale graph/target revisions, coverage loss, authority
  expansion, completed mutation, double expansion, and budget overflow.
- Prove stable ready ordering and exact revision binding across restart.

**Verify**

```bash
uv run pytest tests/long_task/test_types.py tests/long_task/test_transitions.py tests/long_task/test_graph.py -q
uv run ruff check src/modi_harness/long_task tests/long_task
```

### Task 4: Add durable root CAS storage and immutable blob staging

**Files**

- Add `src/modi_harness/checkpoint/root.py`
- Modify `src/modi_harness/checkpoint/__init__.py`
- Modify `src/modi_harness/checkpoint/factory.py`
- Add `src/modi_harness/workspace/artifacts.py`
- Modify `src/modi_harness/workspace/__init__.py`
- Modify `src/modi_harness/api/session.py`
- Modify `src/modi_harness/__main__.py`
- Add `tests/checkpoint/test_root_store.py`
- Add `tests/workspace/test_artifacts.py`
- Modify `tests/api/test_workflow_session.py`

**Work**

- Add `RootRunSnapshot(workflow_state, long_task_state, revision, event)` and
  `RootCheckpointStore.load/create/compare_and_swap`.
- Implement `InMemoryRootCheckpointStore` with a lock and
  `SqliteRootCheckpointStore` with a dedicated table in the configured SQLite
  database and transactional expected-revision update.
- Index root snapshots by both root run ID and thread ID for session restore.
- Require callers to reload and recompute after conflict; never merge stale
  graph snapshots.
- Make `ModiSession` require a shared root store when any selected Agent owns a
  Task Graph Workflow. CLI construction uses the SQLite root-store factory;
  tests pass a shared in-memory store explicitly.
- Route a Task-Graph-enabled Workflow through the root store from run creation,
  not only after entering the Task Graph Node. Legacy Workflows continue to use
  the current checkpointer path.
- Add content-addressed `TaskArtifactStore.stage/seal/read_verified`. Seal via
  temp write, fsync, SHA-256 path, and create-if-absent atomic rename. Never use
  legacy `save_artifact` for committed graph artifacts.

**Tests**

- CAS create/update/conflict, restart, corrupt snapshot, thread lookup, and
  atomic event persistence for memory and SQLite.
- Reject Task Graph startup without a durable compatible store and reject the
  V1 Postgres path without affecting legacy Postgres construction.
- Prove sealed blobs cannot be overwritten or changed between verification and
  metadata commit; reject missing/hash/size mismatch.
- Preserve existing WorkspaceManager behavior for non-Task-Graph callers.

**Verify**

```bash
uv run pytest tests/checkpoint/test_root_store.py tests/workspace/test_artifacts.py tests/api/test_workflow_session.py -q
```

### Task 5: Deliver the Operation-only Task Graph vertical slice

**Files**

- Add `src/modi_harness/long_task/runtime.py`
- Add `src/modi_harness/long_task/verification.py`
- Modify `src/modi_harness/workflow/runtime.py`
- Modify `src/modi_harness/workflow/session.py`
- Modify `src/modi_harness/workflow/router.py`
- Add `tests/long_task/test_operation_runtime.py`
- Add `tests/api/test_long_task_session.py`
- Modify `tests/workflow/test_runtime.py`

**Work**

- Add a deterministic Task Graph Node executor that advances one root snapshot
  by CAS and returns `running`, `waiting`, `completed`, or `failed` to the outer
  Workflow Runtime.
- Require a confirmed Intent input. Draft/unconfirmed Intent cannot create a
  seed graph.
- Call the pinned Planner once for a revision-zero seed, validate it, and
  dispatch only the persisted Operation adapter binding.
- Reuse existing Operation prepare/dispatch/reconcile behavior, but record the
  Task Attempt, dispatch key, lease/fence, CandidateReceipt, validator
  invocation, committed outputs, and Task transition in root state.
- Implement independent criterion and minimal Goal verification. Completing
  every Task does not complete the Node until Goal verification passes.
- Persist `$wait` without changing Node attempt or graph revision; resume the
  exact pending record.
- Add a read-only legacy `TaskPlan` projection for CLI/API consumers. Reject any
  write-back from this projection.

**Acceptance scenario**

```text
confirmed Intent
  -> deterministic seed with two serial Operation Tasks
  -> both candidates verified
  -> Goal Verifier passes
  -> Task Graph Node completes
  -> restart at every boundary produces the same result
```

**Verify**

```bash
uv run pytest tests/long_task/test_operation_runtime.py tests/api/test_long_task_session.py tests/workflow/test_runtime.py -q
```

## Slice 2: Isolated Child Workflow Runs

### Task 6: Add static child template declarations and contract pinning

**Files**

- Add `src/modi_harness/long_task/templates.py`
- Modify `src/modi_harness/api/agent.py`
- Modify `src/modi_harness/agents/loader.py`
- Modify `src/modi_harness/workflow/contract.py`
- Modify `tests/api/test_agent.py`
- Modify `tests/agents/test_research_assistant.py`
- Add `tests/long_task/test_templates.py`

**Work**

- Add `ChildTemplateRef(id, agent_name, workflow_id, limits)` to ModiAgent and
  declarative Agent loading.
- Resolve each template against the session's immutable Agent registry before
  root run creation.
- Embed the complete child Agent definition, child Workflow definition,
  schemas, capabilities, permission profile, limits, and child execution
  contract fingerprint in the parent contract.
- Reject child Workflows containing `task_graph` in V1 and reject templates
  that expand root authority or reference unknown Agent/Workflow IDs.
- Preserve an empty template set for every existing Agent.

**Tests**

- Programmatic and declarative template loading, duplicate IDs, unknown refs,
  authority intersection, contract fingerprint change, and recursion rejection.

### Task 7: Build ContextManifest and child checkpoint lifecycle

**Files**

- Add `src/modi_harness/long_task/context.py`
- Add `src/modi_harness/long_task/child.py`
- Add `src/modi_harness/long_task/dispatch.py`
- Modify `src/modi_harness/workspace/manager.py`
- Add `tests/long_task/test_context.py`
- Add `tests/long_task/test_child_run.py`

**Work**

- Build immutable ContextManifest values from the confirmed Intent, exact Task
  revision, direct dependency summaries/refs, scoped memory, authority
  intersection, and budgets.
- Allocate deterministic child checkpoint namespaces containing root, parent
  Node attempt, Task Attempt, and child run IDs.
- Persist reciprocal parent/child IDs and fingerprints in both aggregates.
- Implement parent-prepare -> child-checkpoint-create -> idempotent
  `ensure_started(dispatch_key)` -> parent-acknowledgement.
- Execute the pinned child Workflow directly; do not route again or expose the
  parent transcript/full graph.
- Create a child workspace partition and expose only manifest-authorized refs.

**Tests**

- Context isolation, immutable resume, authority narrowing, namespace
  collisions, missing child checkpoint, lost acknowledgement, orphan child,
  fingerprint mismatch, and child recursion rejection.

### Task 8: Commit child submissions through parent verification

**Files**

- Add `src/modi_harness/long_task/submission.py`
- Modify `src/modi_harness/long_task/runtime.py`
- Modify `src/modi_harness/long_task/verification.py`
- Modify `src/modi_harness/long_task/child.py`
- Add `tests/long_task/test_submission.py`
- Add `tests/long_task/test_child_verification.py`

**Work**

- Persist CandidateSubmission in the child checkpoint before delivery.
- Enforce global submission ID plus monotonic `(attempt_id, sequence)` and
  stable payload hash.
- Record CandidateReceipt and Task `verifying` in one root CAS.
- Validate schema, child/Attempt/fence binding, staged blob hashes, provenance,
  visibility, and every pinned Task Verifier.
- Commit Artifact/Evidence metadata and Task completion in one root CAS.
  Children can submit EvidenceClaims but cannot write verification status.
- Replay pending validator records after crash; a repeated receipt returns the
  prior durable outcome.
- Support repairable resubmission only with the same ContextManifest and a new
  lease epoch/sequence.

**Tests**

- Duplicate/conflicting/gapped submissions, stale fencing, missing blob, bad
  hash, failed validator, repairable candidate, parent crash during each
  verification boundary, and successful single-child end-to-end completion.

### Task 9: Prove single-child crash recovery end to end

**Files**

- Add `tests/long_task/test_child_recovery.py`
- Modify `tests/api/test_long_task_session.py`

**Work**

- Add crash-injection fixtures at parent prepare, child creation, dispatch,
  acknowledgement, child waiting, staging, receipt, validation, and acceptance.
- Restore from new Session instances using the same root/child stores and
  Workspace.
- Prove completed Task outputs survive child/parent failure and no orphan can be
  adopted by another Attempt.

**Slice gate**

```bash
uv run pytest tests/long_task tests/api/test_long_task_session.py -q
uv run ruff check src tests
uv run mypy src/modi_harness
```

## Slice 3: Concurrent Scheduler and Join Semantics

### Task 10: Add bounded concurrent scheduling, leases, and locks

**Files**

- Add `src/modi_harness/long_task/scheduler.py`
- Add `src/modi_harness/long_task/resources.py`
- Modify `src/modi_harness/long_task/runtime.py`
- Modify `src/modi_harness/tools/gateway.py`
- Add `tests/long_task/test_scheduler.py`
- Add `tests/long_task/test_leases.py`
- Modify `tests/tools/test_gateway.py`

**Work**

- Use a bounded in-process executor for real concurrent child runs; retain the
  synchronous public Session API.
- Derive ready work deterministically, then claim Task, slot, exact resource
  locks, Attempt, dispatch key, lease epoch, and fencing token in one root CAS.
- Enforce global/per-template concurrency and exclusive-by-path write locks.
- Renew leases only through parent CAS after verified child liveness.
- Treat expiry as suspect: reconcile before replacement or lock release.
- Add fence validation to every child side-effecting gateway call. Cancelled or
  stale children can neither mutate parent state nor perform a new side effect.
- Count retiring physical executors against concurrency until stop or definite
  absence.

**Tests**

- Parallel independent Tasks, serial dependencies, mixed DAG, stable ordering,
  per-template cap, resource conflict, lease renewal/expiry, stale side effect,
  and restart with no process-local handles.

### Task 11: Implement Groups, `all_required`, and `any_success`

**Files**

- Modify `src/modi_harness/long_task/graph.py`
- Modify `src/modi_harness/long_task/runtime.py`
- Modify `src/modi_harness/long_task/scheduler.py`
- Add `tests/long_task/test_groups.py`
- Add `tests/long_task/test_any_success.py`

**Work**

- Commit Group state from child Task transitions; Groups never create Attempts.
- Run the pinned Group Verifier against the exact accepted child set.
- Implement `all_required` failure propagation and optional-child behavior.
- Implement `any_success` winner selection as one CAS: record winner, complete
  Group, cancel pending losers, fence running/submitted losers, create durable
  cancellation requests, and mark their resources retiring.
- Ignore late loser receipts and release locks only after physical stop or
  reconciliation.
- Make downstream readiness depend on exact Group revision completion.

**Tests**

- All-required success/failure, optional failure, simultaneous winner race,
  Group validator rejection then alternate winner, pending/prepared/running/
  submitted loser cancellation, late result, and lock retirement.

### Task 12: Add concurrency crash and determinism matrix

**Files**

- Add `tests/long_task/test_concurrent_recovery.py`
- Add `tests/long_task/test_determinism.py`

**Work**

- Inject restart around concurrent claims, winner CAS, cancellation, lease
  expiry, and physical stop acknowledgement.
- Repeat equivalent runs with different completion timing and assert identical
  committed graph/output when policy inputs are the same.
- Assert no duplicate side effects and no max-concurrency overflow.

**Slice gate**

```bash
uv run pytest tests/long_task/test_scheduler.py tests/long_task/test_leases.py tests/long_task/test_groups.py tests/long_task/test_any_success.py tests/long_task/test_concurrent_recovery.py tests/long_task/test_determinism.py -q
```

## Slice 4: Dynamic Planning, Intent Rebase, and HITL

### Task 13: Add rolling-wave planning and validated local patches

**Files**

- Add `src/modi_harness/long_task/planning.py`
- Modify `src/modi_harness/long_task/graph.py`
- Modify `src/modi_harness/long_task/runtime.py`
- Add `tests/long_task/test_planning.py`
- Add `tests/long_task/test_expansion.py`

**Work**

- Build a compact parent context projection; never concatenate child histories.
- Invoke Planner only for seed, ready expandable Task, verification failure,
  deadlock, discovered work, Goal gap, or explicit user change.
- Persist PlannerInvocation before applying the returned GraphPatch.
- Support the approved V1 patch operations and reject full-snapshot replacement.
- Bound Task count, depth, child count, replan count, and Planner repair attempts.
- Process child discovered-work suggestions as untrusted patch input.

**Tests**

- Minimal seed, lazy expansion, stale patch retry, discovered work, deadlock
  repair, invalid model patch feedback, and deterministic budget exhaustion.

### Task 14: Implement atomic IntentRebase and reusable completion proofs

**Files**

- Add `src/modi_harness/long_task/intent.py`
- Modify `src/modi_harness/long_task/runtime.py`
- Modify `src/modi_harness/long_task/verification.py`
- Add `tests/long_task/test_intent.py`
- Add `tests/long_task/test_rebase.py`

**Work**

- Add Intent clarification/confirmation values and require initial confirmation
  through existing Workflow review before graph execution.
- Classify material versus local changes.
- Apply confirmed Intent version, retained/invalidated bindings, superseding
  Task/Group revisions, exact dependency rewrites, active fencing, criterion
  reset, and graph revision in one root CAS.
- Require a pinned Rebase Verifier before reusing prior completed output.
- Preserve all superseded history and committed immutable artifacts.

**Tests**

- High-impact question gate, low-impact assumption, material approval, retained
  completion, invalidated active Attempt, exact dependency revision rewrite,
  stale rebase conflict, and no silent authority expansion.

### Task 15: Add parent-inline, human Task, and complete Goal outcomes

**Files**

- Add `src/modi_harness/long_task/executors.py`
- Modify `src/modi_harness/long_task/runtime.py`
- Modify `src/modi_harness/long_task/verification.py`
- Modify `src/modi_harness/workflow/session.py`
- Add `tests/long_task/test_executors.py`
- Add `tests/long_task/test_goal_verification.py`
- Modify `tests/api/test_long_task_session.py`

**Work**

- Dispatch only persisted Operation/child/parent-inline/human bindings.
- Record parent-inline calls with stable component invocation keys.
- Persist `PendingTaskDecision` and `PendingGoalDecision`; validate and consume
  one response exactly once against expected root revision.
- Implement Goal outcomes: passed -> complete, repairable gap -> validated patch,
  ambiguous -> `$wait`, impossible -> fail.
- Allow executor switching only by creating a new Attempt using another
  pre-pinned binding inside the same authority ceiling.
- Re-run every required criterion during final Goal verification even when
  incremental criterion records already exist.

**Tests**

- Parent-inline replay, human wait/restart/duplicate response, ambiguous Goal
  repair, impossible Goal failure, final criterion recheck, executor switching,
  and forbidden dynamic binding.

### Task 16: Add trace, API, and CLI projections

**Files**

- Modify `src/modi_harness/types.py`
- Modify `src/modi_harness/trace/recorder.py`
- Modify `src/modi_harness/api/session.py`
- Modify `src/modi_harness/cli/renderer.py`
- Modify `src/modi_harness/cli/runner.py`
- Add `tests/trace/test_long_task_trace.py`
- Add `tests/cli/test_long_task_renderer.py`
- Modify `tests/api/test_long_task_session.py`

**Work**

- Emit compact Intent/Graph/Task/Attempt/child/join/verification events keyed by
  root revision; keep prompts, transcripts, and artifact bodies out of events.
- Add read-only root graph, Task history, child observation, and criterion APIs.
- Render one stable Task Graph progress panel with nested child status and no
  duplicate scope/progress panels.
- Expose cancelled/retiring/reconciliation states and current human request.
- Preserve existing legacy TaskPlan renderer behavior.

**Slice gate**

```bash
uv run pytest tests/long_task tests/api/test_long_task_session.py tests/trace/test_long_task_trace.py tests/cli/test_long_task_renderer.py -q
uv run ruff check src tests
uv run mypy src/modi_harness
```

## Slice 5: Application Migration and Release Gate

### Task 17: Migrate Research Assistant onto the generic runtime

**Files**

- Modify `agents/research_assistant/agent.py`
- Modify `agents/research_assistant/workflows/deep_research.yaml`
- Add `agents/research_assistant/workflows/research_dimension.yaml`
- Add `agents/research_assistant/long_task.py`
- Modify `agents/research_assistant/README.md`
- Modify `tests/agents/test_research_assistant.py`
- Modify `tests/agents/test_research_tools.py`
- Add `tests/agents/test_research_long_task.py`

**Work**

- Change scope confirmation output from a mutable TaskPlan to a confirmed
  research Intent with explicit criteria, constraints, and candidate dimensions.
- Replace the `investigate` autonomous Node with a Task Graph Node using pinned
  research Planner, Context Builder, Task/Criterion/Goal Verifiers, and one
  static research child template.
- Run each dimension through `research_dimension.yaml` as an isolated child
  Workflow. Preserve mandatory per-search current-time token, structured query
  planning, cumulative source verification, and Finding provenance.
- Execute independent dimensions concurrently; serialize only declared
  dependencies. Parent verifies Findings and synthesizes the final report.
- Keep all research evidence rules in `agents/research_assistant/long_task.py`;
  generic runtime code must not know about URLs, search IDs, citations,
  confidence, Tesla, Xiaomi, or any research-specific schema.
- Keep quick lookup and unsupported-query Workflows unchanged.

**Tests**

- Tesla Model Y versus Xiaomi YU7 comparison produces separate entity-aware
  dimension Tasks and parallel child runs.
- `Teslamodely` and `小米YU` normalization remains handled by the approved query
  planning path without generic-runtime special cases.
- Every child search obtains fresh current time, verifies all usable URLs, and
  returns a candidate Finding that the parent must accept before Task completion.
- One dimension failure does not erase completed siblings; crash resumes from
  stored child/root checkpoints.
- Final report cites only committed Evidence and exposes explicit limitations.

### Task 18: Compatibility, documentation, and full release verification

**Files**

- Modify `docs/architecture/` documents that describe Workflow execution,
  checkpoints, Workspace, and Research Assistant
- Modify `README.md` only where public behavior changed
- Modify changelog/version documentation according to repository policy
- Modify or add focused compatibility tests under `tests/workflow/`,
  `tests/api/`, and `tests/agents/`

**Work**

- Prove every existing non-Task-Graph Workflow still uses legacy contract and
  checkpoint behavior.
- Prove legacy TaskPlan remains display-only and cannot mutate Task Graph state.
- Search source/tests/docs for a second graph owner, restored legacy subagent
  runtime, child recursion, unpinned component lookup, or mutable committed
  Artifact path.
- Document storage migration, V1 SQLite/memory support, child inspection,
  cancellation semantics, and HITL resume.
- Run the entire non-live suite and static checks. Treat any new warning or
  skipped long-task test as a release blocker.

**Final verification**

```bash
uv run pytest -q
uv run ruff check src tests agents
uv run mypy src/modi_harness
git diff --check
git status --short
```

## Final Acceptance

Implementation is complete only when all of the following are demonstrated in
automated tests:

- a confirmed Intent produces a validated rolling Task Graph;
- Operation, child, parent-inline, and human Tasks use only pinned bindings;
- independent Tasks run concurrently in isolated child Workflow runs;
- serial, parallel, and mixed behavior follows exact dependencies and Groups;
- `all_required` and `any_success` survive races, cancellation, and restart;
- Task and Goal completion are independently verified;
- root/child crash recovery preserves completed work and rejects stale output;
- Intent rebase preserves only outputs proven reusable;
- Workspace bytes are immutable-by-hash and Evidence verification is
  parent-owned;
- Research Assistant uses the generic runtime without leaking research logic
  into `src/modi_harness/long_task/`;
- all existing legacy Workflow tests remain green.
