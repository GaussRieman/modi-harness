# Brain-Agent Loop Runtime Plan

Date: 2026-07-07

## Goal

Implement the Brain-Agent Loop Runtime design: make `LoopState`,
`StepContext`, `StepDecision`, and `StepRecord` real runtime contracts; route
model control through structured Brain decisions; and record Step-level trace
while reusing the existing action/alignment/governance execution path.

## Non-Goals

- Do not implement the full Agent package split.
- Do not implement a full FastRule DSL.
- Do not replace LangGraph.
- Do not replace `ActionGateway`; place it below Step-owned
  `RuntimeOperationProposal`s.
- Do not restructure every graph node around a new object model in one patch.

## Slice

### P1: Contracts

- Add a `modi_harness.loop` package with JSON-serializable TypedDict contracts:
  `LoopState`, `LoopContinuation`, `StepKind`, `HumanJudgmentAssessment`,
  `ContinuationBasis`, `RuntimeOperationProposal`, `BrainIntentPatch`,
  `StepDecision`, `LoopContinuationDecision`, `StepRecord`, and `StepContext`.
- Export these contracts from `modi_harness.loop`.
- Add the new contract names to the internal type reference after implementation.

### P2: Runtime State

- Extend `AgentState` / `MainGraphState` with `loop_state`,
  `step_records`, `current_step`, and `last_continuation_decision`.
- Use append reducers for `step_records` so checkpoint merges behave like
  messages, tool calls, and trace events.

### P3: Loop Scaffolding

- Add helper functions that initialize `LoopState`, build a minimal
  slow-mode `StepDecision`, create `StepRecord`s, and create
  `LoopContinuationDecision`s.
- Route graph control through `brain_step_node`; the graph-backed slow planner
  calls the model only to obtain `submit_step_decision`.
- Emit `step_planned`, `step_completed`, and `loop_continuation_decision`
  trace events around Brain-owned steps.

### P4: Tests

- Add contract tests for the new fields and reducers.
- Add graph node tests proving setup initializes `loop_state`.
- Add Brain step tests proving a slow Brain `StepRecord` is appended and
  trace events include `loop_id`, `step_id`, `reasoning_mode`, and
  `LoopContinuationDecision`.
- Add negative tests for required StepDecision invariants at the helper level:
  human judgment required plus operation is rejected, continue without
  `ContinuationBasis` is rejected, and Brain intent patches cannot carry
  stage mutation keys.

## Acceptance

- Existing graph tests continue to pass.
- A completed simple run has durable loop state and at least one StepRecord.
- Trace can answer which Loop owned the model step, why it ran in slow mode,
  and why the Loop continued or stopped.
