"""Tests for types.py — round-trip, defaults, fingerprint stability."""

from __future__ import annotations

from modi_harness.types import (
    AgentProfile,
    AgentState,
    DeniedAction,
    LoadedSkill,
    MemoryRecord,
    Message,
    OutputContract,
    PendingApproval,
    PermissionProfile,
    PolicyContext,
    PolicyDecision,
    RequestedAction,
    RunTaskRequest,
    RunTaskResponse,
    SkillAssetRef,
    StreamEvent,
    TaskInput,
    TaskProtocolConfig,
    ThreadInfo,
    ToolCallProposal,
    ToolCallRecord,
    ToolSpec,
    TraceEvent,
    TrustAnnotation,
    WorkspaceRef,
)


def test_agent_profile_round_trip() -> None:
    data = {
        "name": "x",
        "description": "y",
        "instruction": "do stuff",
        "default_tools": ["a"],
        "default_skills": [],
        "output_contract": None,
        "permission_profile": None,
        "safety_constraints": [],
        "tags": [],
        "metadata": {},
    }
    # TypedDict round-trip: a dict that satisfies the schema is the value.
    p: AgentProfile = data  # type: ignore[assignment]
    assert p["name"] == "x"


def test_output_contract_free_form_default() -> None:
    oc = OutputContract(
        schema=None,
        required_fields=[],
        citation_required=False,
        risk_label_required=False,
        forbidden_patterns=[],
        review_required=False,
        free_form=True,
    )
    assert oc["free_form"] is True


def test_task_protocol_config_defaults_off() -> None:
    config = TaskProtocolConfig()
    assert config.mode == "off"
    assert config.review == "never"
    assert config.min_items == 1
    assert config.max_items == 8


def test_loaded_skill_allowed_tools_tri_state() -> None:
    # None: do not narrow
    s_none: LoadedSkill = LoadedSkill(  # type: ignore[typeddict-item]
        name="s",
        description="d",
        instruction="",
        allowed_tools=None,
        risk_notes=[],
        references=[],
        scripts=[],
        templates=[],
        examples=[],
        tags=[],
        metadata={},
    )
    # []: narrow to nothing
    s_empty = dict(s_none)
    s_empty["allowed_tools"] = []
    # [a, b]: narrow to listed
    s_list = dict(s_none)
    s_list["allowed_tools"] = ["a", "b"]

    assert s_none["allowed_tools"] is None
    assert s_empty["allowed_tools"] == []
    assert s_list["allowed_tools"] == ["a", "b"]


def test_agent_state_required_fields() -> None:
    state: AgentState = {
        "run_id": "01HX...",
        "root_run_id": "01HX...",
        "parent_run_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": "ask",
        "task": {},
        "messages": [],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": [],
        "workspace_refs": [],
        "pending_approval": None,
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
    }
    assert state["status"] == "running"


def test_tool_spec_full_shape() -> None:
    spec: ToolSpec = {
        "name": "x",
        "description": "y",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": "L1",
        "side_effect": False,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": False,
        "dry_run_supported": False,
        "tags": [],
    }
    assert spec["risk_level"] == "L1"


def test_trust_annotation_shape() -> None:
    ann: TrustAnnotation = {
        "trust_level": "untrusted",
        "source_kind": "tool_result",
        "source_id": "tool_call_42",
        "sanitizer": "default",
    }
    assert ann["trust_level"] == "untrusted"


def test_policy_decision_shape() -> None:
    d: PolicyDecision = {
        "decision": "require_approval",
        "reason": "L3 business write",
        "approval_id": "01HX...",
        "review_requirement": None,
        "denied_retry": False,
        "audit": {},
    }
    assert d["approval_id"]


def test_memory_record_round_trip() -> None:
    r: MemoryRecord = {
        "id": "mem_01",
        "scope": "user",
        "type": "feedback",
        "name": "tone",
        "description": "user prefers terse",
        "body": "Be concise.",
        "tags": ["style"],
        "source_run_id": None,
        "created_at": "2026-05-28T00:00:00.000Z",
        "updated_at": "2026-05-28T00:00:00.000Z",
        "expires_at": None,
        "metadata": {},
    }
    assert r["scope"] == "user"


def test_run_task_request_response() -> None:
    req: RunTaskRequest = {
        "agent": "support-bot",
        "input": {},
        "options": {},
        "permission_mode": None,
        "thread_id": None,
        "parent_run_id": None,
    }
    resp: RunTaskResponse = {
        "run_id": "01H",
        "thread_id": None,
        "status": "completed",
        "output": None,
        "pending_approval": None,
        "error": None,
    }
    assert req["agent"]
    assert resp["status"] == "completed"


def test_trace_event_shape() -> None:
    ev: TraceEvent = {
        "event_id": "01H",
        "run_id": "01H",
        "root_run_id": "01H",
        "parent_run_id": None,
        "thread_id": None,
        "timestamp": "2026-05-28T00:00:00.000Z",
        "event_type": "memory_selection",
        "payload": {},
        "payload_ref": None,
    }
    assert ev["event_type"] == "memory_selection"


def test_stream_event_terminal_carries_response() -> None:
    resp: RunTaskResponse = {
        "run_id": "01H",
        "thread_id": None,
        "status": "completed",
        "output": {"reply": "ok"},
        "pending_approval": None,
        "error": None,
    }
    ev: StreamEvent = {
        "event_type": "terminal",
        "run_id": "01H",
        "sequence": 42,
        "payload": {},
        "terminal_response": resp,
    }
    assert ev["terminal_response"] is not None


def test_thread_info_shape() -> None:
    t: ThreadInfo = {
        "thread_id": "th_01",
        "agent_name": "support-bot",
        "created_at": "2026-05-28T00:00:00.000Z",
        "last_active_at": "2026-05-28T00:00:00.000Z",
        "run_count": 0,
        "status": "open",
    }
    assert t["status"] == "open"


def test_skill_asset_ref_shape() -> None:
    a: SkillAssetRef = {
        "kind": "reference",
        "name": "guide.md",
        "path": "skills/x/references/guide.md",
        "size_bytes": 100,
        "summary": None,
    }
    assert a["kind"] == "reference"


def test_workspace_ref_shape() -> None:
    w: WorkspaceRef = {
        "run_id": "01H",
        "kind": "artifact",
        "path": ".modi/workspace/01H/artifacts/x",
        "artifact_id": "01H",
        "mime_type": "text/plain",
        "trust_level": "trusted",
        "size_bytes": 0,
        "metadata": {},
    }
    assert w["kind"] == "artifact"


def test_requested_action_kinds() -> None:
    a: RequestedAction = {
        "kind": "tool_call",
        "tool_name": "x",
        "arguments": {},
        "target": None,
        "fingerprint": "abc",
    }
    assert a["kind"] == "tool_call"


def test_policy_context_shape() -> None:
    profile: AgentProfile = {  # type: ignore[typeddict-item]
        "name": "x", "description": "y", "instruction": "",
        "default_tools": [], "default_skills": [],
        "output_contract": None, "permission_profile": None,
        "safety_constraints": [], "tags": [], "metadata": {},
    }
    state: AgentState = {  # type: ignore[typeddict-item]
        "run_id": "01H", "root_run_id": "01H", "parent_run_id": None,
        "thread_id": None, "agent_name": "x", "permission_mode": "ask",
        "task": {}, "messages": [], "loaded_skills": [], "tool_calls": [],
        "denied_actions": [], "workspace_refs": [],
        "pending_approval": None, "draft_output": None, "final_output": None,
        "step_count": 0, "status": "running",
    }
    ctx: PolicyContext = {
        "agent": profile,
        "skill": None,
        "tool_spec": None,
        "state": state,
        "requested_action": {
            "kind": "tool_call", "tool_name": "x", "arguments": {},
            "target": None, "fingerprint": "abc",
        },
        "permission_mode": "ask",
    }
    assert ctx["permission_mode"] == "ask"


def test_permission_profile_shape() -> None:
    p: PermissionProfile = {
        "mode": "auto",
        "preauthorized": ["t1"],
        "deny": [],
        "review_required": ["t2"],
    }
    assert p["mode"] == "auto"


def test_denied_action_and_pending_approval() -> None:
    d: DeniedAction = {
        "fingerprint": "abc",
        "tool_name": "x",
        "arguments": {},
        "reason": "user denied",
        "decided_at": "2026-05-28T00:00:00.000Z",
    }
    a: PendingApproval = {
        "approval_id": "01H",
        "tool_call_id": "01H",
        "decision": "require_approval",
        "summary": "do x",
        "risk_level": "L3",
        "requested_at": "2026-05-28T00:00:00.000Z",
    }
    assert d["reason"] and a["risk_level"] == "L3"


def test_message_role_shape() -> None:
    m: Message = {
        "role": "assistant",
        "content": "hi",
        "tool_call_id": None,
        "metadata": {},
    }
    assert m["role"] == "assistant"


def test_tool_call_proposal_and_record() -> None:
    p: ToolCallProposal = {
        "tool_call_id": "01H",
        "tool_name": "x",
        "arguments": {"a": 1},
        "malformed": False,
        "parse_error": None,
    }
    r: ToolCallRecord = {
        "tool_call_id": "01H",
        "tool_name": "x",
        "arguments": {"a": 1},
        "decision": "allow",
        "result": {"ok": True},
        "error": None,
        "started_at": "2026-05-28T00:00:00.000Z",
        "finished_at": None,
    }
    assert p["tool_name"] == r["tool_name"]


def test_tool_binding_value_equality() -> None:
    from modi_harness.types import ToolBinding

    spec = {"name": "x", "description": "d", "input_schema": {}}
    def h(**_): return None

    a = ToolBinding(spec=spec, handler=h)
    b = ToolBinding(spec=spec, handler=h)
    assert a == b


def test_tool_binding_from_tuple_accepts_either_form() -> None:
    from modi_harness.types import ToolBinding

    spec = {"name": "x", "description": "d", "input_schema": {}}
    def h(**_): return None

    a = ToolBinding.from_tuple((spec, h))
    b = ToolBinding.from_tuple(ToolBinding(spec=spec, handler=h))
    assert a == b


def test_permissions_config_defaults() -> None:
    from modi_harness.types import PermissionsConfig

    cfg = PermissionsConfig()
    assert cfg.preauthorized == ()
    assert cfg.deny == ()
    assert cfg.review_required == ()
    assert cfg.mode is None


def test_model_spec_value_equality() -> None:
    from modi_harness.types import ModelSpec

    a = ModelSpec(provider="anthropic", name="claude-sonnet-4-6")
    b = ModelSpec(provider="anthropic", name="claude-sonnet-4-6")
    assert a == b


def test_task_input_recognized_keys() -> None:
    # total=False: every field optional; a plain dict is a valid TaskInput.
    ti: TaskInput = {
        "messages": [{"role": "user", "content": "hi"}],
        "prompt": "hi",
        "customer_message": "hi",
        "question": "hi",
        "goal": "hi",
        "tags": ["billing"],
        "reference_keys": ["refund_policy"],
    }
    assert ti["tags"] == ["billing"]
    # An empty payload is also a valid TaskInput.
    empty: TaskInput = {}
    assert empty == {}
