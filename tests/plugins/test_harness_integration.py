"""Tests for plugin manifest integration (V0.5 N3.1).

V0.5 plugins are self-contained: they contribute a list of ``ModiAgent``s and
a list of ``ToolBinding`` kernel tools. modi never scans a plugin's filesystem.
The old ``ModiHarness(plugins=, auto_discover_plugins=)`` API is gone; plugin
agents now feed into ``ModiSession``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from modi_harness.plugins import (
    PluginInfo,
    PluginLoadError,
    _validate_plugin_dict,
)

_SAMPLE_PLUGIN_DIR = Path(__file__).parent / "fixtures" / "sample_plugin"


def _load_sample_plugin_module() -> Any:
    """Load the sample_plugin fixture package by file path.

    Mirrors the loader in ``test_discovery.py`` so the two test modules stay
    independent. ``tests/`` is not a Python package, so we cannot rely on a
    normal import path.
    """
    if "modi_test_sample_plugin" in sys.modules:
        return sys.modules["modi_test_sample_plugin"]

    spec = importlib.util.spec_from_file_location(
        "modi_test_sample_plugin",
        _SAMPLE_PLUGIN_DIR / "__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["modi_test_sample_plugin"] = module
    spec.loader.exec_module(module)
    return module


def _sample_plugin_info() -> PluginInfo:
    """Build a validated ``PluginInfo`` from the sample fixture."""
    module = _load_sample_plugin_module()
    return _validate_plugin_dict(module.get_plugin(), source="explicit")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_plugin_info_has_v05_shape() -> None:
    info = _sample_plugin_info()
    assert info["name"] == "sample-plugin"
    assert len(info["agents"]) == 1
    assert info["agents"][0].name == "sample-agent"
    assert len(info["kernel_tools"]) == 1
    assert info["kernel_tools"][0].spec["name"] == "sample_tool"


def test_plugin_agents_feed_into_session(tmp_path: Path) -> None:
    """Plugin-contributed ModiAgents can be registered in a ModiSession."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from modi_harness import ModiHarness, ModiSession

    info = _sample_plugin_info()
    harness = ModiHarness(chat_model=FakeListChatModel(responses=["ok"]))
    session = ModiSession(
        harness=harness,
        agents=list(info["agents"]),
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
    )
    assert "sample-agent" in session.list_agents()


def test_plugin_validation_rejects_non_modiagent() -> None:
    with pytest.raises(PluginLoadError):
        _validate_plugin_dict(
            {"name": "bad", "agents": ["not-an-agent"], "kernel_tools": []},
            source="explicit",
        )


def test_plugin_validation_rejects_bad_kernel_tools() -> None:
    with pytest.raises(PluginLoadError):
        _validate_plugin_dict(
            {"name": "bad", "agents": [], "kernel_tools": [12345]},
            source="explicit",
        )


def test_plugin_load_error_on_missing_name() -> None:
    with pytest.raises(PluginLoadError):
        _validate_plugin_dict({"agents": [], "kernel_tools": []}, source="explicit")
