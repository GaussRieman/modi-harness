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
from importlib.metadata import entry_points
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
    "discover_plugins",
]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


_ENTRY_POINT_GROUP = "modi_harness.plugins"


def _entry_point_source(ep: Any) -> str:
    """Build the ``source`` string for a plugin discovered via an entry point.

    Format: ``"entry_point:<dist_name> v<version>"``. If the distribution
    metadata is missing (some test scenarios, or unusual install layouts),
    fall back to ``"entry_point:<ep.value>"``.
    """
    dist = getattr(ep, "dist", None)
    if dist is not None:
        dist_name = getattr(dist, "name", None)
        dist_version = getattr(dist, "version", None)
        if dist_name and dist_version:
            return f"entry_point:{dist_name} v{dist_version}"
    value = getattr(ep, "value", None) or getattr(ep, "name", "<unknown>")
    return f"entry_point:{value}"


def discover_plugins() -> list[PluginInfo]:
    """Scan installed packages for plugins under ``modi_harness.plugins``.

    For each entry point registered in the ``modi_harness.plugins`` group:

    1. Load the target callable. ``ImportError``, ``ModuleNotFoundError``,
       and ``AttributeError`` are converted to :class:`PluginLoadError`.
    2. Invoke the callable with no arguments. Any exception is converted to
       :class:`PluginLoadError` with the original chained as ``__cause__``.
    3. Validate the returned dict via :func:`_validate_plugin_dict` and
       attach the entry-point provenance as ``source``.

    Returns:
        A list of validated :class:`PluginInfo` records in the order
        ``importlib.metadata.entry_points`` yields them.

    Raises:
        PluginLoadError: If any single plugin fails to load, call, or
            validate. Discovery is fail-fast: subsequent entry points are
            not processed.
    """
    discovered: list[PluginInfo] = []
    for ep in entry_points(group=_ENTRY_POINT_GROUP):
        plugin_name = getattr(ep, "name", "<unknown>")
        source = _entry_point_source(ep)

        try:
            loaded = ep.load()
        except (ImportError, ModuleNotFoundError, AttributeError) as exc:
            raise PluginLoadError(
                plugin_name,
                source,
                f"failed to import entry point: {exc}",
            ) from exc

        if not callable(loaded):
            raise PluginLoadError(
                plugin_name,
                source,
                f"entry point did not resolve to a callable, got {type(loaded).__name__}",
            )

        try:
            payload = loaded()
        except Exception as exc:
            raise PluginLoadError(
                plugin_name,
                source,
                f"plugin callable raised {type(exc).__name__}: {exc}",
            ) from exc

        discovered.append(_validate_plugin_dict(payload, source))

    return discovered
