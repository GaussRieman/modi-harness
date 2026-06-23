# Types Reference

This page maps the public and cross-module type families. Exact fields and
literals are defined in [`src/modi_harness/types.py`](../../src/modi_harness/types.py),
which is authoritative. Boundary configuration models live under
`src/modi_harness/config/`.

## Core literals

```python
PermissionMode = Literal["auto", "preview", "trust", "ask", "plan", "bypass"]
RiskLevel = Literal["L0", "L1", "L2", "L3", "L4"]
MemoryScope = Literal["user", "workspace", "agent", "thread"]
MemoryLevel = Literal["minimal", "moderate", "full"]
ToolKind = Literal["regular", "subagent", "builtin", "protocol"]
```

`ask`, `plan`, and `bypass` are deprecated input aliases. Runtime mode
normalization produces `auto`, `preview`, or `trust`.

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
approval or interaction, human context, task plan, output, step count, status,
trace queue, and repair count.

Task and interaction types include `TaskItem`, `TaskPlan`, `PendingInteraction`,
`HumanContext`, and `PendingApproval`.

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
