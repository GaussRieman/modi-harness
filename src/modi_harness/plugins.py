"""Plugin discovery for Modi Harness.

V0.4c plugin system: third-party packages can publish ``pip install``-able
plugins that contribute agents, skills, and tools. Discovery is performed via
the standard ``importlib.metadata`` entry-point mechanism under the group
``modi_harness.plugins``.

See ``docs/superpowers/specs/2026-05-29-v0.4c-plugin-system-design.md`` for the
full design.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict


class PluginInfo(TypedDict):
    """Normalized record describing a discovered (or explicitly provided) plugin.

    Fields:
        name: Plugin identifier as declared by the plugin author.
        agents_dir: Directory containing agent Markdown files, or ``None`` if
            the plugin contributes no agents.
        skills_dir: Directory containing skill packages, or ``None`` if the
            plugin contributes no skills.
        tools: List of ``(tool_spec_dict, handler)`` tuples to register.
        source: Provenance string used in error messages and ``modi plugins
            list`` output. Format: ``"entry_point:<dist_name> v<version>"`` for
            entry-point plugins, or ``"explicit"`` for plugins passed directly
            to ``ModiHarness``.
    """

    name: str
    agents_dir: Path | None
    skills_dir: Path | None
    tools: list[tuple[dict[str, Any], Callable[..., Any]]]
    source: str


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded, validated, or invoked.

    Carries the plugin name and source so the caller (or CLI) can present an
    actionable error. The original underlying exception, if any, is chained
    via ``__cause__``.
    """

    def __init__(self, plugin_name: str, source: str, message: str) -> None:
        self.plugin_name = plugin_name
        self.source = source
        self.message = message
        super().__init__(self._format())

    def _format(self) -> str:
        return f"Plugin '{self.plugin_name}' from {self.source} failed: {self.message}"

    def __str__(self) -> str:
        return self._format()


__all__ = ["PluginInfo", "PluginLoadError"]
