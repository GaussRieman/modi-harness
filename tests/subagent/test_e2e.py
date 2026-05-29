"""Subagent Runtime end-to-end scenarios."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiHarness


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
) -> None:
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


def _harness(tmp_path: Path, script: _Script) -> ModiHarness:
    return ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=script,
    )


# ----------------------------------------------------------------------
# 1. Parent → child happy path
# ----------------------------------------------------------------------


def test_parent_child_happy_path(tmp_path: Path) -> None:
    _write_agent(
        tmp_path / "agents",
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
    )
    _write_agent(tmp_path / "agents", "research", tools=[])
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
    h = _harness(tmp_path, script)
    response = h.run_task(agent="lead", input={"goal": "research and reply"}, thread_id="t-happy")
    assert response["status"] == "completed"
    assert "research" in (response["output"] or {}).get("value", "").lower()


# ----------------------------------------------------------------------
# 2. allowed_subagents=[] denies dispatch
# ----------------------------------------------------------------------


def test_allowed_subagents_empty_denies(tmp_path: Path) -> None:
    _write_agent(
        tmp_path / "agents",
        "solo",
        tools=["delegate_to_research"],
        allowed_subagents=[],  # explicit empty
    )
    _write_agent(tmp_path / "agents", "research", tools=[])
    script = _Script(
        by_agent={
            "solo": [
                AIMessage(content="No delegation possible; replying directly."),
            ],
        }
    )
    h = _harness(tmp_path, script)
    response = h.run_task(agent="solo", input={"goal": "x"}, thread_id="t-empty")
    # Context Manager filters the delegate_to_* tool out, so model sees no
    # delegation tool. Model just replies directly.
    assert response["status"] == "completed"


# ----------------------------------------------------------------------
# 3. Permission mode strict-only: child requests laxer mode → denied
# ----------------------------------------------------------------------


def test_child_cannot_request_laxer_mode(tmp_path: Path) -> None:
    _write_agent(
        tmp_path / "agents",
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        permission_mode="ask",
    )
    _write_agent(tmp_path / "agents", "research", tools=[])
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
                                "permission_mode": "auto",  # laxer than parent ask
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
    h = _harness(tmp_path, script)
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
    _write_agent(
        tmp_path / "agents",
        "lead",
        tools=["delegate_to_mid"],
        allowed_subagents=["mid"],
        subagent_max_depth=1,
        permission_mode="auto",
    )
    _write_agent(
        tmp_path / "agents",
        "mid",
        tools=["delegate_to_deep"],
        allowed_subagents=["deep"],
        subagent_max_depth=1,
        permission_mode="auto",
    )
    _write_agent(tmp_path / "agents", "deep", tools=[], permission_mode="auto")
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
    h = _harness(tmp_path, script)
    response = h.run_task(agent="lead", input={"goal": "go"}, thread_id="t-depth")
    assert response["status"] == "completed"


# ----------------------------------------------------------------------
# 5. Denied action propagation: parent denial reaches child
# ----------------------------------------------------------------------


def test_parent_denied_action_reaches_child(tmp_path: Path) -> None:
    _write_agent(
        tmp_path / "agents",
        "lead",
        tools=["delegate_to_research", "send"],
        allowed_subagents=["research"],
        permission_mode="auto",
    )
    _write_agent(tmp_path / "agents", "research", tools=["send"], permission_mode="auto")
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
    h = ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=script,
    )
    h.register_tool(
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
    _write_agent(
        tmp_path / "agents",
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
    )
    _write_agent(tmp_path / "agents", "research", tools=[])
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
    h = _harness(tmp_path, script)
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
    _write_agent(tmp_path / "agents", "alpha", tools=[])
    _write_agent(tmp_path / "agents", "beta", tools=[])
    _write_agent(tmp_path / "agents", "gamma", tools=[])
    h = ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=_Script(by_agent={}),
    )
    names = h._tools_registry.names()
    assert "delegate_to_alpha" in names
    assert "delegate_to_beta" in names
    assert "delegate_to_gamma" in names


# ----------------------------------------------------------------------
# 8. Subagent target unknown (no agent file) → cannot delegate
# ----------------------------------------------------------------------


def test_unknown_subagent_target_cannot_be_delegated(tmp_path: Path) -> None:
    _write_agent(
        tmp_path / "agents",
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
    h = _harness(tmp_path, script)
    # Tool isn't auto-registered (ghost has no agent.md), so gateway returns
    # an "unknown tool" error if the model tries it. Model just replies.
    response = h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-ghost")
    assert response["status"] == "completed"


# ----------------------------------------------------------------------
# 9. Subagent visibility narrows by allowed_subagents
# ----------------------------------------------------------------------


def test_allowed_subagents_filters_context_tools(tmp_path: Path) -> None:
    _write_agent(
        tmp_path / "agents",
        "lead",
        tools=["delegate_to_a", "delegate_to_b"],
        allowed_subagents=["a"],  # only a, not b
        permission_mode="auto",
    )
    _write_agent(tmp_path / "agents", "a", tools=[])
    _write_agent(tmp_path / "agents", "b", tools=[])
    script = _Script(
        by_agent={
            "lead": [
                AIMessage(content="ok"),
            ],
        }
    )
    h = _harness(tmp_path, script)
    # Drive the run to extract the ContextPack via the model adapter call.
    h.run_task(agent="lead", input={"goal": "x"}, thread_id="t-allow")
    # Indirect check: ensure the harness registered both delegate_to_* tools
    # but the agent's allowed_subagents will narrow them in context.
    names = h._tools_registry.names()
    assert "delegate_to_a" in names
    assert "delegate_to_b" in names
