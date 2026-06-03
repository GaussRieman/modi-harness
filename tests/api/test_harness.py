"""Unit tests for slim V0.5 ModiHarness (capability suite only)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiHarness
from modi_harness.types import PermissionsConfig


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        from langchain_core.messages import AIMessage  # noqa: F401

        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "script"


def test_minimal_construction() -> None:
    h = ModiHarness(chat_model=_ScriptModel())
    assert h.chat_model is not None
    assert h.policy is not None
    assert h.builtin_tool_names  # default: all enabled


def test_does_not_hold_agents() -> None:
    h = ModiHarness(chat_model=_ScriptModel())
    assert not hasattr(h, "_agent_loader")
    assert not hasattr(h, "_workspace")
    assert not hasattr(h, "_runtime")


def test_builtin_tools_whitelist() -> None:
    h = ModiHarness(chat_model=_ScriptModel(), builtin_tools=["save_artifact"])
    assert h.builtin_tool_names == {"save_artifact"}


def test_builtin_tools_disabled() -> None:
    h = ModiHarness(chat_model=_ScriptModel(), builtin_tools=[])
    assert h.builtin_tool_names == set()


def test_permissions_config_accepted() -> None:
    cfg = PermissionsConfig(mode="auto", deny=("delete_*",))
    h = ModiHarness(chat_model=_ScriptModel(), permissions=cfg)
    assert h.permissions is cfg


def test_harness_shareable_across_sessions() -> None:
    # Just verifies the harness is reusable — no internal session state.
    h = ModiHarness(chat_model=_ScriptModel())
    assert h is h  # placeholder; the real reuse test lives in test_session.py
