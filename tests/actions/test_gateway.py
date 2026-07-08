"""ActionGateway: alignment-first execution path (plan N5).

Covers the plan's required cases:
- old L0/L1 execution still works (now through alignment+governance);
- denied retry stays blocked before any alignment;
- a reviewed proposal cannot be changed on resume (integrity);
- the trace carries action id + alignment decision id;
- preview/dry-run still works through the governance layer;
- no-intent state is rejected before execution.
"""
from __future__ import annotations

from typing import Any

from modi_harness.actions import ActionGateway
from modi_harness.autonomy.scope import derive_autonomy_scope
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.intent.types import (
    HumanIntentContext,
    IntentBoundary,
    IntentClarity,
    IntentStage,
)
from modi_harness.policy import PolicyGate
from modi_harness.tools import ToolRegistry

# ---------- spec / agent / proposal ----------


def _spec(
    name: str = "t_x",
    risk_level: str = "L1",
    *,
    side_effect: bool = False,
    idempotent: bool = False,
    dry_run_supported: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": "",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        "output_schema": None,
        "risk_level": risk_level,
        "side_effect": side_effect,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": idempotent,
        "dry_run_supported": dry_run_supported,
        "tags": [],
    }


def _agent(default_tools: list[str] | None = None) -> dict:
    return {
        "name": "x",
        "description": "y",
        "instruction": "",
        "default_tools": default_tools if default_tools is not None else ["t_x"],
        "default_skills": [],
        "output_contract": None,
        "permission_profile": None,
        "safety_constraints": [],
        "tags": [],
        "metadata": {},
    }


def _proposal(tool: str = "t_x", args: dict | None = None, *, tcid: str = "01H_TC") -> dict:
    return {
        "tool_call_id": tcid,
        "tool_name": tool,
        "arguments": args if args is not None else {"q": "hi"},
        "malformed": False,
        "parse_error": None,
    }


# ---------- intent / clarity / scope ----------


def _stage(kind: str = "explore") -> IntentStage:
    return IntentStage(
        id=f"stage-{kind}",
        kind=kind,  # type: ignore[typeddict-item]
        goal="g",
        exit_criteria=[],
        judgment_required_before_exit=False,
    )


def _intent(*, boundaries: list[IntentBoundary] | None = None) -> HumanIntentContext:
    return HumanIntentContext(
        version=2,
        goal="research X",
        desired_outcome=None,
        boundaries=boundaries or [],
        non_goals=[],
        success_criteria=[],
        current_stage=_stage(),
        responsibility={
            "owner": None,
            "on_behalf_of": None,
            "irreversible_requires_judgment": True,
            "notes": None,
        },
        escalation={"default_action": "ask", "escalate_on": [], "quiet": False},
        tradeoffs={},
        confirmed_inputs={},
        decisions=[],
        corrections=[],
    )


def _clarity(level: str = "stable") -> IntentClarity:
    return IntentClarity(level=level, unknowns=[], assumptions=[], confidence=0.9)  # type: ignore[typeddict-item]


def _hard_deny_boundary() -> IntentBoundary:
    return IntentBoundary(
        id="b-hard",
        kind="external_commitment",
        statement="never do that",
        severity="hard",
        escalation="deny",
    )


def _state(
    *,
    mode: str = "ask",
    denied: list | None = None,
    intent: HumanIntentContext | None = None,
    clarity: IntentClarity | None = None,
    with_intent: bool = True,
) -> dict:
    s: dict[str, Any] = {
        "run_id": "r1",
        "root_run_id": "r1",
        "parent_run_id": None,
        "thread_id": None,
        "agent_name": "x",
        "permission_mode": mode,
        "task": {},
        "messages": [],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": denied or [],
        "workspace_refs": [],
        "pending_approval": None,
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
    }
    if with_intent:
        the_intent = intent or _intent()
        the_clarity = clarity or _clarity()
        s["human_intent"] = the_intent
        s["intent_version"] = the_intent["version"]
        s["stage_id"] = the_intent["current_stage"]["id"]
        s["intent_clarity"] = the_clarity
        s["autonomy_scope"] = derive_autonomy_scope(the_clarity, the_intent)
    return s


# ---------- gateway builder ----------


def _dispatcher() -> HookDispatcher:
    return HookDispatcher(registry=HookRegistry([]), project_root=".", pass_env=[])


def _allow_judge(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"verdict": "allow", "matched_boundary_ids": [], "drift": False, "reason": "ok"}


def _verdict_judge(verdict: str) -> Any:
    def judge(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "verdict": verdict,
            "matched_boundary_ids": [],
            "drift": verdict in {"redirect", "constrain", "deny"},
            "reason": f"model says {verdict}",
        }

    return judge


def _gateway(
    handlers: dict[str, Any] | None = None,
    *,
    specs: list[dict] | None = None,
    judge: Any = None,
) -> ActionGateway:
    registry = ToolRegistry()
    for spec in specs or []:
        registry.register_tool(
            spec, handlers.get(spec["name"]) if handlers else (lambda **kw: {})
        )
    return ActionGateway(
        registry=registry,
        policy=PolicyGate(),
        hooks=_dispatcher(),
        result_inline_limit_bytes=8192,
        judge=judge if judge is not None else _allow_judge,
    )


# ---------- old L0/L1 execution still works ----------


def test_l1_executes_through_alignment_and_governance() -> None:
    gw = _gateway(handlers={"t_x": lambda **kw: {"echo": kw["q"]}}, specs=[_spec("t_x", "L1")])
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "executed"
    assert result.record["result"] == {"echo": "hi"}


def test_l0_executes() -> None:
    gw = _gateway(handlers={"t_x": lambda **kw: {"ok": True}}, specs=[_spec("t_x", "L0")])
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "executed"


# ---------- trace carries action id + alignment decision id ----------


def test_trace_carries_action_and_alignment_ids() -> None:
    gw = _gateway(handlers={"t_x": lambda **kw: {"ok": True}}, specs=[_spec("t_x", "L1")])
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "executed"
    assert result.action_id is not None
    assert result.alignment_decision_id is not None
    # Full records ride along so the node can emit lineage trace events without
    # re-deriving them. ids on the records match the stamped summary ids.
    assert result.alignment_decision is not None
    assert result.alignment_decision["id"] == result.alignment_decision_id
    assert result.alignment_decision["intent_version"] == 2
    assert result.action_proposal is not None
    assert result.action_proposal["id"] == result.action_id
    assert result.action_proposal["stage_id"] == "stage-explore"
    assert result.action_proposal["parent_step_id"] is None


def test_consequential_action_requires_parent_step_lineage() -> None:
    called: list[Any] = []

    def handler(**kw: Any) -> dict[str, Any]:
        called.append(kw)
        return {"ok": True}

    gw = _gateway(
        handlers={"write_file": handler},
        specs=[_spec("write_file", "L1", side_effect=True)],
    )
    result = gw.execute_tool_call(
        _proposal(tool="write_file"),
        agent=_agent(default_tools=["write_file"]),
        state=_state(),
    )

    assert result.outcome == "error"
    assert "step lineage required" in (result.error_message or "")
    assert result.action_proposal["parent_step_id"] is None
    assert called == []


def test_consequential_action_executes_with_parent_step_lineage() -> None:
    gw = _gateway(
        handlers={"write_file": lambda **kw: {"ok": True}},
        specs=[_spec("write_file", "L1", side_effect=True)],
    )
    proposal = _proposal(tool="write_file")
    proposal["metadata"] = {"parent_step_id": "loop-abc-0001"}

    result = gw.execute_tool_call(
        proposal,
        agent=_agent(default_tools=["write_file"]),
        state=_state(),
    )

    assert result.outcome == "executed"
    assert result.action_proposal["parent_step_id"] == "loop-abc-0001"


# ---------- denied retry blocks before alignment ----------


def test_denied_retry_blocks_before_alignment() -> None:
    judged: list[Any] = []

    def judge(*a: Any, **k: Any) -> dict[str, Any]:
        judged.append((a, k))
        return _allow_judge()

    gw = _gateway(
        handlers={"t_x": lambda **kw: {"ok": True}},
        specs=[_spec("t_x", "L1")],
        judge=judge,
    )
    state = _state(
        denied=[
            {
                "fingerprint": "fp",
                "tool_name": "t_x",
                "arguments": {"q": "hi"},
                "reason": "user denied",
                "decided_at": "2026-05-28T00:00:00.000Z",
            }
        ]
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=state)
    assert result.outcome == "denied_retry"
    # Alignment never ran — denied-retry is caught in the shared _prepare phase.
    assert judged == []


# ---------- hard-deny boundary denies even when the model would allow ----------


def test_hard_boundary_denies_even_if_model_allows() -> None:
    called: list[Any] = []

    def handler(**kw: Any) -> dict[str, Any]:
        called.append(kw)
        return {"ok": True}

    gw = _gateway(
        handlers={"t_x": handler},
        specs=[_spec("t_x", "L1")],
        judge=_allow_judge,  # model says allow, even matching the boundary
    )
    state = _state(intent=_intent(boundaries=[_hard_deny_boundary()]))
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=state)
    assert result.outcome == "error"
    assert called == []


def test_alignment_redirect_never_executes_handler() -> None:
    called: list[Any] = []

    def handler(**kw: Any) -> dict[str, Any]:
        called.append(kw)
        return {"ok": True}

    gw = _gateway(
        handlers={"t_x": handler},
        specs=[_spec("t_x", "L0")],
        judge=_verdict_judge("redirect"),
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "error"
    assert result.decision is not None
    assert result.decision["decision"] == "deny"
    assert result.alignment_decision is not None
    assert result.alignment_decision["decision"] == "redirect"
    assert "redirect" in (result.error_message or "").lower()
    assert called == []


def test_alignment_constrain_pauses_instead_of_executing_original_action() -> None:
    called: list[Any] = []

    def handler(**kw: Any) -> dict[str, Any]:
        called.append(kw)
        return {"ok": True}

    gw = _gateway(
        handlers={"t_x": handler},
        specs=[_spec("t_x", "L0")],
        judge=_verdict_judge("constrain"),
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "interrupt"
    assert result.decision is not None
    assert result.decision["decision"] == "require_approval"
    assert result.alignment_decision is not None
    assert result.alignment_decision["decision"] == "constrain"
    assert called == []


def test_alignment_ask_judgment_pauses_before_handler() -> None:
    called: list[Any] = []

    def handler(**kw: Any) -> dict[str, Any]:
        called.append(kw)
        return {"ok": True}

    gw = _gateway(
        handlers={"t_x": handler},
        specs=[_spec("t_x", "L0")],
        judge=_verdict_judge("ask_judgment"),
    )
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state())
    assert result.outcome == "interrupt"
    assert result.decision is not None
    assert result.decision["decision"] == "require_approval"
    assert result.alignment_decision is not None
    assert result.alignment_decision["decision"] == "ask_judgment"
    assert called == []


# ---------- reviewed proposal cannot change on resume ----------


def test_reviewed_proposal_cannot_change_on_resume() -> None:
    gw = _gateway(handlers={"t_x": lambda **kw: {"ok": True}}, specs=[_spec("t_x", "L3")])
    # L3 → governance routes to human judgment; gateway records the reviewed hash.
    first = gw.execute_tool_call(_proposal(args={"q": "safe"}), agent=_agent(), state=_state())
    assert first.outcome == "interrupt"

    # Resume under elevated trust mode with the SAME tool_call_id but tampered args.
    tampered = _proposal(args={"q": "EVIL"}, tcid="01H_TC")
    resumed = gw.execute_tool_call(tampered, agent=_agent(), state=_state(mode="trust"))
    assert resumed.outcome == "error"
    assert "integrity" in (resumed.error_message or "").lower()


def test_reviewed_proposal_unchanged_passes_integrity() -> None:
    gw = _gateway(handlers={"t_x": lambda **kw: {"ok": True}}, specs=[_spec("t_x", "L3")])
    first = gw.execute_tool_call(_proposal(args={"q": "safe"}), agent=_agent(), state=_state())
    assert first.outcome == "interrupt"
    # Same args on resume → integrity passes, runs under elevated trust.
    resumed = gw.execute_tool_call(
        _proposal(args={"q": "safe"}), agent=_agent(), state=_state(mode="trust")
    )
    assert resumed.outcome == "executed"


# ---------- preview / dry-run still works through governance ----------


def test_preview_intercepts_side_effecting_tool() -> None:
    called: list[Any] = []

    def handler(**kw: Any) -> dict[str, Any]:
        called.append(kw)
        return {"ok": True}

    gw = _gateway(handlers={"t_x": handler}, specs=[_spec("t_x", "L1", side_effect=True)])
    result = gw.execute_tool_call(_proposal(), agent=_agent(), state=_state(mode="preview"))
    assert result.outcome == "executed"
    assert result.record["result"]["simulated"] is True
    assert called == []  # real handler never ran


# ---------- no-intent execution is rejected ----------


def test_no_intent_is_rejected_before_execution() -> None:
    gw = _gateway(handlers={"t_x": lambda **kw: {"ok": True}}, specs=[_spec("t_x", "L1")])
    result = gw.execute_tool_call(
        _proposal(), agent=_agent(), state=_state(with_intent=False)
    )
    assert result.outcome == "error"
    assert "intent and autonomy scope are required" in (result.error_message or "")
    assert result.alignment_decision_id is None


def test_no_intent_l3_is_rejected_before_approval() -> None:
    gw = _gateway(handlers={"t_x": lambda **kw: {"ok": True}}, specs=[_spec("t_x", "L3")])
    result = gw.execute_tool_call(
        _proposal(), agent=_agent(), state=_state(with_intent=False)
    )
    assert result.outcome == "error"
    assert "intent and autonomy scope are required" in (result.error_message or "")
