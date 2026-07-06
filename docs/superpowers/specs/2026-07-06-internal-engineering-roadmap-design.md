# Internal Engineering Roadmap

Date: 2026-07-06

## Decision

Modi Harness should follow an internal product and engineering roadmap focused
on runtime reliability, explainability, and regression control before expanding
the public surface or adding more product-facing capabilities.

The current intent-aligned runtime redesign is no longer just an architectural
proposal. The repository already contains the main runtime concepts:
`HumanIntentContext`, `IntentClarity`, `AutonomyScope`, `ActionProposal`,
`AlignmentKernel`, `ActionGateway`, `PendingJudgment`, stage transitions, and
intent lineage trace events. The next phase should therefore avoid restarting
the redesign from the old N0/N1 baseline. It should harden what exists.

The selected roadmap principle is:

```text
stabilize the action runtime
explain every consequential step
prove reliability with real scenarios
lock the contracts only after they survive regression tests
```

## Scope

This roadmap is only for internal product and engineering execution. It is not
a public narrative roadmap, launch plan, website outline, or release marketing
plan.

The roadmap may inform package releases later, but package version assignment is
out of scope. Release numbers are governed by
[`docs/project/versioning.md`](../../project/versioning.md).

## Current Baseline

The active product direction remains the intent-aligned runtime:

```text
intent shapes autonomy
alignment checks drift
governance proves safety
```

Code and documentation show that the first implementation slice has landed:

- `ActionGateway` is the action-centered execution path.
- `AlignmentKernel` makes the primary intent-fit decision.
- `GovernanceGate` wraps the existing policy gate beneath alignment.
- `HumanIntentContext`, intent clarity, autonomy scope, and stages are present
  in runtime state.
- `PendingJudgment` and judgment responses let human input update intent.
- Trace lineage records action, alignment, stage, intent version, and judgment
  join keys.
- The research assistant and Zhizheng web agent have tests that exercise parts
  of the new runtime model.

The main engineering gap is not missing vocabulary. It is that the runtime is
not yet sufficiently explainable, recoverable, and regression-proof for ongoing
feature work.

## Non-Goals

- Do not optimize for public messaging.
- Do not assign a new package version number in this roadmap.
- Do not add new Agent categories before the runtime is hardened.
- Do not preserve old approval-first or permission-first concepts merely for
  compatibility.
- Do not make trace a raw data dump. Lineage should remain compact and
  queryable.
- Do not treat scenario-specific fixes as a substitute for general runtime
  contracts.

## Considered Approaches

### Approach A: Finish the Original Intent Redesign Plan in Order

This would continue the 2026-06-23 plan from its original N0-N10 sequence.

The benefit is continuity with the existing plan. The problem is that the code
has already moved past several of those milestones. Following the old order now
would create duplicate work and obscure the real remaining risk.

### Approach B: Feature Expansion

This would add more Agents, more tools, and more product workflows on top of the
new runtime.

The benefit is fast surface-area growth. The problem is that unresolved
runtime-level questions would be multiplied across more scenarios: action
integrity, trace attribution, retry behavior, and scenario reliability would
become harder to reason about.

### Approach C: Runtime Hardening First

This approach treats the landed intent-aligned runtime as a working baseline and
focuses the next milestones on reliability, explainability, and regression.

This is the selected approach. It keeps engineering effort near the highest-risk
contracts: action execution, judgment resume, trace and cost attribution, tool
runtime governance, and scenario reliability.

## Roadmap

### R0: Establish the True Development Baseline

Outcome: the authoritative roadmap reflects the code that exists today.

Work:

- Update `docs/superpowers/plans/development-plan.md` to point at this roadmap
  as the active internal execution plan.
- Mark the intent-aligned runtime as a landed baseline, not a future-only
  direction.
- Separate already-implemented runtime concepts from remaining hardening work.
- Capture the current test baseline before starting runtime changes.

Exit gate:

- The development plan no longer implies that the project should restart the
  intent runtime from the original N0/N1 baseline.
- A maintainer can tell from docs which roadmap item should be implemented next.

### R1: Action Runtime Hardening

Status: complete as of 2026-07-06.

Outcome: `ActionGateway -> AlignmentKernel -> GovernanceGate -> execute` is the
stable main path for consequential operations.

Work:

- Strengthen tests for action integrity across judgment and resume.
- Confirm denied, redirected, constrained, and judgment-requested actions cannot
  fall through to execution.
- Make stage transitions follow the same action and alignment rules as tool
  calls.
- Ensure legacy `ToolGateway` is only shared plumbing for registry, validation,
  hooks, execution, and result wrapping.
- Keep the no-intent fallback narrow and explicit.

Exit gate:

- Every consequential action has an `action_id`, `alignment_decision_id`,
  `intent_version`, and `stage_id`.
- A reviewed action cannot be changed before resume execution.
- Alignment can deny or interrupt before governance is consulted.

Completion notes:

- Redirect, constrain, ask-judgment, and deny outcomes are covered so they do
  not fall through to handler execution.
- Reviewed action integrity is checkpointed and verified on approval resume.
- Approved stage transitions execute the reviewed action and advance the live
  intent stage only after alignment/governance allow the transition.
- `ModiSession` production wiring is locked to `ActionGateway`; `ToolGateway`
  remains shared execution plumbing.
- Judgment resolution trace now preserves the action-centered join key instead
  of falling back to raw tool-call ids.
- Regression baseline: `149 passed` across action, governance, alignment, API
  session, graph runtime, graph node, and research assistant runtime tests;
  ruff is clean for the same touched surface.

### R2: Step, Trace, and Cost Explainability

Status: complete as of 2026-07-06.

Outcome: a completed or failed run can be explained from trace without reading
the model conversation.

Work:

- Introduce a stable `step_id` for model calls, tool/action calls, validation,
  and output submission.
- Record model provider, model name, retry count, fallback usage, latency,
  usage tokens, and estimated cost where pricing is configured.
- Record tool latency, retry attempts, timeout status, and normalized failure
  codes.
- Add run-level `run_end` summaries for total model usage, latency, tool
  attempts, failures, and cost.
- Keep lineage events compact: join keys and decisions, not raw tool arguments.

Exit gate:

- Given one trace file, a maintainer can identify the slowest step, most
  expensive step, retry/fallback behavior, judgment-modified action, and final
  output intent version.

Completion notes:

- Model, tool, validation, output, and run-end events now carry stable
  `step_id` / `step_type` join fields.
- Tool results carry `parent_step_id`, attempt count, elapsed latency,
  timeout status, and normalized failure code.
- Model results carry provider/name metadata, retry budget, fallback usage,
  latency, token usage, and configured cost when available.
- `run_end` is enriched at trace flush with model usage totals, model latency,
  fallback usage, tool attempts, tool failures, and tool latency.
- Final output trace keeps intent version and stage lineage, while action
  lineage remains compact and argument-free.
- Regression baseline: `216 passed` across action, governance, alignment, API
  session, model, trace middleware, graph runtime/node/state, and research
  assistant runtime tests; ruff is clean for the touched surface.

### R3: ToolSpec Timeout and Retry Execution

Status: active as of 2026-07-06.

Outcome: tool runtime behavior matches the declared `ToolSpec` contract.

Work:

- Apply `timeout_seconds` to tool handler execution.
- Implement `RetryPolicy` for transient tool failures.
- Record each attempt with normalized error code, delay, and final outcome.
- Preserve deterministic side-effect order.
- Make idempotency cache behavior explicit when retry and caching interact.
- Avoid model-level whole-turn retries for failures that should be local tool
  retries.

Exit gate:

- Slow tools time out.
- Retryable transient failures are retried locally.
- Non-retryable failures produce one normalized terminal error.
- Attempts are visible in trace and do not corrupt action lineage.

### R4: Scenario Reliability

Outcome: real Agents prove the runtime under messy conditions.

Primary validation scenarios:

- Research Assistant:
  - thin intent starts in guided autonomy;
  - operational research input starts in bounded autonomy;
  - insufficient evidence triggers judgment before delivery;
  - final output is traceable to intent version and stage.
- Zhizheng Web Agent:
  - page-changing browser actions return transition contracts;
  - homepage return is recoverable but not recordable success;
  - stale candidates are rejected;
  - weak text matches cannot prove business success;
  - flow steps are recorded only after verified transitions.

Exit gate:

- Scenario failures are honest: the agent reports ambiguous, unchanged,
  returned-home, or failed states instead of narrating success.
- Recovery uses fresh observed state.
- Scenario-specific contracts reinforce general runtime contracts rather than
  bypassing them.

### R5: Eval and Regression Harness

Outcome: future runtime changes can be judged by golden behavior, not vibes.

Work:

- Add golden trace and lineage fixtures for core runs.
- Add regression assertions for intent initialization, action proposal,
  alignment decision, judgment update, output submission, and run summary.
- Add cost and latency regression budgets where deterministic enough.
- Keep fixtures small and contract-focused so they survive harmless wording
  changes.

Exit gate:

- A core Agent run can be automatically classified as aligned, explainable, and
  within runtime budget.
- Regressions in lineage, judgment, retry, and cost attribution fail tests.

### R6: Contract Stabilization

Outcome: the internal contracts are clear enough for sustained development.

Work:

- Stabilize the schemas for `HumanIntentContext`, `ActionProposal`,
  `AlignmentDecision`, `PendingJudgment`, `IntentLineage`, and run summary
  trace payloads.
- Remove or quarantine approval-first naming that no longer describes the
  runtime model.
- Synchronize `docs/reference/types.md` and architecture docs with the tested
  contracts.
- Define which contracts are candidates for independent protocol versions.

Exit gate:

- New Agents can be added without changing Harness core code.
- New runtime capabilities have clear extension points.
- Docs, tests, and implementation describe the same concepts.

## Priority Order

The roadmap should be executed in order:

```text
R0 -> R1 -> R2 -> R3 -> R4 -> R5 -> R6
```

R1-R3 are the runtime foundation. R4 proves that foundation with real Agents.
R5 makes future changes safer. R6 stabilizes contracts only after the behavior
has survived tests and scenarios.

## Risks

- **Trace noise:** more observability can make trace harder to read. Use spans,
  summaries, and join keys rather than repeated raw payloads.
- **Retry side effects:** retry can be dangerous for non-idempotent tools. The
  retry implementation must respect `side_effect`, `idempotent`, and tool risk.
- **Judgment sprawl:** judgment can become a workflow engine. Keep it focused on
  updating intent, boundaries, stage, and responsibility.
- **Scenario overfitting:** Zhizheng reliability work should produce general
  transition and proof patterns where possible.
- **Premature contract freezing:** stabilize contracts only after R1-R5 expose
  enough failure modes.

## Final Acceptance Criteria

This roadmap is complete when Modi Harness can demonstrate, through tests and
trace, that a consequential Agent run answers all of these questions:

1. What was the human goal?
2. How clear was the intent at each stage?
3. What autonomy did the Agent have?
4. Which action was proposed, and under which intent version?
5. What did alignment decide?
6. What did governance prove or require?
7. What human judgment changed the run?
8. Which retries, fallbacks, or recovery paths occurred?
9. What did the run cost in tokens, time, and configured currency?
10. Did the final output satisfy the success criteria?
