# Types Reference

This page maps the public and cross-module type families. Exact fields and
literals are defined in [`src/modi_harness/types.py`](../../src/modi_harness/types.py),
which is authoritative. Boundary configuration models live under
`src/modi_harness/config/`.

## Core literals

```python
PermissionMode = Literal["auto", "preview", "trust"]
RiskLevel = Literal["L0", "L1", "L2", "L3", "L4"]
MemoryScope = Literal["user", "workspace", "agent", "thread"]
MemoryLevel = Literal["minimal", "moderate", "full"]
ToolKind = Literal["regular", "subagent", "builtin", "protocol"]
```

`auto`, `preview`, and `trust` are the only modes. The legacy 4-mode names
(`ask`, `plan`, `bypass`) were removed in the intent-aligned runtime redesign;
`normalize_mode` now rejects them. A mode is the policy floor that proves an
action is safe — autonomy is shaped by intent clarity, not by the mode.

## 1. AgentProfile

Normalized model-facing Agent data: identity, instruction, visible Tool and
Skill names, output and permission contracts, safety constraints, tags, and
metadata. Markdown loading produces this internal shape from `ModiAgent`.

## 2. PermissionProfile

Agent-local permission defaults: mode, preauthorized Tools, denied Tools,
review-required Tools, allowed subagents, and optional subagent depth limit.

## 3. OutputContract

Output schema and validation requirements: required fields, citation/risk
requirements, forbidden patterns, review requirement, and free-form mode.

## 4. LoadedSkill

A parsed Skill instruction plus allowed-Tool narrowing, risk notes, references,
scripts, templates, examples, tags, and metadata. `SkillAssetRef` describes an
asset without loading its contents into every model turn.

## 5. ContextPack

The complete provider-neutral model input: system, Agent and Skill
instructions, the human intent field (`intent_context`, `intent_clarity`,
`autonomy_scope`, `current_stage`, `active_boundaries`, `judgment_history` —
rendered ahead of memory as first-class authority), Memory, references, state
summary, visible Tools, workspace index, recent messages, output requirement,
trust annotations, and context hash. Related types are `ContextBlock`,
`MemoryBlock`, `Message`, `ToolDescription`, `TrustAnnotation`, and the intent
family in §18. Active boundaries are immutable; memory renders after them and
cannot override them.

`TaskInput` is the open input payload accepted by Session execution methods.
The first user message is derived in this order:

```text
messages > prompt > customer_message > question > goal > str(payload)
```

## 6. AgentState

Checkpointed graph state: run/thread lineage, active Agent, permission mode,
task input, messages, Tool history, denials, workspace references, pending
judgment or interaction, human intent, task plan, output, step count, status,
trace queue, and repair count.

`PendingJudgment` is the current human-in-the-loop contract. It carries the
reviewed action/stage target, allowed judgment kinds, proposed intent patch,
reviewed action hash, and a compatibility `approval_id`. `PendingApproval`
remains only as a transitional bridge for older approval-oriented call sites.
Task and interaction types include `TaskItem`, `TaskPlan`, `PendingInteraction`,
and the retired/transitional `HumanContext`.

## 7. ToolSpec

Provider-facing Tool contract: name, description, JSON Schemas, risk,
side-effect and permission metadata, Agent/Skill restrictions, timeout/retry,
idempotency, dry-run support, tags, kind, and optional subagent target.

`ToolCallProposal`, `ToolCallRecord`, `RetryPolicy`, and `ToolBinding` cover the
proposal, execution record, retry declaration, and Python handler binding.

## 8. PolicyDecision

`PolicyContext` combines Agent, Skill, Tool, state, requested action, and mode.
`PolicyDecision` records allow/deny/approval/review, reason, fingerprint,
retry denial, and audit metadata. `RequestedAction` covers Tool calls, Memory
writes, and output finalization.

In the intent-aligned runtime, policy is the safety proof layer. The primary
fit decision is `AlignmentDecision`; policy requirements such as approval,
review, audit, or dry-run are governance obligations attached beneath that
alignment decision.

## 9. WorkspaceRef

A typed pointer to run-scoped input, state, reference, artifact, draft, or log
data. It carries path, trust, MIME type, size, metadata, and optional artifact
identity.

## 10. ModelResult

Provider-neutral model output: text, normalized Tool proposals, usage, finish
reason, safety signals, raw provider value, and fallback/model metadata.

## 11. OutputValidationResult

Validation status, accepted output, structured issues, and required action.
`OutputIssue` provides code, severity, field, message, and repair hint.

## 12. TraceEvent

Append-only event identity, run/thread lineage, timestamp, event type, inline
payload, and optional payload reference. `StreamEvent` is the smaller
client-facing projection used by CLI and API consumers.

Runtime explainability depends on stable join keys rather than full payload
snapshots. Model/tool/output/run-end events carry stable `step_id` values;
action lineage is represented by the `action_proposed`,
`alignment_decision`, and `intent_lineage_recorded` trio. `run_end` summaries
include model calls, model usage, model latency, fallback use, tool attempts,
tool failures, and tool latency. Golden regression fixtures should compare this
contract surface, not dynamic event IDs or wording.

## 13. MemoryRecord

Typed reusable Memory with scope, type, content, tags, source run, lifecycle
timestamps, and metadata. Retrieval uses `MemoryIndex`, `MemoryCandidate`, and
`SelectedMemory`.

## 14. HookSpec and HookResult

Hook event, matcher, command, timeout, blocking, payload/capture mode, and
failure policy; results normalize decision, feedback, redirect, exit status,
duration, and output references.

## 15. Harness API types

`RunTaskRequest`, `RunTaskResponse`, `ThreadInfo`, and `StreamEvent` are stable
transport shapes around `ModiSession`. Method signatures are defined in
`src/modi_harness/api/session.py`.

## 16. ActionMatcher

Rule-pack matcher for action kind, Tool-name pattern, argument predicate, risk
floor, tags, elevation, and audit label.

## 17. Supporting declarations

- `ToolBinding`: Tool specification, handler, and optional dry-run handler.
- `Skill`: loaded Skill profile plus source path.
- `ModelSpec`: per-Agent provider override.
- `PermissionsConfig`: Harness-level permission defaults.
- `TaskProtocolConfig`: task-plan mode, review behavior, and item bounds.
- `InteractionProtocolConfig`: Agent-driven or prompt-driven startup.

## 18. Intent-aligned runtime (`modi_harness.intent`)

The redesign's new center. These TypedDicts live inside `AgentState`
(`human_intent`, plus `intent_version` / `stage_id` lineage shortcuts) and stay
JSON-serializable for checkpoint/resume. They are authoritative for the human
intent field; `HumanContext` (§6) is transitional and retired as N3/N6 land.

- `HumanIntentContext`: the durable intent field — `version`, `goal`,
  `desired_outcome`, `boundaries`, `non_goals`, `success_criteria`,
  `current_stage`, `responsibility`, `escalation`, `tradeoffs`,
  `confirmed_inputs`, `decisions`, `corrections`. May begin thin; that is valid
  state, not failure.
- `IntentClarity`: model-estimated, deterministically floored — `level`
  (`thin | partial | operational | stable`), `unknowns`, `assumptions`,
  `confidence`. Drives autonomy.
- `IntentBoundary`: a declared edge of the field — `id`, `kind`, `statement`,
  `severity` (`soft | hard`), `escalation` (`continue | ask | deny`).
- `IntentStage`: current phase — `id`, `kind`
  (`clarify | explore | plan | execute | verify | deliver`), `goal`,
  `exit_criteria`, `judgment_required_before_exit`. Sits above `TaskPlan`.
- `HumanJudgment`: the broad human-interaction primitive — `kind`
  (`clarify | approve | reject | revise | redirect | constrain | cancel`),
  optional action/stage targets, `rationale`, and an `intent_updates`
  `IntentPatch`. Approval is one kind, not the whole model.
- `IntentPatch`: optional-key mutation applied to the context by a judgment.
- `ResponsibilityContext`, `EscalationPreference`, `IntentCorrection`:
  supporting records for ownership, escalation posture, and drift corrections.

Initial extraction is deterministic (`intent.extractor.extract_intent`): a goal
from the task input, confirmed inputs, an opening `clarify` (or `explore` when
materials are present) stage, and hard boundaries seeded from the agent's
safety constraints. An explicit caller-supplied partial `HumanIntentContext`
(`input["human_intent"]`) overrides inferred fields.

## 19. Brain-Agent Loop runtime (`modi_harness.loop`)

The Loop runtime promotes lifecycle and semantic progress above raw model turns
and tool calls. These records live inside `AgentState` as `loop_state`,
`step_records`, `current_step`, and `last_continuation_decision`, and remain
JSON-serializable for checkpoint/resume.

- `AgentLoop`: first-class lifecycle controller object for one intent run.
  `prepare_step()` builds `StepContext`, calls `Brain.plan_step()`, validates
  the resulting `StepDecision`, and creates a planned `StepRecord`.
  `complete_step()` completes the record, decides continuation, and advances
  `LoopState`.
- `LoopState`: durable lifecycle state for one intent run — `loop_id`,
  `run_id`, `agent_name`, `status`, `intent_version`, `stage_id`,
  `step_index`, `max_auto_steps`, `continuation`, `last_event_id`, and
  `pending_step_id`.
- `StepDecision`: Brain's structured next-step decision — `step_kind`,
  `reasoning_mode`, `reason`, optional `BrainIntentPatch`, optional
  `RuntimeOperationProposal`, `HumanJudgmentAssessment`, and
  `ContinuationBasis`.
- `StepContext`: compact Brain planning input — current loop, input event,
  intent, clarity, autonomy scope, active stage, agent state, recent steps,
  available capabilities, and optional brain spec.
- `StepRecord`: durable semantic progress record with loop/run ids, step index,
  active intent version/stage, decision, operation refs, state delta, postcheck
  result, and timestamps.
- `RuntimeOperationProposal`: Step-level consequential operation above the
  current `ActionProposal` path — `tool`, `output_finalize`,
  `stage_transition`, or `memory_write`.
  `stage_transition` operations are adapted to the existing `transition_stage`
  builtin and flow through the normal Harness alignment/governance/action path.
  `memory_write` operations adapt to `save_memory` for thread/agent scope or
  `propose_memory` for user/workspace scope. `tool` operations adapt to their
  explicit target tool. `output_finalize` operations set `pending_draft` and
  route into the existing output validation path instead of pretending to be a
  tool call. Unknown or unwired operation targets are recorded as a failed Step
  with a `runtime_operation_not_wired` trace error.
- `HumanJudgmentAssessment`: explicit Brain judgment of whether human input is
  required before the step may proceed. If `required` is true, the step cannot
  carry a runtime operation.
- `ContinuationBasis` and `LoopContinuationDecision`: Brain's semantic basis
  for continuing and the Loop's final continue/wait/finish/fail verdict.

The default implementation is `modi_harness.brain.default_brain()`: a
constrained `RuleBrain` first tries narrow fast rules, then falls back to
`SlowModelBrain`, which preserves the existing `model_turn` behavior as a slow
planning step. The implemented fast rules are intentionally not a workflow DSL:

- explicit `brain.fast_rules.required_inputs` in `clarify`: if a declared
  required input is absent from `confirmed_inputs`, emit a fast `clarify` step
  with an `ask`, create `pending_interaction`, and interrupt before any model
  call;
- explicit `brain.fast_rules.stage_exit_transitions` plus an event flag
  `stage_exit_criteria_satisfied == true`: emit one `stage_transition`
  runtime operation through the existing alignment/governance/action path;
- explicit event `hard_boundary_triggered`: emit a fast `handoff` step with
  `human_judgment.required == true`, create `pending_judgment`, and wait.

General clarity unknowns, implicit stage readiness, and fuzzy boundary guesses
fall through to slow mode. `AgentLoop` validates the decision and owns the
record/continuation/state-change boundary; the graph records `step_planned`,
`runtime_operation_staged` when applicable, `step_completed`, and
`loop_continuation_decision` trace events around that boundary. The full Agent
package split remains a future layer above this contract.

## 20. Stabilized Internal Contract Set

R6 stabilizes internal contracts for sustained development, not a public 1.0
compatibility promise. The current contract set is:

- `HumanIntentContext`: durable human intent field, current stage, judgments,
  corrections, and confirmed inputs.
- `LoopState`, `StepDecision`, `StepRecord`, and `LoopContinuationDecision`:
  durable lifecycle, semantic progress, Brain decision, and Loop continuation
  contracts.
- `ActionProposal` and `ActionImpact`: normalized action plus deterministic
  impact before alignment/governance.
- `AlignmentDecision`: primary fit verdict with action, intent version, stage,
  boundary hits, governance requirements, and `model_judged`.
- `PendingJudgment`: judgment-first pause contract with compatibility
  `approval_id` and reviewed action hash.
- `IntentLineage`: compact join across action, alignment decision, intent
  version/stage, optional judgment, and boundary hits.
- Run summary trace payloads: `run_end` model/tool usage, latency, fallback,
  attempt, and failure totals.

Protocol version candidates are the persisted or cross-process shapes most
likely to need explicit versioning before public stabilization: `TraceEvent`
payload contracts, `ToolSpec`, `HumanIntentContext`, `PendingJudgment`,
`LoopState`, `StepDecision`, `StepRecord`, `IntentLineage`, `MemoryRecord`, and
`RunTaskResponse`.
