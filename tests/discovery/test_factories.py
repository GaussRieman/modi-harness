"""Trusted project Agent package factory tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.api.errors import AgentFactoryError
from modi_harness.discovery import discover_agents


def _write_package(
    directory: Path,
    *,
    name: str = "factory-agent",
    return_expression: str | None = None,
) -> None:
    directory.mkdir(parents=True)
    (directory / "agent.toml").write_text(
        'factory = "runtime:build_agent"\n', encoding="utf-8"
    )
    (directory / "agent.md").write_text(
        f"---\nname: {name}\ndescription: fallback\n---\nFallback.\n",
        encoding="utf-8",
    )
    expression = return_expression or (
        f"ModiAgent(name={name!r}, description='factory', instruction='From factory.')"
    )
    (directory / "runtime.py").write_text(
        "from modi_harness import ModiAgent\n\n"
        "def build_agent():\n"
        f"    return {expression}\n",
        encoding="utf-8",
    )


def test_trusted_project_factory_returns_complete_agent(tmp_path: Path) -> None:
    _write_package(tmp_path / "agents" / "factory_agent")
    (tmp_path / "modi.toml").write_text(
        "[agents]\ntrusted_project_factories = true\ninclude_plugins = false\ninclude_user = false\n",
        encoding="utf-8",
    )

    descriptor = discover_agents(cwd=tmp_path, plugins=[]).registry.resolve("factory-agent")

    assert descriptor.executable_factory is True
    assert descriptor.agent.description == "factory"
    assert descriptor.path == (tmp_path / "agents" / "factory_agent").resolve()


def test_trusted_project_factory_manifest_accepts_profile_fields(tmp_path: Path) -> None:
    package = tmp_path / "agents" / "factory_agent"
    package.mkdir(parents=True)
    (package / "agent.toml").write_text(
        """factory = "runtime:build_agent"
name = "factory-agent"
description = "declarative factory package"
instruction_file = "brain.md"

[metadata]
purpose = "hybrid"
""",
        encoding="utf-8",
    )
    (package / "brain.md").write_text(
        "---\nname: brain\ndescription: brain\n---\nFrom package brain.\n",
        encoding="utf-8",
    )
    (package / "runtime.py").write_text(
        "from pathlib import Path\n"
        "from modi_harness import ModiAgent\n\n"
        "def build_agent():\n"
        "    return ModiAgent.from_markdown(Path(__file__).parent / 'agent.toml')\n",
        encoding="utf-8",
    )
    (tmp_path / "modi.toml").write_text(
        "[agents]\ntrusted_project_factories = true\ninclude_plugins = false\ninclude_user = false\n",
        encoding="utf-8",
    )

    descriptor = discover_agents(cwd=tmp_path, plugins=[]).registry.resolve("factory-agent")

    assert descriptor.executable_factory is True
    assert descriptor.agent.description == "declarative factory package"
    assert descriptor.agent.instruction == "From package brain."
    assert descriptor.agent.metadata["purpose"] == "hybrid"


def test_project_factory_requires_explicit_trust(tmp_path: Path) -> None:
    _write_package(tmp_path / "agents" / "factory_agent")
    (tmp_path / "modi.toml").write_text(
        "[agents]\ninclude_plugins = false\ninclude_user = false\n",
        encoding="utf-8",
    )

    with pytest.raises(AgentFactoryError, match="not trusted"):
        discover_agents(cwd=tmp_path, plugins=[])


def test_factory_return_type_is_validated(tmp_path: Path) -> None:
    _write_package(
        tmp_path / "agents" / "bad_agent",
        return_expression="{'name': 'not-an-agent'}",
    )
    (tmp_path / "modi.toml").write_text(
        "[agents]\ntrusted_project_factories = true\ninclude_plugins = false\ninclude_user = false\n",
        encoding="utf-8",
    )

    with pytest.raises(AgentFactoryError, match="expected ModiAgent"):
        discover_agents(cwd=tmp_path, plugins=[])


def test_user_factory_python_is_not_imported(tmp_path: Path) -> None:
    package = tmp_path / "user" / "unsafe"
    _write_package(package, name="safe-markdown")
    marker = tmp_path / "executed"
    (package / "runtime.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    (tmp_path / "modi.toml").write_text(
        "[agents]\ninclude_plugins = false\n",
        encoding="utf-8",
    )

    result = discover_agents(cwd=tmp_path, user_dir=tmp_path / "user", plugins=[])

    assert not marker.exists()
    descriptor = result.registry.resolve("safe-markdown")
    assert descriptor.source_kind == "user"
    assert descriptor.executable_factory is False
    assert result.notices and "ignored untrusted user factory" in result.notices[0]


def test_explicit_directory_trusts_factory_by_user_intent(tmp_path: Path) -> None:
    _write_package(tmp_path / "external" / "factory_agent")
    (tmp_path / "modi.toml").write_text(
        "[agents]\ninclude_conventional = false\ninclude_plugins = false\ninclude_user = false\n",
        encoding="utf-8",
    )

    descriptor = discover_agents(
        cwd=tmp_path,
        explicit_dirs=[tmp_path / "external"],
        plugins=[],
    ).registry.resolve("factory-agent")

    assert descriptor.source_kind == "explicit"
    assert descriptor.executable_factory is True
