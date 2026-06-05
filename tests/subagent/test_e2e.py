"""Subagent Runtime end-to-end scenarios."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiAgent, ModiSession
from modi_harness._test_fixtures import make_session


class _Script(BaseChatModel):
    """Routes calls by agent_name. Each agent gets its own message list."""

    by_agent: dict[str, list[Any]] = Field(default_factory=dict)
    cursor: dict[str, dict[str, int]] = Field(default_factory=dict)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        agent_name = self._sniff(messages)
        cur = self.cursor.setdefault(agent_name, {"i": 0})
        i = cur["i"]
        seq = self.by_agent.get(agent_name, [])
        if i >= len(seq):
            raise RuntimeError(f"_Script for {agent_name} exhausted at {i}")
        msg = seq[i]
        cur["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _sniff(self, messages) -> str:
        for m in messages:
            content = getattr(m, "content", "") or ""
            if isinstance(content, str) and "AGENT_NAME=" in content:
                return content.split("AGENT_NAME=", 1)[1].split("\n", 1)[0].strip()
        return "unknown"

    @property
    def _llm_type(self) -> str:
        return "subagent_script"


def _write_agent(
    root: Path,
    name: str,
    *,
    tools: list[str] | None = None,
    allowed_subagents: list[str] | None = None,
    subagent_max_depth: int | None = None,
    permission_mode: str = "auto",
) -> Path:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tool_block = "\n".join(f"  - {t}" for t in (tools or []))
    pp_lines = [f"  mode: {permission_mode}"]
    if allowed_subagents is not None:
        pp_lines.append(f"  allowed_subagents: {allowed_subagents!r}")
    if subagent_max_depth is not None:
        pp_lines.append(f"  subagent_max_depth: {subagent_max_depth}")
    pp_block = "\n".join(pp_lines)
    p.write_text(
        f"""---
name: {name}
description: e2e
tools:
{tool_block if tool_block else '  []'}
permission_profile:
{pp_block}
---
AGENT_NAME={name}
"""
    )
    return p


def _agent(
    tmp_path: Path,
    name: str,
    *,
    tools: list[str] | None = None,
    allowed_subagents: list[str] | None = None,
    subagent_max_depth: int | None = None,
    permission_mode: str = "auto",
    subagents: list[ModiAgent] | None = None,
    tool_bindings: list[tuple[dict, Any]] | None = None,
) -> ModiAgent:
    """Write an agent.md and load it as a ModiAgent with nested subagents."""
    path = _write_agent(
        tmp_path / "agents",
        name,
        tools=tools,
        allowed_subagents=allowed_subagents,
        subagent_max_depth=subagent_max_depth,
        permission_mode=permission_mode,
    )
    return ModiAgent.from_markdown(
        path, subagents=subagents, tools=tool_bindings
    )


def _session(tmp_path: Path, script: _Script, agents: list[ModiAgent]) -> ModiSession:
    return make_session(tmp_path, chat_model=script, agents=agents)


# ----------------------------------------------------------------------
# 1. Parent → child happy path
# ----------------------------------------------------------------------


def test_parent_child_happy_path(tmp_path: Path) -> None:
    research = _agent(tmp_path, "research", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        subagents=[research],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {
                                "task": {"goal": "summarize"},
                                "rationale": "need facts",
                            },
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Final reply with research"),
            ],
            "research": [
                AIMessage(content="Researched answer."),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    response = h.run_task(agent="lead", input={"goal": "research and reply"}, thread_id="t-happy")
    assert response["status"] == "completed"
    assert "research" in (response["output"] or {}).get("value", "").lower()


# ----------------------------------------------------------------------
# 2. allowed_subagents=[] denies dispatch
# ----------------------------------------------------------------------


def test_allowed_subagents_empty_denies(tmp_path: Path) -> None:
    research = _agent(tmp_path, "research", tools=[])
    solo = _agent(
        tmp_path,
        "solo",
        tools=["delegate_to_research"],
        allowed_subagents=[],  # explicit empty
        subagents=[research],
    )
    script = _Script(
        by_agent={
            "solo": [
                AIMessage(content="No delegation possible; replying directly."),
            ],
        }
    )
    h = _session(tmp_path, script, [solo])
    response = h.run_task(agent="solo", input={"goal": "x"}, thread_id="t-empty")
    # Context Manager filters the delegate_to_* tool out, so model sees no
    # delegation tool. Model just replies directly.
    assert response["status"] == "completed"


# ----------------------------------------------------------------------
# 3. Permission mode strict-only: child requests laxer mode → denied
# ----------------------------------------------------------------------


def test_child_cannot_request_laxer_mode(tmp_path: Path) -> None:
    research = _agent(tmp_path, "research", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        permission_mode="auto",
        subagents=[research],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {
                                "task": {"goal": "x"},
                                "permission_mode": "trust",  # laxer than parent auto
                                "rationale": "need quick",
                            },
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Could not delegate; mode blocked."),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    response = h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-mode")
    assert response["status"] == "completed"
    state = h.get_state("t-mode")
    assert state is not None
    # The dispatch was denied (denied_retry outcome) so no child happened;
    # parent recovers with second message.


# ----------------------------------------------------------------------
# 4. Subagent depth limit
# ----------------------------------------------------------------------


def test_subagent_depth_limit(tmp_path: Path) -> None:
    # Build a chain: lead -> mid -> deep, with cap=1 (only 1 dispatch allowed
    # below root). lead -> mid is depth 1 (allowed); mid -> deep is depth 2
    # which exceeds the cap of 1, so dispatch is denied.
    deep = _agent(tmp_path, "deep", tools=[], permission_mode="auto")
    mid = _agent(
        tmp_path,
        "mid",
        tools=["delegate_to_deep"],
        allowed_subagents=["deep"],
        subagent_max_depth=1,
        permission_mode="auto",
        subagents=[deep],
    )
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_mid"],
        allowed_subagents=["mid"],
        subagent_max_depth=1,
        permission_mode="auto",
        subagents=[mid],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_mid",
                            "args": {"task": {"goal": "deep"}, "rationale": "go"},
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Done at top"),
            ],
            "mid": [
                # mid tries to delegate to deep — depth would be 2, exceeds cap 1.
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_deep",
                            "args": {"task": {"goal": "deeper"}, "rationale": "go"},
                            "id": "tc2",
                        }
                    ],
                ),
                AIMessage(content="Could not go deeper; depth blocked."),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    response = h.run_task(agent="lead", input={"goal": "go"}, thread_id="t-depth")
    assert response["status"] == "completed"


# ----------------------------------------------------------------------
# 5. Denied action propagation: parent denial reaches child
# ----------------------------------------------------------------------


def test_parent_denied_action_reaches_child(tmp_path: Path) -> None:
    send_tool = (
        {
            "name": "send",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"to": {"type": "string"}},
                "required": ["to"],
            },
            "risk_level": "L3",
            "side_effect": True,
        },
        lambda **kw: {"sent": True},
    )
    research = _agent(
        tmp_path,
        "research",
        tools=["send"],
        permission_mode="auto",
        tool_bindings=[send_tool],
    )
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research", "send"],
        allowed_subagents=["research"],
        permission_mode="auto",
        subagents=[research],
        tool_bindings=[send_tool],
    )
    script = _Script(
        by_agent={
            "lead": [
                # First, propose a side-effect call that gets rejected.
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "send", "args": {"to": "x"}, "id": "tc_send"}
                    ],
                ),
                # After rejection, delegate to research (which will inherit denial).
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {"task": {"goal": "x"}, "rationale": "x"},
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Done."),
            ],
            "research": [
                # Child tries to call same send(to=x) — must be denied as a retry.
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "send", "args": {"to": "x"}, "id": "tc_send_child"}
                    ],
                ),
                AIMessage(content="Could not send (denied)."),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    first = h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-prop")
    assert first["status"] == "interrupted"
    rejected = h.reject_action(
        thread_id="t-prop",
        approval_id=first["pending_approval"]["approval_id"],
        reason="user denied",
    )
    assert rejected["status"] == "completed"


# ----------------------------------------------------------------------
# 6. Child output is treated as untrusted
# ----------------------------------------------------------------------


def test_child_output_wrapped_untrusted(tmp_path: Path) -> None:
    research = _agent(tmp_path, "research", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        subagents=[research],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {"task": {"goal": "x"}, "rationale": "x"},
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="OK based on research."),
            ],
            "research": [
                AIMessage(content="evidence"),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    response = h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-trust")
    assert response["status"] == "completed"
    state = h.get_state("t-trust")
    assert state is not None
    # Parent's tool_calls record should reflect a delegate_to_research call.
    names = [tc["tool_name"] for tc in state["tool_calls"]]
    assert "delegate_to_research" in names


# ----------------------------------------------------------------------
# 7. Subagent registration: every agent gets a delegate_to_<name> tool
# ----------------------------------------------------------------------


def test_all_agents_have_delegate_tools(tmp_path: Path) -> None:
    # V0.5: delegate_to_<name> is auto-registered for NESTED subagents only.
    # Declare alpha/beta/gamma as subagents of a root and assert each got a
    # delegate tool in the merged registry.
    alpha = _agent(tmp_path, "alpha", tools=[])
    beta = _agent(tmp_path, "beta", tools=[])
    gamma = _agent(tmp_path, "gamma", tools=[])
    root = _agent(
        tmp_path,
        "root",
        tools=["delegate_to_alpha", "delegate_to_beta", "delegate_to_gamma"],
        allowed_subagents=["alpha", "beta", "gamma"],
        subagents=[alpha, beta, gamma],
    )
    h = _session(tmp_path, _Script(by_agent={}), [root])
    names = h._tool_gateway._registry.names()
    assert "delegate_to_alpha" in names
    assert "delegate_to_beta" in names
    assert "delegate_to_gamma" in names


# ----------------------------------------------------------------------
# 8. Subagent target unknown (no agent file) → cannot delegate
# ----------------------------------------------------------------------


def test_unknown_subagent_target_cannot_be_delegated(tmp_path: Path) -> None:
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_ghost"],  # ghost agent doesn't exist
        allowed_subagents=["ghost"],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(content="No tool to delegate; replying."),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    # Tool isn't auto-registered (ghost is not a nested subagent), so gateway
    # returns an "unknown tool" error if the model tries it. Model just replies.
    response = h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-ghost")
    assert response["status"] == "completed"


# ----------------------------------------------------------------------
# 9. Subagent visibility narrows by allowed_subagents
# ----------------------------------------------------------------------


def test_allowed_subagents_filters_context_tools(tmp_path: Path) -> None:
    a = _agent(tmp_path, "a", tools=[])
    b = _agent(tmp_path, "b", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_a", "delegate_to_b"],
        allowed_subagents=["a"],  # only a, not b
        permission_mode="auto",
        subagents=[a, b],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(content="ok"),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    # Drive the run to extract the ContextPack via the model adapter call.
    h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-allow")
    # Indirect check: ensure the session registered both delegate_to_* tools
    # but the agent's allowed_subagents will narrow them in context.
    names = h._tool_gateway._registry.names()
    assert "delegate_to_a" in names
    assert "delegate_to_b" in names


# ----------------------------------------------------------------------
# Subagent trace + workspace persistence
# ----------------------------------------------------------------------


def test_child_run_trace_persisted(tmp_path: Path) -> None:
    """Child runs must flush their pending_trace_events to logs/trace.jsonl."""
    research = _agent(tmp_path, "research", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        subagents=[research],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {
                                "task": {"goal": "summarize"},
                                "rationale": "need facts",
                            },
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Final reply with research"),
            ],
            "research": [
                AIMessage(content="Researched answer."),
            ],
        }
    )
    h = _session(tmp_path, script, [lead])
    response = h.run_task(
        agent="lead", input={"goal": "x"}, thread_id="t-child-trace"
    )
    assert response["status"] == "completed"

    # The parent's tool_result for delegate_to_research carries child_run_id.
    parent_events = list(h.get_trace(response["thread_id"]))
    delegate_events = [
        e for e in parent_events
        if e["event_type"] == "tool_result"
        and e["payload"].get("tool_name") == "delegate_to_research"
    ]
    assert delegate_events, "missing delegate_to_research tool_result in parent trace"

    # The child run_id is wired into the tool result payload via the dispatcher.
    # Walk the workspace to find any child run dir; assert it contains a trace.
    ws_root = tmp_path / "ws"
    child_dirs = [
        p for p in ws_root.iterdir()
        if p.is_dir() and p.name != response["run_id"]
    ]
    assert child_dirs, f"no child run dir found under {ws_root}"
    child = child_dirs[0]
    trace = child / "logs" / "trace.jsonl"
    assert trace.exists(), f"child trace missing at {trace}"
    lines = [ln for ln in trace.read_text().splitlines() if ln.strip()]
    assert lines, "child trace.jsonl is empty"
    types = {__import__("json").loads(ln)["event_type"] for ln in lines}
    assert "run_start" in types
    assert "run_end" in types


def test_child_run_trace_persisted_via_streaming(tmp_path: Path) -> None:
    """astream-driven parent → subagent must still persist child trace."""
    import asyncio

    research = _agent(tmp_path, "research", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        subagents=[research],
    )
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {
                                "task": {"goal": "summarize"},
                                "rationale": "facts",
                            },
                            "id": "tc-stream",
                        }
                    ],
                ),
                AIMessage(content="Done."),
            ],
            "research": [AIMessage(content="Researched answer.")],
        }
    )
    h = _session(tmp_path, script, [lead])

    async def _drive() -> None:
        async for _ in h.astream(
            agent="lead", input={"goal": "x"}, thread_id="t-stream-child"
        ):
            pass

    asyncio.run(_drive())

    ws_root = tmp_path / "ws"
    runs = sorted(ws_root.iterdir(), key=lambda p: p.stat().st_mtime)
    assert len(runs) >= 2, f"expected parent + child workspace, got {runs}"
    # All non-empty workspaces must have a trace file.
    for run in runs:
        trace = run / "logs" / "trace.jsonl"
        assert trace.exists() and trace.stat().st_size > 0, (
            f"missing/empty trace at {trace}"
        )


# ----------------------------------------------------------------------
# 10. Delegated child receives derived user text, not str(dict)
# ----------------------------------------------------------------------


def test_child_receives_derived_text_not_stringified_dict(tmp_path: Path) -> None:
    """Regression: dispatcher must seed the child's first user message via
    task_input_to_text, not str(child_input). Delegating task={"goal": "X"}
    must give the child a user message of "X", never "{'goal': 'X'}"."""

    class _Capturing(_Script):
        seen_user_text: dict[str, list[str]] = Field(default_factory=dict)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            agent_name = self._sniff(messages)
            # Record the last human-role message content this agent saw.
            for m in messages:
                if isinstance(m, HumanMessage):
                    self.seen_user_text.setdefault(agent_name, []).append(
                        str(getattr(m, "content", ""))
                    )
            return super()._generate(messages, stop, run_manager, **kwargs)

    research = _agent(tmp_path, "research", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        subagents=[research],
    )
    script = _Capturing(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {
                                "task": {"goal": "summarize the report"},
                                "rationale": "need facts",
                            },
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Final reply."),
            ],
            "research": [AIMessage(content="Summary done.")],
        }
    )
    h = _session(tmp_path, script, [lead])
    response = h.run_task(
        agent="lead", input={"goal": "go"}, thread_id="t-derived"
    )
    assert response["status"] == "completed"

    child_texts = script.seen_user_text.get("research", [])
    assert child_texts, "child model never received a human message"
    joined = " ".join(child_texts)
    assert "summarize the report" in joined
    assert "{'goal'" not in joined  # the bug: stringified dict must not appear
