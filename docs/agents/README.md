# Modi Harness — Agent Library

This directory contains **agent definitions**. Each subdirectory is a self-contained, reusable agent that can be loaded by Modi Harness in any scenario.

```text
agents/<agent-name>/
├── agent.md           # frontmatter + instruction body
└── skills/            # skills bundled with this agent (optional)
    └── <skill-name>/
        ├── SKILL.md
        ├── references/
        ├── scripts/
        ├── templates/
        └── examples/
```

An agent definition contains **only the role**: identity, default tools (by name), default skills, output contract, permission profile, safety constraints, tags. It contains **no scenario inputs and no tool implementations**.

- Tool implementations belong in your application code; agents reference tools by name.
- Test inputs and expected behavior belong in [`scenarios/`](../scenarios/), not here.
- Settings (`.env`, hooks) belong in your project, not here.

This separation lets the same agent be reused by multiple scenarios, and lets the same scenario be re-run against different agents.

## Available Agents

| Agent | Style | Mode | Output | Memory | Highlights |
|---|---|---|---|---|---|
| [support-bot](./support-bot/agent.md) | conversational | ask | free-form | conversation + user | thread continuity, escalation policy |
| [research-assistant](./research-assistant/agent.md) | investigative | ask (default) / plan | structured (citation-required) | project | untrusted source handling, plan mode |
| [case-reviewer](./case-reviewer/agent.md) | structured review | ask | structured (review-required) | project | review-required write_draft, evidence gaps |
| [release-coordinator](./release-coordinator/agent.md) | ops coordination | auto | structured | project | hooks, opt-in `coding` rule pack |

## Loading an Agent

```python
from modi_harness import ModiHarness

harness = ModiHarness(agents_dir="docs/agents")
response = harness.run_task(agent="support-bot", input={...})
```

## Authoring a New Agent

1. Pick a name; create `agents/<name>/agent.md`.
2. Write the frontmatter (`name`, `description`, `tools`, optionally `skills`, `output_contract`, `permission_profile`, `safety_constraints`, `tags`).
3. Write the body as a clear instruction for the role.
4. If the agent needs bundled skills, add `agents/<name>/skills/<skill-name>/SKILL.md`.
5. Add a scenario in `scenarios/<scenario-name>/` to exercise it.

See [`../types-reference.md`](../types-reference.md) for `AgentProfile`, `OutputContract`, `PermissionProfile`, `LoadedSkill` shapes.

## What Goes In an Agent vs Where Else

| Belongs in `agents/<name>/agent.md` | Belongs elsewhere |
|---|---|
| identity and responsibilities | tool implementation code |
| default tool names | tool spec details (in scenarios or registry) |
| default skill names | skill bodies (in `skills/` subdir or shared skill packages) |
| output contract | task input (in scenarios) |
| permission profile | permission mode override (in run options) |
| safety constraints | API keys, base URLs (in `.env`) |
| tags | hooks (in `settings.json`) |
