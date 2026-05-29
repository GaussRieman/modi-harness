"""Tests for ModiHarness <-> plugin integration (V0.4c, N1)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from modi_harness import ModiHarness
from modi_harness import plugins as plugins_module
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


def test_plugin_agent_loadable() -> None:
    info = _sample_plugin_info()
    harness = ModiHarness(plugins=[info], auto_discover_plugins=False)

    names = harness._agent_loader.list_agent_names()
    assert "sample-agent" in names


def test_plugin_tool_registered() -> None:
    info = _sample_plugin_info()
    harness = ModiHarness(plugins=[info], auto_discover_plugins=False)

    assert harness._tools_registry.has("sample_tool") is True


def test_no_plugins_when_disabled() -> None:
    harness = ModiHarness(auto_discover_plugins=False)

    # No plugin agents discovered.
    assert harness._agent_loader.list_agent_names() == []
    # No plugin-contributed tools registered. ``sample_tool`` is the
    # canonical sentinel from the fixture.
    assert harness._tools_registry.has("sample_tool") is False
    assert harness._plugins == []


def test_explicit_plugins_overrides_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``plugins=[]`` is passed explicitly, ``discover_plugins`` is not
    called, even if ``auto_discover_plugins`` defaults to True."""

    def boom() -> list[PluginInfo]:
        raise AssertionError(
            "discover_plugins should not be called when plugins is provided"
        )

    monkeypatch.setattr(plugins_module, "discover_plugins", boom)

    harness = ModiHarness(plugins=[], auto_discover_plugins=True)

    assert harness._plugins == []


def test_plugin_load_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode() -> list[PluginInfo]:
        raise PluginLoadError("borked", "entry_point:fake v0.0.0", "boom")

    monkeypatch.setattr(plugins_module, "discover_plugins", explode)

    with pytest.raises(PluginLoadError):
        ModiHarness(auto_discover_plugins=True)
