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


_REQUIRED_TOOL_SPEC_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "input_schema",
    "risk_level",
)


def _validate_plugin_dict(d: dict[str, Any], source: str) -> PluginInfo:
    """Validate a raw dict returned by a plugin and produce a normalized
    :class:`PluginInfo`.

    Args:
        d: The dict returned by the plugin's ``get_plugin`` callable.
        source: Provenance string used in error messages and stored on the
            resulting :class:`PluginInfo`.

    Returns:
        A :class:`PluginInfo` with missing optional fields filled in
        (``None`` for paths, ``[]`` for tools).

    Raises:
        PluginLoadError: If the dict violates any of the validation rules
            documented in the V0.4c spec §3.4.
    """
    if not isinstance(d, dict):
        raise PluginLoadError(
            "<unknown>",
            source,
            f"plugin payload must be a dict, got {type(d).__name__}",
        )

    raw_name = d.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise PluginLoadError(
            raw_name if isinstance(raw_name, str) and raw_name else "<unknown>",
            source,
            "missing or empty 'name' field (must be a non-empty string)",
        )
    name: str = raw_name

    agents_dir = _coerce_optional_dir(d.get("agents_dir"), name, source, key="agents_dir")
    skills_dir = _coerce_optional_dir(d.get("skills_dir"), name, source, key="skills_dir")
    tools = _validate_tools(d.get("tools"), name, source)

    return PluginInfo(
        name=name,
        agents_dir=agents_dir,
        skills_dir=skills_dir,
        tools=tools,
        source=source,
    )


def _coerce_optional_dir(
    value: Any, plugin_name: str, source: str, *, key: str
) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        path = value
    elif isinstance(value, str):
        path = Path(value)
    else:
        raise PluginLoadError(
            plugin_name,
            source,
            f"{key!s} must be a Path or string, got {type(value).__name__}",
        )
    if not path.exists():
        raise PluginLoadError(
            plugin_name,
            source,
            f"{key!s} does not exist: {path}",
        )
    if not path.is_dir():
        raise PluginLoadError(
            plugin_name,
            source,
            f"{key!s} is not a directory: {path}",
        )
    return path


def _validate_tools(
    raw: Any, plugin_name: str, source: str
) -> list[tuple[dict[str, Any], Callable[..., Any]]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PluginLoadError(
            plugin_name,
            source,
            f"'tools' must be a list of (spec_dict, handler) tuples, got {type(raw).__name__}",
        )

    out: list[tuple[dict[str, Any], Callable[..., Any]]] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise PluginLoadError(
                plugin_name,
                source,
                f"'tools[{idx}]' must be a 2-tuple of (spec_dict, handler)",
            )
        spec, handler = entry
        if not isinstance(spec, dict):
            raise PluginLoadError(
                plugin_name,
                source,
                f"'tools[{idx}]' spec must be a dict, got {type(spec).__name__}",
            )
        if not callable(handler):
            raise PluginLoadError(
                plugin_name,
                source,
                f"'tools[{idx}]' handler must be callable, got {type(handler).__name__}",
            )
        for field in _REQUIRED_TOOL_SPEC_FIELDS:
            if field not in spec:
                raise PluginLoadError(
                    plugin_name,
                    source,
                    f"'tools[{idx}]' spec missing required field {field!r}",
                )
        out.append((spec, handler))
    return out


__all__ = [
    "PluginInfo",
    "PluginLoadError",
    "_validate_plugin_dict",
]
