"""Project location and strict ``modi.toml`` parsing."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ..api.errors import ModiConfigError
from .types import AgentDiscoveryConfig, ProjectLocation

_TOP_LEVEL_KEYS = {"project", "agents"}
_PROJECT_KEYS = {"name"}
_AGENT_KEYS = {
    "dirs",
    "include_conventional",
    "include_plugins",
    "include_user",
    "trusted_project_factories",
}


def locate_project(start: Path | str | None = None) -> ProjectLocation:
    """Walk upward from *start* and return the nearest ``modi.toml`` project."""
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    origin = current
    while True:
        config_path = current / "modi.toml"
        if config_path.is_file():
            return ProjectLocation(project_root=current, config_path=config_path)
        if current.parent == current:
            return ProjectLocation(project_root=origin, config_path=None)
        current = current.parent


def load_discovery_config(
    location: ProjectLocation | None = None,
    *,
    start: Path | str | None = None,
) -> AgentDiscoveryConfig:
    """Load a validated Agent discovery config for a project location."""
    resolved = location or locate_project(start)
    if resolved.config_path is None:
        return AgentDiscoveryConfig(project_root=resolved.project_root, config_path=None)

    try:
        with resolved.config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ModiConfigError(resolved.config_path, str(exc)) from exc

    _reject_unknown(raw, _TOP_LEVEL_KEYS, resolved.config_path, "top level")
    project = _mapping(raw.get("project", {}), resolved.config_path, "project")
    agents = _mapping(raw.get("agents", {}), resolved.config_path, "agents")
    _reject_unknown(project, _PROJECT_KEYS, resolved.config_path, "project")
    _reject_unknown(agents, _AGENT_KEYS, resolved.config_path, "agents")

    project_name = _optional_string(project.get("name"), resolved.config_path, "project.name")
    raw_dirs = agents.get("dirs", [])
    if not isinstance(raw_dirs, list) or any(not isinstance(item, str) for item in raw_dirs):
        raise ModiConfigError(resolved.config_path, "agents.dirs must be a list of strings")

    dirs = tuple(_resolve_configured_dir(resolved, item) for item in raw_dirs)
    return AgentDiscoveryConfig(
        project_root=resolved.project_root,
        config_path=resolved.config_path,
        project_name=project_name,
        dirs=dirs,
        include_conventional=_boolean(agents, "include_conventional", True, resolved.config_path),
        include_plugins=_boolean(agents, "include_plugins", True, resolved.config_path),
        include_user=_boolean(agents, "include_user", True, resolved.config_path),
        trusted_project_factories=_boolean(
            agents, "trusted_project_factories", False, resolved.config_path
        ),
    )


def _resolve_configured_dir(location: ProjectLocation, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = location.project_root / path
    path = path.resolve()
    if not path.is_dir():
        assert location.config_path is not None
        raise ModiConfigError(location.config_path, f"configured Agent directory does not exist: {path}")
    return path


def _mapping(value: Any, path: Path, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModiConfigError(path, f"{key} must be a table")
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], path: Path, section: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ModiConfigError(path, f"unknown {section} key(s): {', '.join(unknown)}")


def _optional_string(value: Any, path: Path, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ModiConfigError(path, f"{key} must be a non-empty string")
    return value.strip()


def _boolean(table: dict[str, Any], key: str, default: bool, path: Path) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ModiConfigError(path, f"agents.{key} must be a boolean")
    return value


__all__ = ["load_discovery_config", "locate_project"]
