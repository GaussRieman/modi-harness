# Agent and Skill

## Ownership

`ModiAgent` is an immutable declaration. It owns identity, instruction,
Agent-scoped Tools and Skills, subagents, model override, permission profile,
interaction protocol, task protocol, and output contract. It has no `run`
method; execution belongs to `ModiSession`.

Agent definitions may be constructed in Python or loaded from Markdown. The
Markdown body becomes the instruction; frontmatter is normalized by
`agents.loader`.

## Discovery

`discovery.config` locates the nearest `modi.toml`. `AgentRegistry` combines
configured project directories, conventional project locations, plugins, user
Agents, explicit directories, and trusted project factories. Every result has
source provenance. Qualified names resolve directly; ambiguous unqualified
names fail with candidates.

Discovery produces `ModiAgent` objects. It does not create a Harness or Session.

## Skill boundary

A Skill is a reusable method package: instruction plus optional assets and
metadata. `SkillLoader` resolves and parses `SKILL.md`; during execution the
Session-backed loader supplies only Skills attached to the active Agent.

```text
Agent  = role, objective, durable behavioral boundary
Skill  = reusable professional method
Tool   = executable operation with a typed input contract
```

Agent instructions should not encode graph transitions or Tool-call order.
Skill instructions should not acquire side-effect authority. Tools never grant
themselves visibility or permission.

## Session binding

At Session construction, `_session_helpers`:

1. validates and flattens recursive Agent trees;
2. rejects conflicting names;
3. builds Agent and Skill lookup adapters;
4. merges Agent Tools with Harness kernel Tools;
5. registers subagent delegation Tools.

Changing the Agent or Tool set requires a new `ModiSession`.

## Source entry points

- `api/agent.py`
- `agents/loader.py`
- `skills/loader.py`
- `discovery/config.py`, `discovery/registry.py`, `discovery/factories.py`
- `api/_session_helpers.py`

