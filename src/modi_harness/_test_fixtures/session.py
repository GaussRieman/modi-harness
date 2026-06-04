"""Test fixture: ``make_session(...)`` for constructing a wired-up ModiSession.

Encapsulates the two-stage (ModiHarness, ModiSession) construction so tests
do not repeat boilerplate. Used across tests/* in V0.5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from .. import ModiAgent, ModiHarness, ModiSession
from ..types import ToolBinding


def make_session(
    tmp_path: Path,
    *,
    chat_model: BaseChatModel,
    agents: list[ModiAgent] | None = None,
    agent_files: dict[str, str] | None = None,
    tools: list[Any] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    **session_opts: Any,
) -> ModiSession:
    """Build a ModiSession with sensible defaults for tests.

    Args:
        tmp_path: pytest tmp_path; used for workspace/memory roots.
        chat_model: required, injected.
        agents: explicit ModiAgent list. Mutually exclusive with agent_files.
        agent_files: ``{"name": "<full markdown text>"}`` — each written to
            tmp_path/agents/<name>.md and loaded via ModiAgent.from_markdown.
        tools: agent-scoped tools (ToolBinding or (spec, handler) tuples)
            attached to every agent loaded from agent_files.
        checkpointer: defaults to MemorySaver().
        session_opts: forwarded to ModiSession (project_root, max_steps, ...).
    """
    if agents is not None and agent_files is not None:
        raise ValueError("pass either `agents` or `agent_files`, not both")

    if agent_files:
        dir_ = tmp_path / "agents"
        dir_.mkdir(parents=True, exist_ok=True)
        for name, body in agent_files.items():
            (dir_ / f"{name}.md").write_text(body)
        normalized_tools = (
            [ToolBinding.from_tuple(t) for t in tools] if tools else None
        )
        agents = [
            ModiAgent.from_markdown(p, tools=normalized_tools)
            for p in sorted(dir_.glob("*.md"))
        ]

    if not agents:
        raise ValueError("at least one agent is required")

    harness = ModiHarness(chat_model=chat_model)
    return ModiSession(
        harness=harness,
        agents=agents,
        checkpointer=checkpointer or MemorySaver(),
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        **session_opts,
    )


__all__ = ["make_session"]
