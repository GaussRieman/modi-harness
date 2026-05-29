# Types Reference

This document is the **single authoritative source** for Modi Harness data types. Architecture and implementation documents reference this file rather than redefining types.

When a type appears here and elsewhere, this file wins.

Types are presented as Python `TypedDict` definitions for clarity. Boundary types (Harness API request/response, settings) are implemented as Pydantic models in code; internal records may use TypedDict or dataclass.

## 1. AgentProfile

Output of Agent Loader.

```python
class AgentProfile(TypedDict):
    name: str
    description: str
    instruction: str
    default_tools: list[str]
    default_skills: list[str]
    output_contract: OutputContract | None
    permission_profile: PermissionProfile | None
    safety_constraints: list[str]
    tags: list[str]
    metadata: dict
```

Frontmatter mapping rules:

- Hyphenated frontmatter keys map to underscore Python fields:
  `allowed-tools` → `allowed_tools`, `risk-notes` → `risk_notes`, etc.
- Top-level keys with no explicit field on the type are preserved under `metadata`.
- Both hyphen and underscore spellings are accepted on input; canonical output uses underscore.

## 2. PermissionProfile

```python
class PermissionProfile(TypedDict):
    mode: Literal["ask", "auto", "plan", "bypass"] | None
    preauthorized: list[str]      # tool names allowed without approval in `auto`
    deny: list[str]               # tool names always denied
    review_required: list[str]    # tool names that always go to review
```

## 3. OutputContract

```python
class OutputContract(TypedDict):
    schema: dict | None           # JSON Schema, optional
    required_fields: list[str]
    citation_required: bool
    risk_label_required: bool
    forbidden_patterns: list[str]
    review_required: bool
    free_form: bool               # when true, schema is bypassed and output is plain text
```

Default values when frontmatter omits a field:

| field | default when contract omitted | default when contract declared but field missing |
|---|---|---|
| `schema` | `None` | `None` |
| `required_fields` | `[]` | `[]` |
| `citation_required` | `False` | `False` |
| `risk_label_required` | `False` | `False` |
| `forbidden_patterns` | `[]` | `[]` |
| `review_required` | `False` | `False` |
| `free_form` | `True` | `False` (declaring a contract implies structured) |

So an agent that omits `output_contract` entirely gets a free-form pass-through contract. An agent that declares any `output_contract` block defaults `free_form=False`, and Output Controller enforces declared fields.

## 4. LoadedSkill

Output of Skill Loader.

```python
class LoadedSkill(TypedDict):
    name: str
    description: str
    instruction: str
    allowed_tools: list[str] | None    # None = do not narrow; [] = narrow to nothing
    risk_notes: list[str]
    references: list[SkillAssetRef]
    scripts: list[SkillAssetRef]
    templates: list[SkillAssetRef]
    examples: list[SkillAssetRef]
    tags: list[str]
    metadata: dict
```

`allowed_tools` distinguishes three frontmatter shapes:

| frontmatter | `allowed_tools` value | meaning |
|---|---|---|
| absent | `None` | do not narrow; inherit agent's tools |
| `allowed-tools: []` | `[]` | narrow to nothing; skill cannot call any tool |
| `allowed-tools: [a, b]` | `["a", "b"]` | narrow to listed tools |

```python
class SkillAssetRef(TypedDict):
    kind: Literal["reference", "script", "template", "example"]
    name: str
    path: str
    size_bytes: int
    summary: str | None
```

Allowed-tools algebra: the runtime tool visibility for a model step is

```text
let skill_union = union of skill.allowed_tools for every active skill where
                  skill.allowed_tools is not None
                  (skills with allowed_tools=None are skipped, they do not narrow)

if no active skill narrows:
    visible = agent.default_tools ∩ policy.visible_tools(agent, mode, state)
else:
    visible = agent.default_tools ∩ skill_union ∩ policy.visible_tools(agent, mode, state)
```

A skill cannot expose a tool that the agent did not declare. A skill with `allowed_tools=None` does not narrow. A skill with `allowed_tools=[]` narrows to nothing for itself and is removed from the union (it cannot widen via emptiness).

## 5. ContextPack

Output of Context Manager.

```python
class ContextPack(TypedDict):
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
```

```python
class ContextBlock(TypedDict):
    block_id: str
    source_kind: str
    content: str | None           # None when block is a reference, not inlined
    workspace_ref: str | None
    trust: TrustAnnotation
```

```python
class MemoryBlock(TypedDict):
    record_id: str
    type: Literal["user", "feedback", "project", "reference"]
    scope: Literal["user", "agent", "project", "conversation"]
    body: str
    tags: list[str]
```

### MemoryLevel

```python
MemoryLevel = Literal["minimal", "moderate", "full"]
```

Controls how much memory is injected into the context pack:

| Level | Behavior |
|-------|----------|
| `minimal` | Only conversation-scoped memory; body truncated to first 256 chars |
| `moderate` | Conversation + agent-scoped memory; full body up to 4 KiB limit |
| `full` | All scopes (user, agent, project, conversation); full body; tags included |

Default is `"full"` (V0.2 behavior). Set via `MODI_MEMORY_LEVEL` or per-request `options.memory_level`.

```python
class TrustAnnotation(TypedDict):
    trust_level: Literal["trusted", "untrusted"]
    source_kind: str
    source_id: str
    sanitizer: str | None
```

```python
class Message(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None
    metadata: dict
```

```python
class ToolDescription(TypedDict):
    name: str
    description: str
    input_schema: dict
    risk_level: str
    side_effect: bool
```

`context_hash` is a stable hash over the serialized pack, used by Trace Recorder.

## 6. AgentState

Runtime state held by Runtime Adapter and persisted via Workspace Manager.

```python
class AgentState(TypedDict):
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    thread_id: str | None
    agent_name: str
    permission_mode: Literal["ask", "auto", "plan", "bypass"]
    task: dict
    messages: list[Message]
    loaded_skills: list[str]
    tool_calls: list[ToolCallRecord]
    denied_actions: list[DeniedAction]
    workspace_refs: list[WorkspaceRef]
    pending_approval: PendingApproval | None
    draft_output: dict | None
    final_output: dict | None
    step_count: int
    status: Literal["running", "interrupted", "blocked", "completed", "failed"]
```

```python
class ToolCallRecord(TypedDict):
    tool_call_id: str
    tool_name: str
    arguments: dict
    decision: Literal["allow", "deny", "require_approval", "require_review"]
    result: dict | None
    error: dict | None
    started_at: str
    finished_at: str | None
```

```python
class DeniedAction(TypedDict):
    fingerprint: str              # canonical hash of (tool_name, arguments, target)
    tool_name: str
    arguments: dict
    reason: str
    decided_at: str
```

```python
class PendingApproval(TypedDict):
    approval_id: str
    tool_call_id: str
    decision: Literal["require_approval", "require_review"]
    summary: str
    risk_level: str
    requested_at: str
```

## 7. ToolSpec

Tool registration record.

```python
class ToolSpec(TypedDict):
    name: str
    description: str
    input_schema: dict
    output_schema: dict | None
    risk_level: Literal["L0", "L1", "L2", "L3", "L4"]
    side_effect: bool
    permission_scope: str
    allowed_agents: list[str]
    allowed_skills: list[str]
    timeout_seconds: int
    retry: RetryPolicy | None
    idempotent: bool
    dry_run_supported: bool
    tags: list[str]
```

Default values applied at registration when omitted:

| field | default |
|---|---|
| `output_schema` | `None` |
| `permission_scope` | `""` |
| `allowed_agents` | `[]` (empty = all agents allowed) |
| `allowed_skills` | `[]` (empty = all skills allowed) |
| `timeout_seconds` | `MODI_TOOL_TIMEOUT_DEFAULT` |
| `retry` | `None` |
| `idempotent` | `False` |
| `dry_run_supported` | `False` |
| `tags` | `[]` |

`allowed_agents=[]` and `allowed_skills=[]` mean "no agent-level / skill-level restriction"; restriction is then driven by `agent.default_tools` and `skill.allowed_tools`. Non-empty lists narrow further.

```python
class RetryPolicy(TypedDict):
    max_attempts: int
    backoff_seconds: float
    retry_on: list[str]           # error class names or normalized error codes
```

## 8. PolicyDecision

Output of Policy Gate.

```python
class PolicyContext(TypedDict):
    agent: AgentProfile
    skill: LoadedSkill | None
    tool_spec: ToolSpec | None
    state: AgentState
    requested_action: RequestedAction
    permission_mode: Literal["ask", "auto", "plan", "bypass"]

class RequestedAction(TypedDict):
    kind: Literal["tool_call", "output_finalize", "memory_write"]
    tool_name: str | None
    arguments: dict
    target: dict | None
    fingerprint: str

class PolicyDecision(TypedDict):
    decision: Literal["allow", "deny", "require_approval", "require_review"]
    reason: str
    approval_id: str | None
    review_requirement: dict | None
    denied_retry: bool
    audit: dict
```

`PolicyContext` is the explicit input to `decide`; nothing outside this struct may influence the decision.

## 9. WorkspaceRef

```python
class WorkspaceRef(TypedDict):
    run_id: str
    kind: Literal["input", "state", "reference", "artifact", "draft", "log"]
    path: str
    artifact_id: str | None
    mime_type: str | None
    trust_level: Literal["trusted", "untrusted"]
    size_bytes: int
    metadata: dict
```

## 10. ModelResult

Output of Model Adapter.

```python
class ModelResult(TypedDict):
    message: Message
    tool_calls: list[ToolCallProposal]
    draft_output: dict | None
    usage: ModelUsage
    safety_signals: list[SafetySignal]
    finish_reason: str
    raw: object

class ToolCallProposal(TypedDict):
    tool_call_id: str
    tool_name: str
    arguments: dict
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
```

### ModelAdapter Async Methods (V0.3)

```python
class ModelAdapter:
    def call(self, context: ContextPack) -> ModelResult: ...
    async def acall(self, context: ContextPack) -> ModelResult: ...
    async def astream(self, context: ContextPack) -> AsyncIterator[StreamEvent]: ...
```

- `acall` — async equivalent of `call`; returns a complete `ModelResult`.
- `astream` — yields `StreamEvent` dicts with `event_type="model_delta"` per token, followed by a final `terminal` event containing the full `ModelResult`.

Both async methods require the model adapter to be constructed via `create_chat_model`.

### create_chat_model Factory (V0.3)

```python
def create_chat_model(
    provider: Literal["openai", "anthropic"],
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> ModelAdapter: ...
```

Factory function that returns a configured `ModelAdapter` for the given provider:

| Parameter | Description |
|-----------|-------------|
| `provider` | `"openai"` or `"anthropic"` |
| `name` | Model name (e.g. `"gpt-4o"`, `"claude-sonnet-4-20250514"`) |
| `api_key` | Provider API key; falls back to `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` env |
| `base_url` | Optional override for the provider endpoint |

## 11. OutputValidationResult

Output of Output Controller.

```python
class OutputValidationResult(TypedDict):
    status: Literal["draft", "validated", "needs_review", "final", "rejected"]
    output: dict | None
    issues: list[OutputIssue]
    required_action: dict | None

class OutputIssue(TypedDict):
    code: str                     # machine-readable, stable
    severity: Literal["info", "warn", "error"]
    field: str | None
    message: str
    hint: str | None
```

## 12. TraceEvent

```python
class TraceEvent(TypedDict):
    event_id: str
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    thread_id: str | None
    timestamp: str
    event_type: str
    payload: dict
    payload_ref: str | None       # for large payloads stored in workspace
```

Standard `event_type` values:

```text
run_start, run_end,
state_transition,
context_built,
model_call, model_result,
tool_call, tool_result,
policy_decision,
approval_request, approval_granted, approval_rejected,
denial,
hook_dispatch,
output_validation,
memory_selection, memory_write, memory_delete,
mode_change,
error
```

## 13. MemoryRecord

```python
class MemoryRecord(TypedDict):
    id: str
    scope: Literal["user", "agent", "project", "conversation"]
    type: Literal["user", "feedback", "project", "reference"]
    name: str
    description: str
    body: str
    tags: list[str]
    source_run_id: str | None
    created_at: str
    updated_at: str
    expires_at: str | None
    metadata: dict
```

```python
class MemoryIndex(TypedDict):
    records: list[MemoryRecord]
    by_scope: dict[str, list[str]]    # scope -> list of record ids
    by_type: dict[str, list[str]]
    by_tag: dict[str, list[str]]
```

The index loads metadata for all records in the active scopes; bodies are loaded on demand.

## 14. HookSpec / HookResult

```python
class HookSpec(TypedDict):
    event: str
    matcher: dict | None
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
    redirect: dict | None
    exit_code: int
    duration_ms: int
    stdout_ref: str | None
    stderr_ref: str | None
```

## 15. Harness API Types

```python
class RunTaskRequest(TypedDict):
    agent: str
    input: dict
    options: dict
    permission_mode: Literal["ask", "auto", "plan", "bypass"] | None
    thread_id: str | None
    parent_run_id: str | None

class RunTaskResponse(TypedDict):
    run_id: str
    thread_id: str | None
    status: Literal["completed", "interrupted", "blocked", "failed"]
    output: dict | None
    pending_approval: PendingApproval | None
    error: dict | None

class ThreadInfo(TypedDict):
    thread_id: str
    agent_name: str
    created_at: str
    last_active_at: str
    run_count: int
    status: Literal["open", "closed"]

class StreamEvent(TypedDict):
    event_type: Literal[
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
    run_id: str
    sequence: int
    payload: dict
    terminal_response: RunTaskResponse | None   # only set when event_type == "terminal"
```

## 16. Policy Rule Pack Types

```python
class ActionMatcher(TypedDict):
    kind: Literal["tool_call", "output_finalize", "memory_write"]
    tool_name_pattern: str | None     # glob or exact
    argument_predicate: str | None    # name of registered predicate fn
    risk_floor: Literal["L0", "L1", "L2", "L3", "L4"] | None
    tag_any: list[str]
    elevate_to: Literal["require_approval", "require_review", "deny"]
    audit_label: str
```

A rule pack exposes:

```python
def matchers() -> list[ActionMatcher]: ...
```

Matchers can only **elevate** an action's decision (e.g. `allow` → `require_approval`), never lower it.

## 17. Identifiers and Time

- `run_id`, `root_run_id`, `parent_run_id`, `event_id`, `approval_id`, `tool_call_id`, `record_id` are ULID strings generated by Modi.
- `thread_id` is **caller-supplied** when starting a thread; Modi accepts any string matching `[A-Za-z0-9_-]{1,128}`. If absent at `start_thread`, Modi generates a ULID.
- All timestamps are ISO 8601 UTC with millisecond precision.
- `fingerprint` is a SHA-256 of canonical-JSON-serialized inputs; used for denied-retry checks.

## 18. Settings

Authoritative settings keys live in `00-project-foundation.md` plus the Memory Store and Hook System docs. Settings are loaded once via `pydantic-settings` and passed into modules; no module reads `os.environ` directly.

## Change Rule

Any change to a type in this file is a documented break. Implementation code imports types from `modi_harness.types`; that module is the single source in code, mirroring this file.
