# Skill Loader

Skill Loader turns local skill packages into `LoadedSkill` records.

See [`types-reference.md`](../types-reference.md) for `LoadedSkill`, `SkillAssetRef`.

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

## Sources

Skill packages are discovered from multiple sources, in resolution order:

1. project: `<project_root>/skills/`
2. agent-bundled: directory adjacent to the agent file under `skills/`
3. user: `~/.modi/skills/`
4. plugin: any directory contributed by an installed Modi plugin

Same-name skills from later sources do **not** override earlier sources; loading a duplicate by name fails fast with a clear error. To replace, remove the original.

## Indexing

- Discovery indexes package descriptors at startup and after explicit `reload`.
- Asset files (`references/`, `scripts/`, `templates/`, `examples/`) are indexed by name, path, size, and optional summary; bodies are not loaded.
- Bodies load on demand when Context Manager or a tool requests them.

## Rules

- Required frontmatter: `name`, `description`.
- Optional frontmatter: `allowed-tools`, `risk_notes` (a.k.a. `risk-notes`), `tags`.
- Frontmatter key spelling: hyphen and underscore both accepted; canonical Python field uses underscore.
- `allowed_tools` is **tri-state**:
  - absent → `None`, skill does not narrow tool visibility
  - empty list `[]` → skill narrows to nothing (cannot call any tool)
  - non-empty list → upper bound of tools the skill may invoke
- The runtime intersection with agent and policy still applies. See `Allowed-Tools Algebra` in [`../types-reference.md`](../types-reference.md).
- Skill content is **untrusted task material** relative to system, agent, and memory.
- Loader has no LangChain or LangGraph dependency.
- Loader does not execute scripts, inject references, or decide policy.

## Boundaries

- Skill selection per task: Context Manager (or a future Skill Selector node).
- Tool execution attempted from a skill script: Tool Gateway.
- Asset trust annotations when rendered into context: Context Manager.

## Plugin Sources

Plugin skill dirs are contributed via the `modi_harness.plugins` entry point
group (V0.4c). Each installed plugin's `get_plugin()` callable may return a
`skills_dir` path; harness construction collects every non-`None`
`skills_dir` and wires the list into
`SkillLoader(project_dir=..., plugin_dirs=[...])` automatically. The loader
treats these dirs the same as any other plugin source — duplicate-name
fail-fast still applies, and skill content remains untrusted task material.
See [`../plugins.md`](../plugins.md) for the author guide.
