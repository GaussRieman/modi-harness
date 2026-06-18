"""Agent and project discovery public API."""

from .config import load_discovery_config, locate_project
from .registry import AgentRegistry, discover_agents
from .types import (
    AgentDescriptor,
    AgentDiscoveryConfig,
    AgentSourceKind,
    DiscoveryResult,
    ProjectLocation,
    ResolutionReport,
)

__all__ = [
    "AgentDescriptor",
    "AgentDiscoveryConfig",
    "AgentRegistry",
    "AgentSourceKind",
    "DiscoveryResult",
    "ProjectLocation",
    "ResolutionReport",
    "discover_agents",
    "load_discovery_config",
    "locate_project",
]
