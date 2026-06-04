"""Tests for the ``modi plugins list`` CLI subcommand (V0.5)."""

from __future__ import annotations

from typing import Any

import pytest

from modi_harness import ModiAgent
from modi_harness.plugins import PluginInfo, PluginLoadError
from modi_harness.types import ToolBinding


def _sample_plugin_info() -> PluginInfo:
    """Build a V0.5 PluginInfo with one agent and one kernel tool."""
    spec = {
        "name": "fake_tool",
        "description": "x",
        "input_schema": {},
        "risk_level": "L1",
    }
    handler = lambda **_: None  # noqa: E731
    return PluginInfo(
        name="test-plugin",
        agents=[
            ModiAgent(name="sample-agent", description="d", instruction="reply")
        ],
        kernel_tools=[ToolBinding(spec=spec, handler=handler)],
        source="entry_point:fake-pkg v1.0.0",
    )


def test_list_no_plugins(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from modi_harness.__main__ import _cmd_plugins_list

    monkeypatch.setattr("modi_harness.plugins.discover_plugins", lambda: [])
    rc = _cmd_plugins_list()

    captured = capsys.readouterr()
    assert rc == 0
    assert "No plugins discovered" in captured.out


def test_list_with_plugins(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from modi_harness.__main__ import _cmd_plugins_list

    info = _sample_plugin_info()
    monkeypatch.setattr("modi_harness.plugins.discover_plugins", lambda: [info])

    rc = _cmd_plugins_list()
    captured = capsys.readouterr()

    assert rc == 0
    assert "test-plugin" in captured.out
    assert "entry_point:fake-pkg v1.0.0" in captured.out
    # Agent / kernel-tool counts and names from the manifest
    assert "agents:" in captured.out
    assert "sample-agent" in captured.out
    assert "kernel_tools:" in captured.out
    assert "fake_tool" in captured.out
    # Summary line
    assert "1 plugin" in captured.out
    assert "1 agent" in captured.out
    assert "1 tool" in captured.out


def test_list_propagates_load_error(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from modi_harness.__main__ import _cmd_plugins_list

    def boom() -> Any:
        raise PluginLoadError("test", "src", "boom")

    monkeypatch.setattr("modi_harness.plugins.discover_plugins", boom)

    rc = _cmd_plugins_list()
    captured = capsys.readouterr()

    assert rc == 1
    assert "boom" in captured.err
