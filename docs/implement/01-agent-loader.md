# Agent Loader

## Module

`modi_harness.agents`

## Purpose

Load Markdown agent definitions into `AgentProfile`.

## Input Format

Agent file:

```text
agents/<agent-name>.md
```

Required frontmatter:

```yaml
name:
description:
```

Optional frontmatter:

```yaml
tools:
skills:
output_contract:
permission_profile:
safety_constraints:
```

## Design

Implement:

- `AgentLoader`
- `load_agent(name_or_path: str) -> AgentProfile`
- frontmatter parser
- path resolver
- validation errors

Use `pyyaml` for frontmatter parsing.

## Rules

- Agent instructions do not grant permission.
- Unknown frontmatter is preserved in `metadata`.
- Loader has no LangChain or LangGraph dependency.
- Loader does not select skills or expose tools.

## Tests

- valid Markdown agent
- missing file
- invalid frontmatter
- missing `name`
- missing `description`
- metadata preservation
