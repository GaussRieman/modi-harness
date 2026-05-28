# Agent Loader

## Module

`modi_harness.agents`

## Purpose

Load Markdown agent definitions into `AgentProfile`.

Contract: see [`../architecture/01-agent-loader.md`](../architecture/01-agent-loader.md).
Types: see [`../types-reference.md`](../types-reference.md).

## Design

Implement:

- `AgentLoader`
- `load_agent(name_or_path: str) -> AgentProfile`
- frontmatter parser (shared utility, see Skill Loader)
- multi-source resolver: project → user → plugin
- duplicate-name detector
- `OutputContract` normalizer (absent → `free_form=True`)
- `PermissionProfile` normalizer

Use `pyyaml` for frontmatter parsing.

## Rules (impl-specific)

- Resolver fails fast on duplicate names across sources.
- Unknown frontmatter is preserved verbatim under `metadata`.
- Loader has no LangChain or LangGraph dependency.
- No filesystem writes.

## Settings

```text
MODI_AGENT_PROJECT_DIR=agents
MODI_AGENT_USER_DIR=~/.modi/agents
```

## Tests

- valid Markdown agent
- missing file
- invalid frontmatter
- missing `name`
- missing `description`
- metadata preservation for unknown frontmatter keys
- both `allowed-tools` and `allowed_tools` accepted (and other hyphen/underscore pairs)
- duplicate across sources fails fast
- absent `output_contract` yields `free_form=True`
- declared `output_contract` defaults `free_form=False` and applies field defaults
- `tags` parsed as first-class field, not metadata
- absent `permission_profile.mode` resolves later via Permission Mode rules
