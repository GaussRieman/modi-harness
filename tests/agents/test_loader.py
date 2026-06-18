"""Tests for AgentLoader."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.agents import SUBMIT_OUTPUT_TOOL_NAME, AgentLoader
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
    assert profile["metadata"] == {"memory_level": "moderate"}


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
    """Smoke: every example-shipped agent loads cleanly."""
    repo_root = Path(__file__).resolve().parents[2]
    examples_root = repo_root / "examples"
    found = 0
    for agents_dir in sorted(examples_root.glob("*/agents")):
        loader = AgentLoader(project_dir=agents_dir)
        for agent_md in sorted(agents_dir.glob("*.md")):
            profile = loader.load_agent(str(agent_md))
            assert profile["name"] == agent_md.stem
            assert profile["description"]
            found += 1
    assert found > 0, "expected at least one example agent to smoke-load"


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


def test_loader_parses_memory_level(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
memory_level: full
---
body
""",
    )
    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent("x")
    assert profile["metadata"]["memory_level"] == "full"


def test_loader_defaults_memory_level_to_moderate(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        "---\nname: x\ndescription: y\n---\nbody",
    )
    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent("x")
    assert profile["metadata"]["memory_level"] == "moderate"


def test_loader_parses_memory_level_minimal(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
memory_level: minimal
---
body
""",
    )
    profile = AgentLoader(project_dir=tmp_path / "agents").load_agent("x")
    assert profile["metadata"]["memory_level"] == "minimal"


# ----------------------------------------------------------------------
# Cache: load_agent reuses parse, invalidates on mtime change
# ----------------------------------------------------------------------


def test_load_agent_caches_parse(tmp_path: Path) -> None:
    """Repeated load_agent without file change must not re-read disk."""
    p = tmp_path / "agents" / "x.md"
    _write(p, "---\nname: x\ndescription: y\n---\nbody")

    loader = AgentLoader(project_dir=tmp_path / "agents")
    p1 = loader.load_agent("x")

    # Mutate the file but freeze the mtime: the cache should still serve old.
    mtime_ns = p.stat().st_mtime_ns
    p.write_text("---\nname: x\ndescription: changed\n---\nbody2")
    import os
    os.utime(p, ns=(mtime_ns, mtime_ns))

    p2 = loader.load_agent("x")
    assert p2["description"] == p1["description"], "cached profile expected"


def test_load_agent_invalidates_on_mtime_bump(tmp_path: Path) -> None:
    """When the file mtime moves forward, the next load must re-read."""
    p = tmp_path / "agents" / "x.md"
    _write(p, "---\nname: x\ndescription: original\n---\nbody")

    loader = AgentLoader(project_dir=tmp_path / "agents")
    first = loader.load_agent("x")
    assert first["description"] == "original"

    # Edit and bump mtime forward (ns precision).
    p.write_text("---\nname: x\ndescription: edited\n---\nbody")
    import os
    new_mtime_ns = p.stat().st_mtime_ns + 5_000_000_000  # +5s
    os.utime(p, ns=(new_mtime_ns, new_mtime_ns))

    second = loader.load_agent("x")
    assert second["description"] == "edited"


# ---------------------------------------------------------------------------
# submit_output auto-injection
# ---------------------------------------------------------------------------


def test_structured_contract_with_schema_auto_injects_submit_output(tmp_path: Path) -> None:
    """Agents whose output_contract has both ``schema`` and free_form=False
    get ``submit_output`` appended to default_tools so the model can deliver
    its final payload as SDK-parsed dict args.
    """
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
tools:
  - search
output_contract:
  schema:
    type: object
    properties:
      answer: {type: string}
    required: [answer]
  required_fields: [answer]
---
body
""",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")
    profile = loader.load_agent("x")
    assert SUBMIT_OUTPUT_TOOL_NAME in profile["default_tools"]
    # Ordering: original tools preserved, submit_output appended.
    assert profile["default_tools"] == ["search", SUBMIT_OUTPUT_TOOL_NAME]


def test_free_form_contract_does_not_inject_submit_output(tmp_path: Path) -> None:
    """code_auditor-style free-form Markdown agents must NOT see submit_output."""
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
tools:
  - search
output_contract:
  free_form: true
---
body
""",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")
    profile = loader.load_agent("x")
    assert SUBMIT_OUTPUT_TOOL_NAME not in profile["default_tools"]


def test_structured_contract_with_required_fields_synthesizes_schema(tmp_path: Path) -> None:
    """A required-fields-only contract gets a minimal schema so submit_output
    can carry SDK-parsed dict args instead of raw JSON text.
    """
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
output_contract:
  required_fields: [answer]
---
body
""",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")
    profile = loader.load_agent("x")
    assert SUBMIT_OUTPUT_TOOL_NAME in profile["default_tools"]
    assert profile["output_contract"]["schema"] == {
        "type": "object",
        "properties": {"answer": {}},
        "required": ["answer"],
        "additionalProperties": True,
    }


def test_idempotent_when_user_already_listed_submit_output(tmp_path: Path) -> None:
    """If an author explicitly listed submit_output in tools, don't double-add."""
    _write(
        tmp_path / "agents" / "x.md",
        """---
name: x
description: y
tools:
  - submit_output
output_contract:
  schema:
    type: object
    properties:
      answer: {type: string}
---
body
""",
    )
    loader = AgentLoader(project_dir=tmp_path / "agents")
    profile = loader.load_agent("x")
    assert profile["default_tools"].count(SUBMIT_OUTPUT_TOOL_NAME) == 1
