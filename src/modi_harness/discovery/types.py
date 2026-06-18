"""Value types for project and Agent discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..api.agent import ModiAgent
from ..types import ToolBinding

if TYPE_CHECKING:
    from .registry import AgentRegistry

AgentSourceKind = Literal["explicit", "project", "plugin", "user"]


@dataclass(frozen=True)
class ProjectLocation:
    """Resolved project root and optional ``modi.toml`` path."""

    project_root: Path
    config_path: Path | None


@dataclass(frozen=True)
class AgentDiscoveryConfig:
    """Strictly parsed ``[agents]`` discovery configuration."""

    project_root: Path
    config_path: Path | None
    project_name: str | None = None
    dirs: tuple[Path, ...] = ()
    include_conventional: bool = True
    include_plugins: bool = True
    include_user: bool = True
    trusted_project_factories: bool = False

    @property
    def conventional_dirs(self) -> tuple[Path, Path]:
        return (
            self.project_root / "agents",
            self.project_root / ".modi" / "agents",
        )


@dataclass(frozen=True)
class AgentDescriptor:
    """A discovered Agent plus stable source provenance."""

    name: str
    qualified_name: str
    source_kind: AgentSourceKind
    source_id: str
    path: Path | None
    plugin_name: str | None
    executable_factory: bool
    agent: ModiAgent


@dataclass(frozen=True)
class ResolutionReport:
    """Explain how an unqualified or qualified Agent name resolves."""

    query: str
    candidates: tuple[AgentDescriptor, ...]
    selected: AgentDescriptor | None
    shadowed: tuple[AgentDescriptor, ...] = ()


@dataclass(frozen=True)
class DiscoveryResult:
    """Complete discovery result needed for session construction."""

    registry: AgentRegistry
    kernel_tools: tuple[ToolBinding, ...]
    project_root: Path
    config_path: Path | None
    notices: tuple[str, ...] = ()


__all__ = [
    "AgentDescriptor",
    "AgentDiscoveryConfig",
    "AgentSourceKind",
    "DiscoveryResult",
    "ProjectLocation",
    "ResolutionReport",
]
