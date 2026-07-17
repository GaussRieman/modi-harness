"""Authoritative type contracts for Modi Harness.

``docs/reference/types.md`` maps these type families for maintainers. This
module is authoritative for exact fields and literals.

Types here are TypedDict for internal records. Boundary types (API
request/response, settings) use Pydantic models in their own modules
(``modi_harness.config``, future ``modi_harness.api.types``).
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping  # noqa: F401  (Mapping used in V0.5 N0.2)
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType  # noqa: F401  (used in V0.5 N0.2 ModiAgent.metadata)
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from modi_harness.loop.types import (
    LoopContinuationDecision,
    LoopState,
    StepRecord,
)
from modi_harness.workflow import Workflow

# ---------------------------------------------------------------------------
# 1. AgentProfile
# ---------------------------------------------------------------------------


class AgentProfile(TypedDict):
    """Output of Agent Loader. See docs/reference/types.md#1-agentprofile."""

    name: str
    description: str
    instruction: str
    default_tools: list[str]
    default_skills: list[str]
    output_contract: OutputContract | None
    permission_profile: PermissionProfile | None
    safety_constraints: list[str]
    tags: list[str]
    workflows: list[Workflow]
    metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# 2. PermissionProfile
# ---------------------------------------------------------------------------


PermissionMode = Literal["auto", "preview", "trust"]

MemoryLevel = Literal["minimal", "moderate", "full"]


class PermissionProfile(TypedDict):
    """See docs/reference/types.md#2-permissionprofile."""

    mode: PermissionMode | None
    preauthorized: list[str]
    deny: list[str]
    review_required: list[str]


# ---------------------------------------------------------------------------
# 3. OutputContract
# ---------------------------------------------------------------------------


class OutputContract(TypedDict):
    """See docs/reference/types.md#3-outputcontract."""

    schema: dict[str, Any] | None
    required_fields: list[str]
    citation_required: bool
    risk_label_required: bool
    forbidden_patterns: list[str]
    review_required: bool
    free_form: bool


# ---------------------------------------------------------------------------
# 4. LoadedSkill
# ---------------------------------------------------------------------------


class SkillAssetRef(TypedDict):
    kind: Literal["reference", "script", "template", "example"]
    name: str
    path: str
    size_bytes: int
    summary: str | None


class LoadedSkill(TypedDict):
    """See docs/reference/types.md#4-loadedskill.

    ``allowed_tools`` is tri-state:
      - ``None``        — do not narrow; inherit agent's tools.
      - ``[]``          — narrow to nothing.
      - ``["a", ...]``  — narrow to listed tools.
    """

    name: str
    description: str
    instruction: str
    allowed_tools: list[str] | None
    risk_notes: list[str]
    references: list[SkillAssetRef]
    scripts: list[SkillAssetRef]
    templates: list[SkillAssetRef]
    examples: list[SkillAssetRef]
    tags: list[str]
    metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# 5. ContextPack
# ---------------------------------------------------------------------------


class TrustAnnotation(TypedDict):
    trust_level: Literal["trusted", "untrusted"]
    source_kind: str
    source_id: str
    sanitizer: str | None


class ContextBlock(TypedDict):
    block_id: str
    source_kind: str
    content: str | None
    workspace_ref: str | None
    trust: TrustAnnotation


class MemoryBlock(TypedDict):
    record_id: str
    type: Literal["user", "feedback", "project", "reference"]
    scope: Literal["user", "workspace", "agent", "thread"]
    body: str
    tags: list[str]
    authority: Literal["trusted", "context"]
    score: float
    reasons: list[str]


class Message(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None
    metadata: dict[str, Any]


class TaskInput(TypedDict, total=False):
    """Input payload for ModiSession.run_task / stream / astream.

    All keys are optional and the dict may carry additional keys the agent
    expects. The harness derives the agent's first user message from these
    keys in priority order: messages (last user item) > prompt >
    customer_message > question > goal, falling back to str(payload).
    ``tags`` and ``reference_keys``
    additionally steer memory selection. See
    docs/architecture/execution-runtime.md for the authoritative precedence.
    """

    messages: list[Message]
    prompt: str
    customer_message: str
    question: str
    goal: str
    tags: list[str]
    reference_keys: list[str]


class ToolDescription(TypedDict):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: str
    side_effect: bool


class ContextPack(TypedDict):
    """See docs/reference/types.md#5-contextpack.

    Intent-aligned runtime: ``intent_context`` and friends carry the human
    intent field as first-class authority. The Model Adapter renders them ahead
    of memory so the model sees *what the human wants* and *how much freedom it
    has* before any reusable historical context. Memory is reusable context, not
    active authority, and cannot override the active boundaries.
    """

    system_instruction: str
    agent_instruction: str
    skill_instructions: list[str]
    memory_blocks: list[MemoryBlock]
    references: list[ContextBlock]
    state_summary: str
    tool_descriptions: list[ToolDescription]
    workspace_index: list[WorkspaceRef]
    recent_messages: list[Message]
    output_requirement: OutputContract | None
    trust_annotations: list[TrustAnnotation]
    context_hash: str


# ---------------------------------------------------------------------------
# 6. AgentState
# ---------------------------------------------------------------------------


class ToolCallRecord(TypedDict):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    decision: Literal["allow", "deny", "require_approval", "require_review"]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    started_at: str
    finished_at: str | None


class DeniedAction(TypedDict):
    fingerprint: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    decided_at: str


class PendingApproval(TypedDict):
    approval_id: str
    tool_call_id: str
    decision: Literal["require_approval", "require_review"]
    summary: str
    risk_level: str
    requested_at: str


class PendingJudgment(TypedDict):
    """A point where the runtime needs human judgment, not just approval.

    Approval is one ``allowed_kind``; the human may equally revise the goal,
    add a boundary, redirect the stage, or reject. ``proposed_intent_patch``
    is the runtime's suggested edit (e.g. a boundary it inferred) that the
    human can accept or override. ``approval_id`` mirrors ``judgment_id`` so
    the transitional approval bridge keeps working.
    """

    judgment_id: str
    approval_id: str
    tool_call_id: str | None
    target_action_id: str | None
    reviewed_action_hash: str | None
    prompt: str
    allowed_kinds: list[str]
    proposed_intent_patch: dict[str, Any] | None
    summary: str
    rationale: str | None
    risk_level: str
    trigger: str | None
    requested_at: str


TaskStatus = Literal["pending", "in_progress", "completed", "blocked"]
TaskProtocolMode = Literal["off", "optional", "required"]
TaskPlanReview = Literal["never", "before_execution"]
InteractionStartup = Literal["prompt", "agent"]


class TaskItem(TypedDict):
    id: str
    title: str
    status: TaskStatus
    summary: str | None


class TaskPlan(TypedDict):
    version: int
    items: list[TaskItem]
    current_task_id: str | None
    current_action: str | None
    last_activity: str | None


class PendingInteraction(TypedDict):
    interaction_id: str
    kind: Literal["node_review", "plan_review", "user_input"]
    prompt: str
    payload: dict[str, Any]
    tool_call_id: str | None


class HumanContext(TypedDict):
    """Durable user inputs and decisions collected through HITL interactions."""

    version: int
    inputs: dict[str, Any]
    decisions: list[dict[str, Any]]
    feedback: list[dict[str, Any]]


class AgentState(TypedDict):
    """See docs/reference/types.md#6-agentstate.

    Append-only list fields use ``operator.add`` reducers so LangGraph merges
    concurrent partial-state updates by concatenation. ``pending_trace_events``
    is the queue drained by the trace middleware; ``repair_used`` and
    ``parent_thread_id`` were added in V0.2 for repair budgeting and Subagent
    Runtime, respectively.
    """

    run_id: str
    root_run_id: str
    parent_run_id: str | None
    parent_thread_id: str | None
    thread_id: str | None
    agent_name: str
    permission_mode: PermissionMode
    approved_action_hash: NotRequired[str | None]
    task: dict[str, Any]
    messages: Annotated[list[Message], operator.add]
    loaded_skills: list[str]
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    denied_actions: Annotated[list[DeniedAction], operator.add]
    workspace_refs: Annotated[list[WorkspaceRef], operator.add]
    pending_approval: PendingApproval | None
    pending_judgment: NotRequired[PendingJudgment | None]
    task_plan: NotRequired[TaskPlan | None]
    pending_task_plan: NotRequired[TaskPlan | None]
    pending_interaction: NotRequired[PendingInteraction | None]
    human_context: NotRequired[HumanContext]
    draft_output: dict[str, Any] | None
    final_output: dict[str, Any] | None
    step_count: int
    status: Literal["running", "interrupted", "blocked", "completed", "failed", "cancelled"]
    pending_trace_events: Annotated[list[TraceEvent], operator.add]
    # Brain-Agent Loop runtime: durable lifecycle and semantic progress records.
    loop_state: NotRequired[LoopState]
    step_records: Annotated[list[StepRecord], operator.add]
    current_step: NotRequired[StepRecord | None]
    last_continuation_decision: NotRequired[LoopContinuationDecision | None]
    repair_used: int


# ---------------------------------------------------------------------------
# 7. ToolSpec
# ---------------------------------------------------------------------------


RiskLevel = Literal["L0", "L1", "L2", "L3", "L4"]


class RetryPolicy(TypedDict):
    max_attempts: int
    backoff_seconds: float
    retry_on: list[str]


ToolKind = Literal["regular", "builtin", "protocol"]


class ToolSpec(TypedDict):
    """See docs/reference/types.md#7-toolspec.

    Defaults table in the doc; ``ToolGateway.register_tool`` applies them.
    ``kind`` discriminates regular tools, kernel builtins and protocols.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    risk_level: RiskLevel
    side_effect: bool
    permission_scope: str
    allowed_agents: list[str]
    allowed_skills: list[str]
    timeout_seconds: int
    retry: RetryPolicy | None
    idempotent: bool
    dry_run_supported: bool
    tags: list[str]
    kind: ToolKind
    max_calls_per_node: NotRequired[int]
    max_calls_per_task: NotRequired[int]
    fresh_output_prerequisite: NotRequired[dict[str, Any]]


# ---------------------------------------------------------------------------
# 8. PolicyDecision
# ---------------------------------------------------------------------------


class RequestedAction(TypedDict):
    kind: Literal["tool_call", "memory_write"]
    tool_name: str | None
    arguments: dict[str, Any]
    target: dict[str, Any] | None
    fingerprint: str


class PolicyContext(TypedDict, total=False):
    agent: AgentProfile
    skill: LoadedSkill | None
    tool_spec: ToolSpec | None
    state: AgentState
    requested_action: RequestedAction
    permission_mode: PermissionMode
    # When mode == 'auto' and a require_human outcome arises, the gate
    # uses this flag to choose between require_approval (interactive) and
    # deny (non-interactive). Defaults to True. Callers compute it from
    # TTY detection + MODI_INTERACTIVE env override.
    interactive: bool


class PolicyDecision(TypedDict):
    """See docs/reference/types.md#8-policydecision."""

    decision: Literal["allow", "deny", "require_approval", "require_review"]
    reason: str
    approval_id: str | None
    review_requirement: dict[str, Any] | None
    denied_retry: bool
    audit: dict[str, Any]


# ---------------------------------------------------------------------------
# 9. WorkspaceRef
# ---------------------------------------------------------------------------


WorkspaceKind = Literal["input", "state", "reference", "artifact", "draft", "log"]


class WorkspaceRef(TypedDict):
    """See docs/reference/types.md#9-workspaceref."""

    run_id: str
    kind: WorkspaceKind
    path: str
    artifact_id: str | None
    mime_type: str | None
    trust_level: Literal["trusted", "untrusted"]
    size_bytes: int
    metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# 10. ModelResult
# ---------------------------------------------------------------------------


class ToolCallProposal(TypedDict):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    malformed: bool
    parse_error: str | None
    metadata: NotRequired[dict[str, Any]]


class ModelUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None


class SafetySignal(TypedDict):
    kind: str
    detail: str


class ModelResult(TypedDict):
    """See docs/reference/types.md#10-modelresult."""

    message: Message
    tool_calls: list[ToolCallProposal]
    draft_output: dict[str, Any] | None
    usage: ModelUsage
    model_info: dict[str, Any]
    safety_signals: list[SafetySignal]
    finish_reason: str
    fallback_used: bool
    raw: Any


# ---------------------------------------------------------------------------
# 11. OutputValidationResult
# ---------------------------------------------------------------------------


OutputStatus = Literal["draft", "validated", "needs_review", "final", "rejected"]


class OutputIssue(TypedDict):
    code: str
    severity: Literal["info", "warn", "error"]
    field: str | None
    message: str
    hint: str | None


class OutputValidationResult(TypedDict):
    """See docs/reference/types.md#11-outputvalidationresult."""

    status: OutputStatus
    output: dict[str, Any] | None
    issues: list[OutputIssue]
    required_action: dict[str, Any] | None


# ---------------------------------------------------------------------------
# 12. TraceEvent
# ---------------------------------------------------------------------------


# Standard event_type values are listed in types-reference §12. Implementations
# should treat unknown values as forward-compatible additions.
TRACE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run_start",
        "run_end",
        "state_transition",
        "context_built",
        "model_call",
        "model_result",
        "tool_call",
        "tool_result",
        "policy_decision",
        "approval_request",
        "approval_granted",
        "approval_rejected",
        "interaction_requested",
        "interaction_resolved",
        "task_plan_created",
        "task_plan_revised",
        "task_started",
        "task_resumed",
        "task_completed",
        "task_blocked",
        "task_transition_rejected",
        "finalization_started",
        "output_repair_started",
        "denial",
        "hook_dispatch",
        "output_validation",
        "output_submitted",
        "memory_recall_candidates",
        "memory_admission",
        "memory_selection",
        "memory_write_proposed",
        "memory_write",
        "memory_update",
        "memory_delete",
        "memory_consolidated",
        "mode_change",
        "intent_initialized",
        "intent_updated",
        "intent_clarity_estimated",
        "autonomy_scope_derived",
        "loop_initialized",
        "step_planned",
        "step_completed",
        "loop_continuation_decision",
        "action_proposed",
        "alignment_decision",
        "judgment_requested",
        "judgment_resolved",
        "intent_lineage_recorded",
        "error",
    }
)


class TraceEvent(TypedDict):
    """See docs/reference/types.md#12-traceevent."""

    event_id: str
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    thread_id: str | None
    timestamp: str
    event_type: str
    payload: dict[str, Any]
    payload_ref: str | None


# ---------------------------------------------------------------------------
# 13. MemoryRecord / MemoryIndex
# ---------------------------------------------------------------------------


MemoryScope = Literal["user", "workspace", "agent", "thread"]
MemoryType = Literal["user", "feedback", "project", "reference"]


class MemoryRecord(TypedDict):
    """See docs/reference/types.md#13-memoryrecord."""

    id: str
    scope: MemoryScope
    type: MemoryType
    name: str
    description: str
    body: str
    tags: list[str]
    source_run_id: str | None
    created_at: str
    updated_at: str
    expires_at: str | None
    metadata: dict[str, Any]


class MemoryIndex(TypedDict):
    records: list[MemoryRecord]
    by_scope: dict[str, list[str]]
    by_type: dict[str, list[str]]
    by_tag: dict[str, list[str]]


class MemoryCandidate(TypedDict):
    record: MemoryRecord
    score: float
    reasons: list[str]
    signals: dict[str, float]


class SelectedMemory(TypedDict):
    record: MemoryRecord
    authority: Literal["trusted", "context"]
    score: float
    reasons: list[str]


# ---------------------------------------------------------------------------
# 14. HookSpec / HookResult
# ---------------------------------------------------------------------------


class HookSpec(TypedDict):
    event: str
    matcher: dict[str, Any] | None
    command: str
    timeout_seconds: int
    blocking: bool
    pass_payload: Literal["env", "stdin", "argv"]
    capture: Literal["stdout", "stderr", "none"]
    on_failure: Literal["block", "warn", "ignore"]


class HookResult(TypedDict):
    event: str
    hook_id: str
    decision: Literal["proceed", "block", "redirect"]
    feedback: str | None
    redirect: dict[str, Any] | None
    exit_code: int
    duration_ms: int
    stdout_ref: str | None
    stderr_ref: str | None


# ---------------------------------------------------------------------------
# 15. Harness API Types
# ---------------------------------------------------------------------------


class RunTaskRequest(TypedDict):
    agent: str
    input: dict[str, Any]
    options: dict[str, Any]
    permission_mode: PermissionMode | None
    thread_id: str | None
    parent_run_id: str | None


class RunTaskResponse(TypedDict):
    run_id: str
    thread_id: str | None
    status: Literal["completed", "interrupted", "blocked", "failed", "cancelled"]
    output: dict[str, Any] | None
    pending_approval: PendingApproval | None
    pending_judgment: NotRequired[PendingJudgment | None]
    pending_interaction: NotRequired[PendingInteraction | None]
    error: dict[str, Any] | None


class ThreadInfo(TypedDict):
    thread_id: str
    agent_name: str
    created_at: str
    last_active_at: str
    run_count: int
    status: Literal["open", "closed"]


StreamEventType = Literal[
    "workflow_started",
    "node_started",
    "node_completed",
    "operation_started",
    "operation_completed",
    "step_completed",
    "completion_accepted",
    "completion_rejected",
    "model_delta",
    "tool_call_proposal",
    "tool_call_started",
    "tool_call_result",
    "policy_decision",
    "approval_request",
    "interaction_requested",
    "interaction_resolved",
    "task_plan_created",
    "task_plan_revised",
    "task_started",
    "task_resumed",
    "task_completed",
    "task_blocked",
    "finalization_started",
    "output_repair_started",
    "hook_dispatch",
    "output_validation",
    "terminal",
]


class StreamEvent(TypedDict):
    event_type: StreamEventType
    run_id: str
    sequence: int
    payload: dict[str, Any]
    terminal_response: RunTaskResponse | None


# ---------------------------------------------------------------------------
# 16. Policy Rule Pack Types
# ---------------------------------------------------------------------------


class ActionMatcher(TypedDict):
    kind: Literal["tool_call", "memory_write"]
    tool_name_pattern: str | None
    argument_predicate: str | None
    risk_floor: RiskLevel | None
    tag_any: list[str]
    elevate_to: Literal["require_approval", "require_review", "deny"]
    audit_label: str


# ---------------------------------------------------------------------------
# 17. V0.5 Supporting Dataclasses (ToolBinding / Skill / ModelSpec / PermissionsConfig)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=True)
class ToolBinding:
    """Pairs a JSON-schema tool spec with its handler (and optional dry-run).

    Use `ToolBinding.from_tuple(...)` to accept the legacy ``(spec, handler)``
    tuple form. Note: ``spec`` is a dict, so ``__hash__`` is None — compare
    with ``==`` only.
    """

    spec: dict[str, Any]
    handler: Callable[..., Any]
    dry_run: Callable[..., Any] | None = None

    @classmethod
    def from_tuple(
        cls, item: ToolBinding | tuple[dict[str, Any], Callable[..., Any]]
    ) -> ToolBinding:
        if isinstance(item, ToolBinding):
            return item
        spec, handler = item
        return cls(spec=spec, handler=handler)


@dataclass(frozen=True, eq=True)
class Skill:
    """Lightweight wrapper around a LoadedSkill-equivalent profile."""

    name: str
    profile: LoadedSkill
    source_path: Path | None = None


@dataclass(frozen=True, eq=True)
class ModelSpec:
    """Per-agent model override declaration. String fields env-expanded by harness."""

    provider: str
    name: str
    api_key: str | None = None
    base_url: str | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True, eq=True)
class PermissionsConfig:
    """Harness-level permission defaults; per-agent overrides go on PermissionProfile."""

    mode: PermissionMode | None = None
    preauthorized: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    review_required: tuple[str, ...] = ()


@dataclass(frozen=True, eq=True)
class TaskProtocolConfig:
    """Opt-in native task protocol settings for one Agent."""

    mode: TaskProtocolMode = "off"
    review: TaskPlanReview = "never"
    min_items: int = 1
    max_items: int = 8


@dataclass(frozen=True, eq=True)
class InteractionProtocolConfig:
    """Opt-in Agent-driven interactive startup settings."""

    startup: InteractionStartup = "prompt"


__all__ = [
    "TRACE_EVENT_TYPES",
    "ActionMatcher",
    "AgentProfile",
    "AgentState",
    "ContextBlock",
    "ContextPack",
    "DeniedAction",
    "HookResult",
    "HookSpec",
    "HumanContext",
    "InteractionProtocolConfig",
    "InteractionStartup",
    "LoadedSkill",
    "MemoryBlock",
    "MemoryIndex",
    "MemoryLevel",
    "MemoryRecord",
    "MemoryScope",
    "MemoryType",
    "Message",
    "ModelResult",
    "ModelSpec",
    "ModelUsage",
    "OutputContract",
    "OutputIssue",
    "OutputStatus",
    "OutputValidationResult",
    "PendingApproval",
    "PendingInteraction",
    "PendingJudgment",
    "PermissionMode",
    "PermissionProfile",
    "PermissionsConfig",
    "PolicyContext",
    "PolicyDecision",
    "RequestedAction",
    "RetryPolicy",
    "RiskLevel",
    "RunTaskRequest",
    "RunTaskResponse",
    "SafetySignal",
    "Skill",
    "SkillAssetRef",
    "StreamEvent",
    "StreamEventType",
    "TaskInput",
    "TaskItem",
    "TaskPlan",
    "TaskPlanReview",
    "TaskProtocolConfig",
    "TaskProtocolMode",
    "TaskStatus",
    "ThreadInfo",
    "ToolBinding",
    "ToolCallProposal",
    "ToolCallRecord",
    "ToolDescription",
    "ToolKind",
    "ToolSpec",
    "TraceEvent",
    "TrustAnnotation",
    "WorkspaceKind",
    "WorkspaceRef",
]
