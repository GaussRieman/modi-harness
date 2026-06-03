"""Authoritative type contracts for Modi Harness.

Mirrors ``docs/types-reference.md`` section-for-section. When this file and
``types-reference.md`` disagree, the doc wins; update this file to match.

Types here are TypedDict for internal records. Boundary types (API
request/response, settings) use Pydantic models in their own modules
(``modi_harness.config``, future ``modi_harness.api.types``).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

# ---------------------------------------------------------------------------
# 1. AgentProfile
# ---------------------------------------------------------------------------


class AgentProfile(TypedDict):
    """Output of Agent Loader. See docs/types-reference.md#1-agentprofile."""

    name: str
    description: str
    instruction: str
    default_tools: list[str]
    default_skills: list[str]
    output_contract: "OutputContract | None"
    permission_profile: "PermissionProfile | None"
    safety_constraints: list[str]
    tags: list[str]
    metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# 2. PermissionProfile
# ---------------------------------------------------------------------------


PermissionMode = Literal["ask", "auto", "plan", "bypass", "preview", "trust"]

MemoryLevel = Literal["minimal", "moderate", "full"]


class PermissionProfile(TypedDict):
    """See docs/types-reference.md#2-permissionprofile.

    ``allowed_subagents`` opts the agent into Subagent Runtime:
      - absent / ``[]``       — cannot dispatch any subagent (safe default).
      - ``["*"]``             — any registered agent.
      - ``["a", "b", ...]``   — only the named agents.
    ``subagent_max_depth`` overrides ``MODI_SUBAGENT_MAX_DEPTH`` when set.
    """

    mode: PermissionMode | None
    preauthorized: list[str]
    deny: list[str]
    review_required: list[str]
    allowed_subagents: list[str]
    subagent_max_depth: int | None


# ---------------------------------------------------------------------------
# 3. OutputContract
# ---------------------------------------------------------------------------


class OutputContract(TypedDict):
    """See docs/types-reference.md#3-outputcontract."""

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
    """See docs/types-reference.md#4-loadedskill.

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
    scope: Literal["user", "agent", "project", "conversation"]
    body: str
    tags: list[str]


class Message(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None
    metadata: dict[str, Any]


class ToolDescription(TypedDict):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: str
    side_effect: bool


class ContextPack(TypedDict):
    """See docs/types-reference.md#5-contextpack."""

    system_instruction: str
    agent_instruction: str
    skill_instructions: list[str]
    memory_blocks: list[MemoryBlock]
    references: list[ContextBlock]
    state_summary: str
    tool_descriptions: list[ToolDescription]
    workspace_index: list["WorkspaceRef"]
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


class AgentState(TypedDict):
    """See docs/types-reference.md#6-agentstate.

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
    task: dict[str, Any]
    messages: Annotated[list[Message], operator.add]
    loaded_skills: list[str]
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    denied_actions: Annotated[list[DeniedAction], operator.add]
    workspace_refs: Annotated[list["WorkspaceRef"], operator.add]
    pending_approval: PendingApproval | None
    draft_output: dict[str, Any] | None
    final_output: dict[str, Any] | None
    step_count: int
    status: Literal["running", "interrupted", "blocked", "completed", "failed"]
    pending_trace_events: Annotated[list["TraceEvent"], operator.add]
    repair_used: int


# ---------------------------------------------------------------------------
# 7. ToolSpec
# ---------------------------------------------------------------------------


RiskLevel = Literal["L0", "L1", "L2", "L3", "L4"]


class RetryPolicy(TypedDict):
    max_attempts: int
    backoff_seconds: float
    retry_on: list[str]


ToolKind = Literal["regular", "subagent", "builtin", "protocol"]


class ToolSpec(TypedDict):
    """See docs/types-reference.md#7-toolspec.

    Defaults table in the doc; ``ToolGateway.register_tool`` applies them.
    ``kind`` discriminates between ordinary tools and subagent dispatch
    handles; when ``kind == "subagent"`` the gateway delegates to the
    Subagent Runtime instead of the registered handler.
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
    subagent_target: str | None


# ---------------------------------------------------------------------------
# 8. PolicyDecision
# ---------------------------------------------------------------------------


class RequestedAction(TypedDict):
    kind: Literal["tool_call", "output_finalize", "memory_write"]
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
    """See docs/types-reference.md#8-policydecision."""

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
    """See docs/types-reference.md#9-workspaceref."""

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
    """See docs/types-reference.md#10-modelresult."""

    message: Message
    tool_calls: list[ToolCallProposal]
    draft_output: dict[str, Any] | None
    usage: ModelUsage
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
    """See docs/types-reference.md#11-outputvalidationresult."""

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
        "denial",
        "hook_dispatch",
        "output_validation",
        "memory_selection",
        "memory_write",
        "memory_delete",
        "mode_change",
        "error",
    }
)


class TraceEvent(TypedDict):
    """See docs/types-reference.md#12-traceevent."""

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


MemoryScope = Literal["user", "agent", "project", "conversation"]
MemoryType = Literal["user", "feedback", "project", "reference"]


class MemoryRecord(TypedDict):
    """See docs/types-reference.md#13-memoryrecord."""

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
    status: Literal["completed", "interrupted", "blocked", "failed"]
    output: dict[str, Any] | None
    pending_approval: PendingApproval | None
    error: dict[str, Any] | None


class ThreadInfo(TypedDict):
    thread_id: str
    agent_name: str
    created_at: str
    last_active_at: str
    run_count: int
    status: Literal["open", "closed"]


StreamEventType = Literal[
    "model_delta",
    "tool_call_proposal",
    "tool_call_started",
    "tool_call_result",
    "policy_decision",
    "approval_request",
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
    kind: Literal["tool_call", "output_finalize", "memory_write"]
    tool_name_pattern: str | None
    argument_predicate: str | None
    risk_floor: RiskLevel | None
    tag_any: list[str]
    elevate_to: Literal["require_approval", "require_review", "deny"]
    audit_label: str


__all__ = [
    "ActionMatcher",
    "AgentProfile",
    "AgentState",
    "ContextBlock",
    "ContextPack",
    "DeniedAction",
    "HookResult",
    "HookSpec",
    "LoadedSkill",
    "MemoryBlock",
    "MemoryIndex",
    "MemoryLevel",
    "MemoryRecord",
    "MemoryScope",
    "MemoryType",
    "Message",
    "ModelResult",
    "ModelUsage",
    "OutputContract",
    "OutputIssue",
    "OutputStatus",
    "OutputValidationResult",
    "PendingApproval",
    "PermissionMode",
    "PermissionProfile",
    "PolicyContext",
    "PolicyDecision",
    "RequestedAction",
    "RetryPolicy",
    "RiskLevel",
    "RunTaskRequest",
    "RunTaskResponse",
    "SafetySignal",
    "SkillAssetRef",
    "StreamEvent",
    "StreamEventType",
    "TRACE_EVENT_TYPES",
    "ThreadInfo",
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
