"""Tests for the `mode=` keyword on the Session API and `MODI_MODE` env.

The product surface accepts ``mode=`` as the canonical knob. V0.5 removed the
legacy ``permission_mode=`` keyword from ``ModiSession.run_task`` entirely
(the env-level ``MODI_PERMISSION_MODE`` alias is still honored by Settings).
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness._test_fixtures import make_session
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


def _session(tmp_path):
    return make_session(
        tmp_path,
        chat_model=_ScriptModel(script=[AIMessage(content="ok")]),
        agent_files={
            "demo": """---
name: demo
description: demo
---
Reply directly.
""",
        },
    )


def test_mode_keyword_seeds_state(tmp_path) -> None:
    """``mode='preview'`` must propagate to the run state's ``permission_mode``."""
    s = _session(tmp_path)
    response = s.run_task(agent="demo", input={"goal": "x"}, mode="preview")
    assert response["status"] == "completed"
    state = s.get_state(response["thread_id"])
    assert state is not None
    assert state["permission_mode"] == "preview"


def test_legacy_permission_mode_kwarg_removed(tmp_path) -> None:
    """V0.5 dropped the legacy ``permission_mode=`` kwarg; ``mode=`` is canonical."""
    s = _session(tmp_path)
    with pytest.raises(TypeError):
        s.run_task(agent="demo", input={"goal": "x"}, permission_mode="auto")


def test_modi_mode_env_wins_over_modi_permission_mode(monkeypatch) -> None:
    """``MODI_MODE`` must override ``MODI_PERMISSION_MODE`` in env collection."""
    # Settings._collect_env reads os.environ; monkeypatch isolates this test.
    monkeypatch.setenv("MODI_MODE", "preview")
    monkeypatch.setenv("MODI_PERMISSION_MODE", "trust")
    settings = Settings(_env_file=None)
    assert settings.runtime.permission_mode == "preview"
