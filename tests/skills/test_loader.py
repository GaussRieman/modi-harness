"""Tests for SkillLoader."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.skills import SkillDuplicateError, SkillLoader, SkillNotFoundError


def _write_skill(
    root: Path,
    name: str,
    body: str,
    *,
    references: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
    examples: dict[str, str] | None = None,
) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(body)
    for kind, files in (
        ("references", references),
        ("scripts", scripts),
        ("templates", templates),
        ("examples", examples),
    ):
        if not files:
            continue
        d = pkg / kind
        d.mkdir(exist_ok=True)
        for fn, content in files.items():
            (d / fn).write_text(content)
    return pkg


def test_load_minimal_skill(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills",
        "x",
        "---\nname: x\ndescription: y\n---\nbody",
    )
    loader = SkillLoader(project_dir=tmp_path / "skills")
    s = loader.load_skill("x")
    assert s["name"] == "x"
    assert s["description"] == "y"
    assert s["instruction"] == "body"
    assert s["allowed_tools"] is None  # tri-state: absent -> None
    assert s["risk_notes"] == []
    assert s["tags"] == []


def test_allowed_tools_tri_state_absent(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills", "x", "---\nname: x\ndescription: y\n---\n")
    s = SkillLoader(project_dir=tmp_path / "skills").load_skill("x")
    assert s["allowed_tools"] is None


def test_allowed_tools_tri_state_empty(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills",
        "x",
        "---\nname: x\ndescription: y\nallowed-tools: []\n---\n",
    )
    s = SkillLoader(project_dir=tmp_path / "skills").load_skill("x")
    assert s["allowed_tools"] == []


def test_allowed_tools_tri_state_listed(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills",
        "x",
        "---\nname: x\ndescription: y\nallowed-tools:\n  - a\n  - b\n---\n",
    )
    s = SkillLoader(project_dir=tmp_path / "skills").load_skill("x")
    assert s["allowed_tools"] == ["a", "b"]


def test_assets_indexed_without_body_load(tmp_path: Path) -> None:
    pkg = _write_skill(
        tmp_path / "skills",
        "x",
        "---\nname: x\ndescription: y\n---\n",
        references={"guide.md": "G" * 1000},
        scripts={"run.py": "print(1)"},
        templates={"out.md": "T"},
        examples={"good.md": "OK"},
    )
    s = SkillLoader(project_dir=tmp_path / "skills").load_skill("x")
    assert {a["name"] for a in s["references"]} == {"guide.md"}
    assert s["references"][0]["size_bytes"] == 1000
    assert s["references"][0]["path"] == str(pkg / "references" / "guide.md")
    assert {a["name"] for a in s["scripts"]} == {"run.py"}
    assert {a["name"] for a in s["templates"]} == {"out.md"}
    assert {a["name"] for a in s["examples"]} == {"good.md"}


def test_missing_skill_md(tmp_path: Path) -> None:
    pkg = tmp_path / "skills" / "x"
    pkg.mkdir(parents=True)
    with pytest.raises(SkillNotFoundError):
        SkillLoader(project_dir=tmp_path / "skills").load_skill("x")


def test_missing_skill_dir(tmp_path: Path) -> None:
    with pytest.raises(SkillNotFoundError):
        SkillLoader(project_dir=tmp_path / "skills").load_skill("missing")


def test_duplicate_across_sources_fails_fast(tmp_path: Path) -> None:
    _write_skill(tmp_path / "project", "x", "---\nname: x\ndescription: a\n---\n")
    _write_skill(tmp_path / "user", "x", "---\nname: x\ndescription: b\n---\n")
    loader = SkillLoader(
        project_dir=tmp_path / "project",
        user_dir=tmp_path / "user",
    )
    with pytest.raises(SkillDuplicateError):
        loader.load_skill("x")


def test_load_skills_batch(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills", "a", "---\nname: a\ndescription: A\n---\n")
    _write_skill(tmp_path / "skills", "b", "---\nname: b\ndescription: B\n---\n")
    loaded = SkillLoader(project_dir=tmp_path / "skills").load_skills(["a", "b"])
    assert {s["name"] for s in loaded} == {"a", "b"}


def test_loads_all_sample_skills() -> None:
    """Smoke: every example-shipped skill loads cleanly."""
    repo_root = Path(__file__).resolve().parents[2]
    examples_root = repo_root / "examples"
    found = 0
    for skills_dir in sorted(examples_root.glob("*/skills")):
        loader = SkillLoader(project_dir=skills_dir)
        for skill_dir in sorted(skills_dir.iterdir()):
            if not (skill_dir / "SKILL.md").exists():
                continue
            s = loader.load_skill(skill_dir.name)
            assert s["name"] == skill_dir.name
            found += 1
    assert found > 0, "expected at least one example skill to smoke-load"


# ----------------------------------------------------------------------
# Cache: load_skill reuses parse, invalidates on mtime change
# ----------------------------------------------------------------------


def test_load_skill_caches_parse(tmp_path: Path) -> None:
    pkg = _write_skill(tmp_path / "skills", "a", "---\nname: a\ndescription: orig\n---\nbody")
    loader = SkillLoader(project_dir=tmp_path / "skills")
    first = loader.load_skill("a")

    skill_md = pkg / "SKILL.md"
    mtime_ns = skill_md.stat().st_mtime_ns
    skill_md.write_text("---\nname: a\ndescription: changed\n---\nbody")
    import os
    os.utime(skill_md, ns=(mtime_ns, mtime_ns))

    second = loader.load_skill("a")
    assert second["description"] == first["description"], "expected cached skill"


def test_load_skill_invalidates_on_mtime(tmp_path: Path) -> None:
    pkg = _write_skill(tmp_path / "skills", "a", "---\nname: a\ndescription: orig\n---\nbody")
    loader = SkillLoader(project_dir=tmp_path / "skills")
    first = loader.load_skill("a")
    assert first["description"] == "orig"

    skill_md = pkg / "SKILL.md"
    skill_md.write_text("---\nname: a\ndescription: edited\n---\nbody")
    import os
    new_mtime_ns = skill_md.stat().st_mtime_ns + 5_000_000_000
    os.utime(skill_md, ns=(new_mtime_ns, new_mtime_ns))

    second = loader.load_skill("a")
    assert second["description"] == "edited"
