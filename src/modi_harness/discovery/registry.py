"""Provenance-preserving Agent source discovery and name resolution."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..api.agent import ModiAgent
from ..api.errors import AgentFactoryError, AgentResolutionError
from ..plugins import PluginInfo, discover_plugins
from ..types import ToolBinding
from .config import load_discovery_config
from .factories import load_agent_package
from .types import (
    AgentDescriptor,
    AgentSourceKind,
    DiscoveryResult,
    ResolutionReport,
)

_SOURCE_ORDER = {"explicit": 0, "project": 1, "plugin": 2, "user": 3}


class AgentRegistry:
    """Immutable searchable index of discovered Agent descriptors."""

    def __init__(
        self,
        descriptors: Iterable[AgentDescriptor],
        *,
        explicit_requested: bool = False,
    ) -> None:
        self._descriptors = tuple(sorted(descriptors, key=_descriptor_sort_key))
        self._explicit_requested = explicit_requested

    def list(self) -> list[AgentDescriptor]:
        return list(self._descriptors)

    def resolve(self, query: str) -> AgentDescriptor:
        report = self.explain(query)
        if report.selected is not None:
            return report.selected
        if not report.candidates:
            available = sorted({item.name for item in self._descriptors})
            raise AgentResolutionError(query, (), available=available)
        raise AgentResolutionError(
            query,
            tuple(item.qualified_name for item in report.candidates),
        )

    def explain(self, query: str) -> ResolutionReport:
        qualified = [item for item in self._descriptors if item.qualified_name == query]
        if qualified:
            return ResolutionReport(query=query, candidates=tuple(qualified), selected=qualified[0])
        if _looks_qualified(query):
            return ResolutionReport(query=query, candidates=(), selected=None)

        candidates = tuple(item for item in self._descriptors if item.name == query)
        if len(candidates) == 1:
            return ResolutionReport(query=query, candidates=candidates, selected=candidates[0])
        if self._explicit_requested:
            explicit = tuple(item for item in candidates if item.source_kind == "explicit")
            if len(explicit) == 1:
                return ResolutionReport(
                    query=query,
                    candidates=candidates,
                    selected=explicit[0],
                    shadowed=tuple(item for item in candidates if item is not explicit[0]),
                )
        return ResolutionReport(query=query, candidates=candidates, selected=None)


def discover_agents(
    *,
    cwd: Path | str | None = None,
    explicit_dirs: Iterable[Path | str] = (),
    user_dir: Path | str | None = None,
    plugins: list[PluginInfo] | None = None,
) -> DiscoveryResult:
    """Discover all configured Agent sources without constructing a Session."""
    config = load_discovery_config(start=cwd)
    descriptors: list[AgentDescriptor] = []
    notices: list[str] = []
    seen_paths: set[Path] = set()
    explicit_paths = tuple(Path(path).expanduser().resolve() for path in explicit_dirs)
    for path in explicit_paths:
        if not path.is_dir():
            raise AgentResolutionError(str(path), (), detail="explicit Agent directory missing")
        descriptors.extend(
            _scan_directory(path, "explicit", str(path), trusted_factories=True, seen=seen_paths)
        )

    for path in config.dirs:
        descriptors.extend(
            _scan_directory(
                path,
                "project",
                f"configured:{path}",
                trusted_factories=config.trusted_project_factories,
                seen=seen_paths,
            )
        )
    if config.include_conventional:
        for path in config.conventional_dirs:
            if path.is_dir():
                descriptors.extend(
                    _scan_directory(
                        path,
                        "project",
                        f"conventional:{path}",
                        trusted_factories=config.trusted_project_factories,
                        seen=seen_paths,
                    )
                )

    kernel_tools: list[ToolBinding] = []
    if config.include_plugins:
        discovered_plugins = plugins if plugins is not None else discover_plugins()
        for plugin in discovered_plugins:
            kernel_tools.extend(plugin.get("kernel_tools", []))
            plugin_name = plugin["name"]
            for agent in plugin.get("agents", []):
                descriptors.append(
                    _descriptor(
                        agent=agent,
                        source_kind="plugin",
                        source_id=plugin.get("source", plugin_name),
                        path=None,
                        plugin_name=plugin_name,
                        executable_factory=True,
                    )
                )

    if config.include_user:
        resolved_user_dir = Path(user_dir or "~/.modi/agents").expanduser().resolve()
        if resolved_user_dir.is_dir():
            user_descriptors, user_notices = _scan_user_directory(resolved_user_dir, seen_paths)
            descriptors.extend(user_descriptors)
            notices.extend(user_notices)

    return DiscoveryResult(
        registry=AgentRegistry(descriptors, explicit_requested=bool(explicit_paths)),
        kernel_tools=tuple(kernel_tools),
        project_root=config.project_root,
        config_path=config.config_path,
        notices=tuple(notices),
    )


def _scan_directory(
    directory: Path,
    source_kind: AgentSourceKind,
    source_id: str,
    *,
    trusted_factories: bool,
    seen: set[Path],
) -> list[AgentDescriptor]:
    descriptors: list[AgentDescriptor] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".md":
            canonical = entry.resolve()
            if canonical in seen:
                continue
            seen.add(canonical)
            descriptors.append(
                _descriptor(
                    agent=ModiAgent.from_markdown(entry),
                    source_kind=source_kind,
                    source_id=source_id,
                    path=canonical,
                    plugin_name=None,
                    executable_factory=False,
                )
            )
            continue
        if not entry.is_dir():
            continue
        manifest = entry / "agent.toml"
        markdown = entry / "agent.md"
        if manifest.is_file():
            if not trusted_factories:
                raise AgentFactoryError(entry, "project Agent factories are not trusted")
            canonical = entry.resolve()
            if canonical in seen:
                continue
            seen.add(canonical)
            descriptors.append(
                _descriptor(
                    agent=load_agent_package(entry),
                    source_kind=source_kind,
                    source_id=source_id,
                    path=canonical,
                    plugin_name=None,
                    executable_factory=True,
                )
            )
        elif markdown.is_file():
            canonical = markdown.resolve()
            if canonical in seen:
                continue
            seen.add(canonical)
            descriptors.append(
                _descriptor(
                    agent=ModiAgent.from_markdown(markdown),
                    source_kind=source_kind,
                    source_id=source_id,
                    path=canonical,
                    plugin_name=None,
                    executable_factory=False,
                )
            )
    return descriptors


def _scan_user_directory(
    directory: Path,
    seen: set[Path],
) -> tuple[list[AgentDescriptor], list[str]]:
    descriptors: list[AgentDescriptor] = []
    notices: list[str] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".md":
            descriptors.extend(
                _scan_directory(directory, "user", str(directory), trusted_factories=False, seen=seen)
            )
            break
        if entry.is_dir() and (entry / "agent.toml").is_file():
            markdown = entry / "agent.md"
            notices.append(f"ignored untrusted user factory: {entry}")
            if markdown.is_file():
                canonical = markdown.resolve()
                if canonical not in seen:
                    seen.add(canonical)
                    descriptors.append(
                        _descriptor(
                            agent=ModiAgent.from_markdown(markdown),
                            source_kind="user",
                            source_id=str(directory),
                            path=canonical,
                            plugin_name=None,
                            executable_factory=False,
                        )
                    )
        elif entry.is_dir() and (entry / "agent.md").is_file():
            canonical = (entry / "agent.md").resolve()
            if canonical not in seen:
                seen.add(canonical)
                descriptors.append(
                    _descriptor(
                        agent=ModiAgent.from_markdown(entry / "agent.md"),
                        source_kind="user",
                        source_id=str(directory),
                        path=canonical,
                        plugin_name=None,
                        executable_factory=False,
                    )
                )
    return descriptors, notices


def _descriptor(
    *,
    agent: ModiAgent,
    source_kind: AgentSourceKind,
    source_id: str,
    path: Path | None,
    plugin_name: str | None,
    executable_factory: bool,
) -> AgentDescriptor:
    if source_kind == "plugin":
        qualified = f"plugin:{plugin_name}/{agent.name}"
    else:
        qualified = f"{source_kind}:{agent.name}"
    return AgentDescriptor(
        name=agent.name,
        qualified_name=qualified,
        source_kind=source_kind,
        source_id=source_id,
        path=path,
        plugin_name=plugin_name,
        executable_factory=executable_factory,
        agent=agent,
    )


def _descriptor_sort_key(item: AgentDescriptor) -> tuple[int, str, str, str]:
    return (
        _SOURCE_ORDER[item.source_kind],
        item.source_id,
        item.name,
        item.qualified_name,
    )


def _looks_qualified(query: str) -> bool:
    return query.startswith(("explicit:", "project:", "plugin:", "user:"))


__all__ = ["AgentRegistry", "discover_agents"]
