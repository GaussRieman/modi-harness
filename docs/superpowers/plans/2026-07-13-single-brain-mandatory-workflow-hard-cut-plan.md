# Single-Brain Mandatory-Workflow Hard-Cut Implementation Plan

Date: 2026-07-13

Status: Complete

## Goal

Ship one runtime architecture: every Agent owns at least one explicit Workflow;
operation Nodes execute trusted adapters; autonomous Nodes embed the only
AgentLoop and the only Brain. Delete all fast/slow Brain, stage, standalone,
and compatibility paths.

## Delivery rule

Implementation may use internal slices, but the final public tree has no
feature flag, alias, warning-only migration, implicit Workflow, or legacy
loader. Existing unrelated worktree changes are preserved.

## Slice 1: Close the definition and Agent contracts

- [x] Make `ModiAgent.workflows` non-empty by construction.
- [x] Make Workflow routing reject zero Workflows.
- [x] Replace Agent package discovery with the canonical package files only:
  `agent.toml`, `intent.toml`, `loop.toml`, `workflows/*.yaml`, and `skills/`.
- [x] Reject reserved obsolete control files and `agent.md` declarations.
- [x] Add source-local tests for mandatory Workflows and closed package shape.

## Slice 2: Runtime adapter and execution contract

- [x] Add versioned RuntimeOperationAdapter and CompletionValidator registries.
- [x] Separate author-selectable adapters from embedded protocol adapters.
- [x] Compute the canonical execution-contract snapshot/fingerprint at run creation.
- [x] Pin adapter/validator versions, OutputContract, capability ceiling, protocol
  version, and fixed limits.
- [x] Reject resume when the authoritative execution contract changes.

## Slice 3: Operation-only WorkflowRuntime

- [x] Add immutable definition references plus mutable WorkflowState, NodeAttempt,
  transition, event-receipt, and invocation records.
- [x] Resolve Workflow/Node inputs and execute operation Nodes through the existing
  policy/action/tool path.
- [x] Persist `prepared -> dispatching -> terminal|reconciliation_required` with
  revision-based claims and recovery-mode-constrained retry.
- [x] Validate Node completion and atomically commit declared transitions,
  terminal output, wait/resume, failure, and cancellation.

## Slice 4: Autonomous embedding and `complete_node`

- [x] Require Workflow run, Workflow, Node, and attempt scope to construct
  AgentLoop.
- [x] Replace the old mode-specific Brain implementations with one neutral Brain and
  one `plan_step` protocol.
- [x] Remove mode, rule, stage, `finish`, `stop`, and standalone finalization fields.
- [x] Implement embedded-only `complete_node`; Harness validates schema, semantic
  validator, plan closure, evidence, pending effects, and next-Node input before
  WorkflowRuntime commits completion.
- [x] Map planner provider/parse/normalization/schema failure to Node `failed`, and
  integrity violations directly to Workflow failure.

## Slice 5: Atomic public switch and Agent migration

- [x] Route sync, incremental stream, async stream, API, and CLI through WorkflowRuntime.
- [x] Delete nested/subagent entry points from V1 rather than preserving a second runtime.
- [x] Carry `workflow_id` only in the control envelope; pin it on resume.
- [x] Migrate root Agents, retained examples, plugin/factory fixtures, and
  programmatic test Agents to explicit Workflows.
- [x] Delete old Brain modules, stage types/modules, standalone graph branches,
  package files, duplicated Markdown Agents, and compatibility tests.

## Slice 6: Repository cleanup and verification

- [x] Delete superseded specs/plans and update live docs to the single architecture.
- [x] Run focused Workflow/Brain/Loop tests after each slice.
- [x] Run the full non-live suite, Ruff, mypy, and `git diff --check`.
- [x] Search live source/config/tests/docs for every banned concept and remove all
  remaining runtime references.

## Delivered runtime guarantees

- Completion validators are trusted Agent-owned bindings; unknown declarations
  fail construction.
- `complete_node` checks schema, semantic validation, required evidence,
  TaskPlan closure, unresolved Steps/effects, and next-Node input readiness.
- Waiting state pins the exact proposal and invocation; approval executes that
  proposal, rejection records denial, and user input becomes durable state.
- Operation recovery mode narrows ToolGateway retry policy;
  manual-reconciliation side effects run at most once.
- Sync and async streams expose incremental Workflow, Node, Operation, Step,
  wait-resolution, and normalized terminal events.
