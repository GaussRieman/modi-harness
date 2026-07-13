# Single-Brain Mandatory-Workflow Hard-Cut Implementation Plan

Date: 2026-07-13

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

- Make `ModiAgent.workflows` non-empty by construction.
- Make Workflow routing reject zero Workflows.
- Replace Agent package discovery with the canonical package files only:
  `agent.toml`, `intent.toml`, `loop.toml`, `workflows/*.yaml`, and `skills/`.
- Reject reserved obsolete control files and `agent.md` declarations.
- Add source-local tests for mandatory Workflows and closed package shape.

## Slice 2: Runtime adapter and execution contract

- Add versioned RuntimeOperationAdapter and CompletionValidator registries.
- Separate author-selectable adapters from embedded protocol adapters.
- Compute the canonical execution-contract snapshot/fingerprint at run creation.
- Pin adapter/validator versions, OutputContract, capability ceiling, protocol
  version, and fixed limits.
- Reject resume when the authoritative execution contract changes.

## Slice 3: Operation-only WorkflowRuntime

- Add immutable definition references plus mutable WorkflowState, NodeAttempt,
  transition, event-receipt, and invocation records.
- Resolve Workflow/Node inputs and execute operation Nodes through the existing
  policy/action/tool path.
- Persist `prepared -> dispatching -> terminal|reconciliation_required` with
  revision-based claims and recovery-mode-constrained retry.
- Validate Node completion and atomically commit declared transitions,
  terminal output, wait/resume, failure, and cancellation.

## Slice 4: Autonomous embedding and `complete_node`

- Require Workflow run, Workflow, Node, and attempt scope to construct
  AgentLoop.
- Replace RuleBrain/SlowModelBrain with one neutral Brain implementation and
  one `plan_step` protocol.
- Remove mode, rule, stage, `finish`, `stop`, and standalone finalization fields.
- Implement embedded-only `complete_node`; Harness validates schema, semantic
  validator, plan closure, evidence, pending effects, and next-Node input before
  WorkflowRuntime commits completion.
- Map planner provider/parse/normalization/schema failure to Node `failed`, and
  integrity violations directly to Workflow failure.

## Slice 5: Atomic public switch and Agent migration

- Route sync/stream/async/API/CLI/subagent entry points through WorkflowRuntime.
- Carry `workflow_id` only in the control envelope; pin it on resume.
- Migrate root Agents, retained examples, plugin/factory fixtures,
  programmatic test Agents, and nested subagents to explicit Workflows.
- Delete old Brain modules, stage types/modules, standalone graph branches,
  package files, duplicated Markdown Agents, and compatibility tests.

## Slice 6: Repository cleanup and verification

- Delete superseded specs/plans and update live docs to the single architecture.
- Run focused Workflow/Brain/Loop tests after each slice.
- Run the full non-live suite, Ruff, mypy, and `git diff --check`.
- Search live source/config/tests/docs for every banned concept and remove all
  remaining runtime references.

## First executable target

Complete Slice 1 and the pure state/adapter contracts from Slice 2 first. They
create the hard boundaries needed to implement WorkflowRuntime without keeping
standalone behavior alive.
