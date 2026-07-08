"""Offline tests for the support_triage multi-agent example."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness._test_fixtures import as_step_decision_message

from modi_harness import ModiHarness, ModiSession
from modi_harness.api.errors import AgentNotRegistered

_EXPERTS_PATH = Path(__file__).resolve().parents[2] / "examples" / "support_triage" / "_experts.py"


def _load_experts():
    """Load the example's _experts.py by file path (examples/ is not a package)."""
    spec = importlib.util.spec_from_file_location("support_triage_experts", _EXPERTS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lookup_account_known() -> None:
    experts = _load_experts()
    result = experts.lookup_account("acct_123")
    assert result["plan"] == "Pro"
    assert result["account_id"] == "acct_123"


def test_lookup_account_unknown() -> None:
    experts = _load_experts()
    result = experts.lookup_account("nope")
    assert "error" in result


def test_lookup_order_known() -> None:
    experts = _load_experts()
    result = experts.lookup_order("ord_555")
    assert result["refundable"] is True
    assert result["amount"] == 290


def test_lookup_order_unknown() -> None:
    experts = _load_experts()
    result = experts.lookup_order("nope")
    assert "error" in result


def test_build_triage_agent_topology() -> None:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    assert triage.name == "triage"
    # three specialists attached as subagents
    sub_names = sorted(a.name for a in triage.subagents)
    assert sub_names == ["billing", "refund", "technical"]
    # orchestrator declares delegate tools in its profile
    assert "billing" in (triage.permission_profile or {}).get("allowed_subagents", [])


def test_triage_profile_includes_delegate_tools_in_default_tools() -> None:
    """Regression guard: the projected AgentProfile must list delegate_to_*
    tools so the model sees them. V0.5's agent_to_profile broke this by only
    including ToolBinding-derived names, dropping frontmatter-declared tools.
    Fix: load_agent_object stores frontmatter tool names in metadata;
    agent_to_profile merges them."""
    from modi_harness.api._session_helpers import agent_to_profile
    experts = _load_experts()
    triage = experts.build_triage_agent()
    prof = agent_to_profile(triage)
    assert "delegate_to_billing" in prof["default_tools"]
    assert "delegate_to_technical" in prof["default_tools"]
    assert "delegate_to_refund" in prof["default_tools"]


def test_specialists_have_expected_tools() -> None:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    by_name = {a.name: a for a in triage.subagents}
    assert [t.spec["name"] for t in by_name["billing"].tools] == ["lookup_account"]
    assert [t.spec["name"] for t in by_name["refund"].tools] == ["lookup_order"]
    assert by_name["technical"].tools == ()  # pure-reasoning specialist


class _RoutingScript(BaseChatModel):
    """Serves a per-agent script. Picks the script by matching a stable
    substring of each agent's instruction in the prompt."""

    by_marker: dict[str, list[Any]] = Field(default_factory=dict)
    cursor: dict[str, int] = Field(default_factory=dict)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        marker = self._match(messages)
        i = self.cursor.get(marker, 0)
        seq = self.by_marker.get(marker, [])
        if i >= len(seq):
            raise RuntimeError(f"_RoutingScript for {marker!r} exhausted at {i}")
        self.cursor[marker] = i + 1
        return ChatResult(generations=[ChatGeneration(message=as_step_decision_message(seq[i]))])

    def _match(self, messages) -> str:
        text = " ".join(
            (getattr(m, "content", "") or "") for m in messages
            if isinstance(getattr(m, "content", ""), str)
        )
        if "triage agent" in text:
            return "triage"
        if "billing questions" in text:
            return "billing"
        if "refunds" in text:
            return "refund"
        if "technical problems" in text:
            return "technical"
        return "unknown"

    @property
    def _llm_type(self) -> str:
        return "routing_script"


def _session(tmp_path, script) -> ModiSession:
    experts = _load_experts()
    triage = experts.build_triage_agent()
    harness = ModiHarness(chat_model=script)
    return ModiSession(
        harness=harness,
        agents=[triage],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        max_steps=20,
    )


def _refund_script() -> _RoutingScript:
    return _RoutingScript(by_marker={
        "triage": [
            AIMessage(content="", tool_calls=[{
                "name": "delegate_to_refund",
                "args": {"task": {"ticket": "refund ord_555"}, "rationale": "refund request"},
                "id": "tc-del",
            }]),
            AIMessage(content="Your refund of $290 for ord_555 is approved."),
        ],
        "refund": [
            AIMessage(content="", tool_calls=[{
                "name": "lookup_order", "args": {"order_id": "ord_555"}, "id": "tc-lo",
            }]),
            AIMessage(content="Order ord_555 is refundable for $290. Approved."),
        ],
    })


def test_triage_routes_to_refund(tmp_path) -> None:
    s = _session(tmp_path, _refund_script())
    resp = s.run_task(
        agent="triage",
        input={"goal": "Resolve the support ticket.",
               "messages": [{"role": "user", "content": "Please refund order ord_555."}]},
        thread_id="t-refund",
    )
    assert resp["status"] == "completed"
    out = str(resp["output"])
    assert "290" in out or "refund" in out.lower()


def test_specialist_isolation(tmp_path) -> None:
    s = _session(tmp_path, _RoutingScript(by_marker={}))
    assert s.list_agents() == ["triage"]
    assert set(s.list_all_agents()) == {"triage", "billing", "technical", "refund"}
    with pytest.raises(AgentNotRegistered):
        s.run_task(agent="refund", input={"goal": "x", "messages": []})


def test_delegation_appears_in_trace(tmp_path) -> None:
    """The parent trace records the delegate_to_<specialist> call. Subagent
    runs are isolated child runs with their own trace files, so the
    specialist's own tool calls (e.g. lookup_order) intentionally do NOT
    appear in the parent trace — delegation is the boundary."""
    s = _session(tmp_path, _refund_script())
    resp = s.run_task(
        agent="triage",
        input={"goal": "Resolve the support ticket.",
               "messages": [{"role": "user", "content": "Please refund order ord_555."}]},
        thread_id="t-trace",
    )
    tid = resp["thread_id"]
    tool_names = [
        ev["payload"].get("tool_name")
        for ev in s.get_trace(tid)
        if ev["event_type"] == "tool_result"
    ]
    assert "delegate_to_refund" in tool_names
    # NOT asserting lookup_order here — it lives in the child run's trace.
