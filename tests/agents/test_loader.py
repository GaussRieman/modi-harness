"""Tests for AgentLoader."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.agents import AgentLoader
from modi_harness.agents.errors import (
    AgentDuplicateError,
    AgentFrontmatterError,
    AgentNotFoundError,
)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_load_minimal_agent(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        "---\nname: x\ndescription: y\n---\nbody",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")
    profile = loader.load_agent("x")
    assert profile["name"] == "x"
    assert profile["description"] == "y"
    assert profile["instruction"] == "body"
    assert profile["default_tools"] == []
    assert profile["default_skills"] == []
    assert profile["safety_constraints"] == []
    assert profile["tags"] == []
    assert profile["metadata"] == {}


def test_omitted_output_contract_yields_free_form(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        "---\nname: x\ndescription: y\n---\nbody",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")
    profile = loader.load_agent("x")
    assert profile["output_contract"] is not None
    assert profile["output_contract"]["free_form"] is True


def test_declared_output_contract_defaults_structured(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
output_contract:
  required_fields: [a, b]
  citation_required: true
---
body
""",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")
    profile = loader.load_agent("x")
    oc = profile["output_contract"]
    assert oc is not None
    assert oc["free_form"] is False  # declared block defaults free_form False
    assert oc["required_fields"] == ["a", "b"]
    assert oc["citation_required"] is True
    # Other fields filled with defaults.
    assert oc["risk_label_required"] is False


def test_permission_profile_normalized(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
permission_profile:
  mode: auto
  preauthorized: [t1]
---
""",
    )
    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent("x")
    pp = profile["permission_profile"]
    assert pp is not None
    assert pp["mode"] == "auto"
    assert pp["preauthorized"] == ["t1"]
    assert pp["deny"] == []
    assert pp["review_required"] == []


def test_unknown_frontmatter_preserved_in_metadata(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
custom_key: keep
---
""",
    )
    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent("x")
    assert profile["metadata"].get("custom_key") == "keep"


def test_hyphen_underscore_both_accepted(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
safety-constraints:
  - one
---
""",
    )
    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent("x")
    assert profile["safety_constraints"] == ["one"]


def test_missing_file(tmp_path: Path) -> None:
    loader = AgentLoader(project_dir=tmp_path / "agents")
    with pytest.raises(AgentNotFoundError):
        loader.load_agent("missing")


def test_missing_required_frontmatter(tmp_path: Path) -> None:
    _write(tmp_path / "agents" / "x.md", "---\nname: x\n---\n")
    with pytest.raises(AgentFrontmatterError, match="description"):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("x")


def test_invalid_yaml(tmp_path: Path) -> None:
    _write(tmp_path / "agents" / "x.md", "---\n: : :\n---\n")
    with pytest.raises(AgentFrontmatterError):
        AgentLoader(project_dir=tmp_path / "agents").load_agent("x")


def test_duplicate_across_sources_fails_fast(tmp_path: Path) -> None:
    _write(tmp_path / "project" / "x.md", "---\nname: x\ndescription: a\n---\n")
    _write(tmp_path / "user" / "x.md", "---\nname: x\ndescription: b\n---\n")
    loader = AgentLoader(
        project_dir=tmp_path / "project",
        user_dir=tmp_path / "user",
    )
    with pytest.raises(AgentDuplicateError):
        loader.load_agent("x")


def test_load_by_path(tmp_path: Path) -> None:
    p = tmp_path / "anywhere" / "x.md"
    _write(p, "---\nname: x\ndescription: y\n---\n")
    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent(str(p))
    assert profile["name"] == "x"


def test_loads_all_sample_agents() -> None:
    """Smoke: every doc-shipped sample agent loads cleanly."""
    repo_root = Path(__file__).resolve().parents[2]
    samples_root = repo_root / "docs" / "agents"
    loader = AgentLoader(project_dir=samples_root)
    for sample_dir in sorted(samples_root.iterdir()):
        if not sample_dir.is_dir():
            continue
        agent_md = sample_dir / "agent.md"
        if not agent_md.exists():
            continue
        profile = loader.load_agent(str(agent_md))
        assert profile["name"] == sample_dir.name
        assert profile["description"]


def test_loader_parses_allowed_subagents(tmp_path: Path) -> None:
    p = tmp_path / "lead.md"
    _write(
        p,
        """---
name: lead
description: lead
permission_profile:
  mode: ask
  allowed_subagents: ["research-assistant", "case-reviewer"]
  subagent_max_depth: 2
---
Body.
""",
    )
    profile = AgentLoader(project_dir=tmp_path).load_agent("lead")
    pp = profile["permission_profile"] or {}
    assert pp["allowed_subagents"] == ["research-assistant", "case-reviewer"]
    assert pp["subagent_max_depth"] == 2


def test_loader_defaults_allowed_subagents_to_empty(tmp_path: Path) -> None:
    p = tmp_path / "solo.md"
    _write(
        p,
        """---
name: solo
description: solo
permission_profile:
  mode: ask
---
Body.
""",
    )
    profile = AgentLoader(project_dir=tmp_path).load_agent("solo")
    pp = profile["permission_profile"] or {}
    assert pp["allowed_subagents"] == []
    assert pp["subagent_max_depth"] is None
