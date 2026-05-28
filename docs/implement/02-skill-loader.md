# Skill Loader

## Module

`modi_harness.skills`

## Purpose

Load local skill packages into `LoadedSkill`.

Contract: see [`../architecture/02-skill-loader.md`](../architecture/02-skill-loader.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Design

Implement:

- `SkillLoader`
- `load_skill(name_or_path: str) -> LoadedSkill`
- `load_skills(names: list[str]) -> list[LoadedSkill]`
- multi-source resolver: project → agent-bundled → user → plugin
- duplicate-name detector
- package asset indexer (no body loading)
- frontmatter parser (shared utility)
- `reload(name)` for explicit refresh

Use `pyyaml` for frontmatter. Do not load large package assets into memory.

## Rules (impl-specific)

- `allowed_tools` is tri-state: absent → `None`; `[]` → narrow to nothing; `[a, b]` → narrow to listed.
- Both hyphen (`allowed-tools`) and underscore (`allowed_tools`) frontmatter spellings accepted; canonical Python field uses underscore.
- Asset indexing reads names, paths, sizes, and optional `summary.md` per asset dir; no body load.
- Loader has no LangChain or LangGraph dependency.
- No filesystem writes.

## Settings

```text
MODI_SKILL_PROJECT_DIR=skills
MODI_SKILL_USER_DIR=~/.modi/skills
```

## Tests

- valid skill package
- missing `SKILL.md`
- missing required frontmatter
- asset indexing without body load
- `allowed_tools` tri-state: absent → None, `[]` → empty list, populated → list
- `tags` parsed as first-class field
- both hyphen and underscore frontmatter spellings accepted
- duplicate across sources fails fast
- plugin-contributed skill discovery
- reload refreshes index
