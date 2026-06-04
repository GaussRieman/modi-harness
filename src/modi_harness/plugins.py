"""Plugin discovery for Modi Harness.

V0.4c plugin system: third-party packages can publish ``pip install``-able
plugins that contribute agents, skills, and tools. Discovery is performed via
the standard ``importlib.metadata`` entry-point mechanism under the group
``modi_harness.plugins``.

See ``docs/superpowers/specs/2026-05-29-v0.4c-plugin-system-design.md`` for the
full design.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, TypedDict


class PluginInfo(TypedDict):
    """V0.5 plugin manifest.

    Fields:
        name: Plugin identifier.
        agents: list[ModiAgent] the plugin contributes. The plugin runs its
            own ModiAgent.load_dir / from_markdown internally — modi never
            reads from a plugin's filesystem.
        kernel_tools: list[ToolBinding] contributing new kernel-scoped tools.
        source: Provenance string.
    """

    name: str
    agents: list  # list[ModiAgent] — untyped to avoid an import cycle at module load
    kernel_tools: list  # list[ToolBinding]
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


def _validate_plugin_dict(d: dict[str, Any], source: str) -> PluginInfo:
    from .api.agent import ModiAgent
    from .types import ToolBinding

    if not isinstance(d, dict):
        raise PluginLoadError(
            "<unknown>", source, f"plugin payload must be a dict, got {type(d).__name__}"
        )

    raw_name = d.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise PluginLoadError(
            raw_name if isinstance(raw_name, str) and raw_name else "<unknown>",
            source,
            "missing or empty 'name' field (must be a non-empty string)",
        )
    name: str = raw_name

    raw_agents = d.get("agents", [])
    if not isinstance(raw_agents, list):
        raise PluginLoadError(
            name, source, f"'agents' must be a list, got {type(raw_agents).__name__}"
        )
    for i, a in enumerate(raw_agents):
        if not isinstance(a, ModiAgent):
            raise PluginLoadError(
                name, source, f"agents[{i}] must be a ModiAgent, got {type(a).__name__}"
            )

    raw_tools = d.get("kernel_tools", [])
    if not isinstance(raw_tools, list):
        raise PluginLoadError(
            name, source, f"'kernel_tools' must be a list, got {type(raw_tools).__name__}"
        )
    normalized_tools: list = []
    for i, t in enumerate(raw_tools):
        try:
            normalized_tools.append(ToolBinding.from_tuple(t))
        except Exception as exc:
            raise PluginLoadError(
                name,
                source,
                f"kernel_tools[{i}] is not a ToolBinding or (spec, handler): {exc}",
            ) from exc

    return PluginInfo(
        name=name,
        agents=raw_agents,
        kernel_tools=normalized_tools,
        source=source,
    )


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
