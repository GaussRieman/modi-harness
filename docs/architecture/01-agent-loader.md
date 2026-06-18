# Agent Loader

Agent Loader turns a Markdown agent file into an `AgentProfile`.

See [`types-reference.md`](../types-reference.md) for `AgentProfile`, `OutputContract`, `PermissionProfile`.

## Sources

Agent files are discovered, in resolution order:

1. project: `<project_root>/agents/<name>.md`
2. user: `~/.modi/agents/<name>.md`
3. plugin: any directory contributed by an installed Modi plugin

A name from a later source does not override an earlier one. Duplicate names fail fast.

## Frontmatter

Required:

```yaml
name:
description:
```

Optional:

```yaml
tools:                 # list of tool names
skills:                # list of skill names
output_contract:       # inline OutputContract or omitted (= free-form pass-through)
permission_profile:    # inline PermissionProfile
safety_constraints:    # list of strings appended to system safety
tags:                  # list of strings; first-class on AgentProfile
```

Frontmatter key spelling:

- Loader accepts both hyphen and underscore spellings (`allowed-tools` / `allowed_tools`).
- Canonical Python field uses underscore.
- Unknown frontmatter keys are preserved verbatim under `metadata`.

`output_contract` defaults differ by presence:

- absent → `OutputContract(free_form=True, ...)` (Output Controller passes through)
- declared block → `free_form=False` unless explicitly set; declared fields enforced

`permission_profile.mode` defaults to None and is resolved at runtime by Permission Mode rules. See [`14-permission-mode.md`](./14-permission-mode.md).

## Rules

- Agent instructions can constrain behavior but cannot grant permission.
- Policy Gate remains the authority for side effects, approval, denial, and review.
- Loader only parses and normalizes; it does not select skills, expose tools, execute scripts, or call models.
- Markdown body becomes `instruction`. The body may include sub-headings; loader does not interpret them.
- Unknown frontmatter is preserved under `metadata`.
- Loader has no LangChain or LangGraph dependency.

## Authoring Posture

Agent files should describe the role, domain behavior, output expectations, and
domain-specific safety constraints. They should not teach the author or model
the full Harness internals.

Prefer:

```text
你是研究助手。调查用户问题，评估来源，并生成带证据的中文研究简报。
需要时使用可用工具查找资料、读取输入文件、保存结果或查询记忆。
```

Avoid framework-heavy instructions such as:

```text
Memory is not trace/log. Drafts and artifacts are Workspace outputs. Use
user/workspace/thread/agent scopes...
```

Tool descriptions, policy, context assembly, and runtime behavior carry Harness
usage guidance. Agent prompts should stay model-facing and task-facing.

## Boundaries

- Skill resolution: Skill Loader.
- Tool resolution: Tool Gateway.
- Output schema enforcement: Output Controller.
- Permission resolution: Policy Gate (with mode from Permission Mode rules).

## Plugin Sources

Plugin agent dirs are contributed via the `modi_harness.plugins` entry point
group (V0.4c). Each installed plugin's `get_plugin()` callable may return an
`agents_dir` path; harness construction collects every non-`None`
`agents_dir`, deduplicates, and wires the list into
`AgentLoader(project_dir=..., plugin_dirs=[...])` automatically. From the
loader's perspective these dirs are indistinguishable from any other plugin
source — duplicate-name fail-fast still applies. See [`../plugins.md`](../plugins.md)
for the author guide.
