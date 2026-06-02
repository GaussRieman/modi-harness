"""Tests for the `mode=` keyword on the Harness API and `MODI_MODE` env.

The product surface accepts ``mode=`` as the canonical knob; ``permission_mode=``
remains as a legacy alias for one minor release. When both are passed, the new
``mode=`` wins. Same precedence applies to ``MODI_MODE`` vs ``MODI_PERMISSION_MODE``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiHarness
from modi_harness.config.settings import Settings


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "script"


def _harness(tmp_path: Path) -> ModiHarness:
    agents = tmp_path / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "demo.md").write_text(
        """---
name: demo
description: demo
---
Reply directly.
"""
    )
    return ModiHarness(
        agents_dir=agents,
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
    )


def test_mode_keyword_seeds_state(tmp_path: Path) -> None:
    """``mode='preview'`` must propagate to the run state's ``permission_mode``."""
    h = _harness(tmp_path)
    response = h.run_task(agent="demo", input={"goal": "x"}, mode="preview")
    assert response["status"] == "completed"
    state = h.get_state(response["thread_id"])
    assert state is not None
    assert state["permission_mode"] == "preview"


def test_mode_wins_over_permission_mode(tmp_path: Path) -> None:
    """When both are passed, the new ``mode=`` keyword takes precedence."""
    h = _harness(tmp_path)
    response = h.run_task(
        agent="demo",
        input={"goal": "x"},
        mode="preview",
        permission_mode="auto",  # legacy keyword should lose
    )
    state = h.get_state(response["thread_id"])
    assert state is not None
    assert state["permission_mode"] == "preview"


def test_modi_mode_env_wins_over_modi_permission_mode(monkeypatch) -> None:
    """``MODI_MODE`` must override ``MODI_PERMISSION_MODE`` in env collection."""
    # Settings._collect_env reads os.environ; monkeypatch isolates this test.
    monkeypatch.setenv("MODI_MODE", "preview")
    monkeypatch.setenv("MODI_PERMISSION_MODE", "trust")
    settings = Settings(_env_file=None)
    assert settings.runtime.permission_mode == "preview"
