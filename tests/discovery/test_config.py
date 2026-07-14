"""Project locator and strict ``modi.toml`` parsing tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.api.errors import ModiConfigError
from modi_harness.discovery import load_discovery_config, locate_project


def test_locate_project_walks_to_nearest_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "src" / "feature"
    nested.mkdir(parents=True)
    (root / "modi.toml").write_text("[project]\nname = 'root'\n", encoding="utf-8")

    location = locate_project(nested)

    assert location.project_root == root.resolve()
    assert location.config_path == (root / "modi.toml").resolve()


def test_locate_project_prefers_nearest_nested_project(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    leaf = inner / "work"
    leaf.mkdir(parents=True)
    (outer / "modi.toml").write_text("", encoding="utf-8")
    (inner / "modi.toml").write_text("", encoding="utf-8")

    assert locate_project(leaf).project_root == inner.resolve()


def test_locate_project_without_config_uses_start_directory(tmp_path: Path) -> None:
    start = tmp_path / "plain"
    start.mkdir()

    location = locate_project(start)

    assert location.project_root == start.resolve()
    assert location.config_path is None


def test_load_discovery_config_resolves_agent_dirs_from_project_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    agents = root / "custom-agents"
    launch_dir = root / "nested"
    agents.mkdir(parents=True)
    launch_dir.mkdir()
    (root / "modi.toml").write_text(
        """
[project]
name = "demo"

[agents]
dirs = ["custom-agents"]
include_conventional = false
include_plugins = false
include_user = false
trusted_project_factories = true
""".strip(),
        encoding="utf-8",
    )

    config = load_discovery_config(start=launch_dir)

    assert config.project_root == root.resolve()
    assert config.project_name == "demo"
    assert config.dirs == (agents.resolve(),)
    assert config.include_conventional is False
    assert config.include_plugins is False
    assert config.include_user is False
    assert config.trusted_project_factories is True


def test_load_discovery_config_rejects_unknown_key(tmp_path: Path) -> None:
    (tmp_path / "modi.toml").write_text("[agents]\nmagic_registry = true\n", encoding="utf-8")

    with pytest.raises(ModiConfigError, match="magic_registry"):
        load_discovery_config(start=tmp_path)


def test_load_discovery_config_rejects_wrong_boolean_type(tmp_path: Path) -> None:
    (tmp_path / "modi.toml").write_text("[agents]\ninclude_user = 'yes'\n", encoding="utf-8")

    with pytest.raises(ModiConfigError, match="include_user"):
        load_discovery_config(start=tmp_path)


def test_load_discovery_config_rejects_missing_configured_dir(tmp_path: Path) -> None:
    (tmp_path / "modi.toml").write_text("[agents]\ndirs = ['missing']\n", encoding="utf-8")

    with pytest.raises(ModiConfigError, match="does not exist"):
        load_discovery_config(start=tmp_path)


def test_missing_conventional_dirs_are_not_errors(tmp_path: Path) -> None:
    config = load_discovery_config(start=tmp_path)

    assert all(not path.exists() for path in config.conventional_dirs)


def test_locate_project_resolves_symlinked_start(tmp_path: Path) -> None:
    root = tmp_path / "real"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "modi.toml").write_text("", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(nested, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert locate_project(link).project_root == root.resolve()
