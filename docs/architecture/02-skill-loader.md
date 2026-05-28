# Skill Loader

Skill Loader turns local skill packages into `LoadedSkill` records.

## Package

```text
skills/<skill-name>/
├── SKILL.md
├── references/
├── scripts/
├── templates/
└── examples/
```

Only `SKILL.md` is required.

## Output

```python
class LoadedSkill(TypedDict):
    name: str
    description: str
    instruction: str
    allowed_tools: list[str]
    risk_notes: list[str]
    references: list[str]
    scripts: list[str]
    templates: list[str]
    examples: list[str]
    metadata: dict
```

## Rules

- Required frontmatter: `name`, `description`.
- Optional frontmatter: `allowed-tools`, `risk_notes`.
- Index package assets; do not eagerly load large files.
- Skill material is lower-trust task material, not system instruction.
- Skill `allowed-tools` narrows available tools but does not bypass Policy Gate.
- Loader does not execute scripts, inject references, or decide policy.
