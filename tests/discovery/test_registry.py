"""Agent registry source merge, provenance, and resolution tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness import ModiAgent
from modi_harness.api.errors import AgentResolutionError
from modi_harness.discovery import discover_agents
from modi_harness.types import ToolBinding


def _write_agent(directory: Path, name: str, *, description: str = "demo") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nReply clearly.\n",
        encoding="utf-8",
    )
    return path


def _config(root: Path, text: str = "") -> None:
    (root / "modi.toml").write_text(text, encoding="utf-8")


def test_discovers_configured_and_conventional_project_agents(tmp_path: Path) -> None:
    configured = tmp_path / "configured"
    conventional = tmp_path / "agents"
    _write_agent(configured, "configured-agent")
    _write_agent(conventional, "conventional-agent")
    _config(
        tmp_path,
        "[agents]\ndirs = ['configured']\ninclude_plugins = false\ninclude_user = false\n",
    )

    result = discover_agents(cwd=tmp_path, plugins=[])

    assert [item.name for item in result.registry.list()] == [
        "configured-agent",
        "conventional-agent",
    ]
    resolved = result.registry.resolve("configured-agent")
    assert resolved.qualified_name == "project:configured-agent"
    assert resolved.source_kind == "project"
    assert resolved.path == (configured / "configured-agent.md").resolve()


def test_registry_preserves_plugin_kernel_tools(tmp_path: Path) -> None:
    _config(tmp_path, "[agents]\ninclude_user = false\n")
    agent = ModiAgent(name="plugin-agent", description="d", instruction="i")
    binding = ToolBinding(
        spec={
            "name": "plugin_tool",
            "description": "d",
            "input_schema": {},
            "risk_level": "L0",
        },
        handler=lambda: {"ok": True},
    )
    plugins = [{
        "name": "demo-plugin",
        "agents": [agent],
        "kernel_tools": [binding],
        "source": "entry_point:demo 1.0",
    }]

    result = discover_agents(cwd=tmp_path, plugins=plugins)  # type: ignore[arg-type]

    descriptor = result.registry.resolve("plugin-agent")
    assert descriptor.qualified_name == "plugin:demo-plugin/plugin-agent"
    assert descriptor.plugin_name == "demo-plugin"
    assert result.kernel_tools == (binding,)


def test_ambiguous_name_requires_qualified_resolution(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "shared", description="project")
    _write_agent(tmp_path / "user", "shared", description="user")
    _config(tmp_path, "[agents]\ninclude_plugins = false\n")

    result = discover_agents(cwd=tmp_path, user_dir=tmp_path / "user", plugins=[])

    with pytest.raises(AgentResolutionError, match="ambiguous") as exc_info:
        result.registry.resolve("shared")
    assert "project:shared" in str(exc_info.value)
    assert "user:shared" in str(exc_info.value)
    assert result.registry.resolve("project:shared").source_kind == "project"
    assert result.registry.resolve("user:shared").source_kind == "user"


def test_explicit_directory_wins_unqualified_query_and_reports_shadowed(
    tmp_path: Path,
) -> None:
    _write_agent(tmp_path / "agents", "shared", description="project")
    _write_agent(tmp_path / "explicit", "shared", description="explicit")
    _config(tmp_path, "[agents]\ninclude_plugins = false\ninclude_user = false\n")

    result = discover_agents(
        cwd=tmp_path,
        explicit_dirs=[tmp_path / "explicit"],
        plugins=[],
    )
    report = result.registry.explain("shared")

    assert report.selected is not None
    assert report.selected.source_kind == "explicit"
    assert [item.source_kind for item in report.shadowed] == ["project"]


def test_same_directory_discovered_by_config_and_convention_is_deduped(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "one")
    _config(
        tmp_path,
        "[agents]\ndirs = ['agents']\ninclude_plugins = false\ninclude_user = false\n",
    )

    result = discover_agents(cwd=tmp_path, plugins=[])

    assert [item.name for item in result.registry.list()] == ["one"]


def test_missing_query_lists_available_agents(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "known")
    _config(tmp_path, "[agents]\ninclude_plugins = false\ninclude_user = false\n")
    registry = discover_agents(cwd=tmp_path, plugins=[]).registry

    with pytest.raises(AgentResolutionError, match="available: known"):
        registry.resolve("missing")


def test_listing_is_deterministic(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "zeta")
    _write_agent(tmp_path / "agents", "alpha")
    _config(tmp_path, "[agents]\ninclude_plugins = false\ninclude_user = false\n")

    names = [item.name for item in discover_agents(cwd=tmp_path, plugins=[]).registry.list()]

    assert names == ["alpha", "zeta"]
