"""Skill Loader implementation.

Walks a directory layout::

    skills/<skill-name>/
        SKILL.md            (required)
        references/
        scripts/
        templates/
        examples/

Indexes asset names + sizes; never loads asset bodies. Asset bodies are loaded
on demand by Context Manager or tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .._utils import parse_frontmatter
from ..types import LoadedSkill, SkillAssetRef
from .errors import SkillDuplicateError, SkillFrontmatterError, SkillNotFoundError

_KNOWN_FIELDS: frozenset[str] = frozenset(
    {"name", "description", "allowed_tools", "risk_notes", "tags"}
)

_ASSET_KINDS: tuple[tuple[str, str], ...] = (
    ("references", "reference"),
    ("scripts", "script"),
    ("templates", "template"),
    ("examples", "example"),
)


class SkillLoader:
    """Load skill packages from project / agent-bundled / user / plugin sources."""

    def __init__(
        self,
        project_dir: Path | str | None = None,
        agent_bundled_dir: Path | str | None = None,
        user_dir: Path | str | None = None,
        plugin_dirs: list[Path] | None = None,
    ) -> None:
        self._sources: list[Path] = []
        for d in (project_dir, agent_bundled_dir, user_dir, *(plugin_dirs or [])):
            if d is None:
                continue
            self._sources.append(Path(d))

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def load_skill(self, name_or_path: str) -> LoadedSkill:
        pkg = self._resolve(name_or_path)
        skill_md = pkg / "SKILL.md"
        try:
            text = skill_md.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise SkillNotFoundError(f"{pkg}: SKILL.md missing") from exc

        try:
            fm, body = parse_frontmatter(text)
        except ValueError as exc:
            raise SkillFrontmatterError(f"{skill_md}: {exc}") from exc

        return self._build(fm, body, pkg)

    def load_skills(self, names: list[str]) -> list[LoadedSkill]:
        return [self.load_skill(n) for n in names]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _resolve(self, name_or_path: str) -> Path:
        candidate = Path(name_or_path)
        if candidate.is_dir() and (candidate / "SKILL.md").exists():
            return candidate

        matches: list[Path] = []
        for source in self._sources:
            pkg = source / name_or_path
            if (pkg / "SKILL.md").exists():
                matches.append(pkg)

        if not matches:
            raise SkillNotFoundError(f"skill '{name_or_path}' not found in any source")
        if len(matches) > 1:
            joined = ", ".join(str(m) for m in matches)
            raise SkillDuplicateError(
                f"skill '{name_or_path}' defined in multiple sources: {joined}"
            )
        return matches[0]

    def _build(self, fm: dict[str, Any], body: str, pkg: Path) -> LoadedSkill:
        name = self._require_str(fm, "name", pkg)
        description = self._require_str(fm, "description", pkg)

        # tri-state: absent -> None; [] -> []; list -> list
        if "allowed_tools" not in fm:
            allowed_tools: list[str] | None = None
        else:
            raw = fm["allowed_tools"]
            if raw is None:
                allowed_tools = None
            elif isinstance(raw, list):
                allowed_tools = [str(v) for v in raw]
            else:
                raise SkillFrontmatterError(
                    f"{pkg}/SKILL.md: 'allowed_tools' must be a list or absent"
                )

        risk_notes = self._as_str_list(fm.get("risk_notes", []), "risk_notes", pkg)
        tags = self._as_str_list(fm.get("tags", []), "tags", pkg)

        assets = {
            kind_field: self._index_assets(pkg, dirname, kind_label)
            for dirname, kind_label in _ASSET_KINDS
            for kind_field in (dirname,)
        }

        metadata = {k: v for k, v in fm.items() if k not in _KNOWN_FIELDS}

        return LoadedSkill(
            name=name,
            description=description,
            instruction=body.strip(),
            allowed_tools=allowed_tools,
            risk_notes=risk_notes,
            references=assets["references"],
            scripts=assets["scripts"],
            templates=assets["templates"],
            examples=assets["examples"],
            tags=tags,
            metadata=metadata,
        )

    def _index_assets(self, pkg: Path, dirname: str, kind_label: str) -> list[SkillAssetRef]:
        d = pkg / dirname
        if not d.is_dir():
            return []
        out: list[SkillAssetRef] = []
        for entry in sorted(d.iterdir()):
            if not entry.is_file():
                continue
            out.append(
                SkillAssetRef(
                    kind=kind_label,  # type: ignore[arg-type]
                    name=entry.name,
                    path=str(entry),
                    size_bytes=entry.stat().st_size,
                    summary=None,
                )
            )
        return out

    @staticmethod
    def _require_str(fm: dict[str, Any], key: str, pkg: Path) -> str:
        if key not in fm:
            raise SkillFrontmatterError(f"{pkg}/SKILL.md: missing required field '{key}'")
        v = fm[key]
        if not isinstance(v, str) or not v.strip():
            raise SkillFrontmatterError(f"{pkg}/SKILL.md: '{key}' must be a non-empty string")
        return v

    @staticmethod
    def _as_str_list(value: Any, key: str, pkg: Path) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        raise SkillFrontmatterError(f"{pkg}/SKILL.md: '{key}' must be a list")
