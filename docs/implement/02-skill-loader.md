# Skill Loader

## Module

`modi_harness.skills`

## Purpose

Load local skill packages into `LoadedSkill`.

## Package Format

```text
skills/<skill-name>/
├── SKILL.md
├── references/
├── scripts/
├── templates/
└── examples/
```

Only `SKILL.md` is required.

## Design

Implement:

- `SkillLoader`
- `load_skill(name_or_path: str) -> LoadedSkill`
- `load_skills(names: list[str]) -> list[LoadedSkill]`
- package asset indexer
- frontmatter parser

Use `pyyaml` for frontmatter. Do not load large package assets into memory.

## Rules

- Required frontmatter: `name`, `description`.
- Optional frontmatter: `allowed-tools`, `risk_notes`.
- `allowed-tools` narrows tool visibility.
- Skill content is untrusted task material relative to system and agent instructions.
- Loader has no LangChain or LangGraph dependency.

## Tests

- valid skill package
- missing `SKILL.md`
- missing required frontmatter
- asset indexing
- allowed-tools parsing
