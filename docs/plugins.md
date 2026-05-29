# Modi Harness Plugin Author Guide

> **Status:** new in V0.4c. Requires `modi-harness >= 0.4.2`.

This guide explains how to ship a Modi Harness plugin: a `pip install`-able
Python package that contributes agents, skills, and tools to any harness it is
installed alongside. Discovery uses Python's standard
[`importlib.metadata` entry points](https://docs.python.org/3/library/importlib.metadata.html#entry-points),
so authors do not need to interact with Modi internals beyond a single
`get_plugin()` callable.

## Overview

A Modi plugin can contribute three kinds of artefacts:

| Contribution | What it adds |
|---|---|
| `agents_dir` | A directory of Markdown agent files. Each becomes an `AgentProfile` resolvable by name (and gets a `delegate_to_<agent>` subagent tool automatically). |
| `skills_dir` | A directory of skill packages (each with `SKILL.md`). Each becomes a `LoadedSkill` available to agents that opt in via their frontmatter `skills:` list. |
| `tools` | A list of `(tool_spec_dict, handler_callable)` tuples registered through the same path as `ModiHarness.register_tool(...)`. Plugin tools obey the full Tool Gateway chain (visibility, hooks, Policy Gate, trust annotation). |

A single plugin may contribute any subset (agents only, tools only, or all
three). Discovery is fail-fast: if any installed plugin fails to import,
validate, or call cleanly, `ModiHarness(...)` raises `PluginLoadError` at
construction time. There is no per-plugin disable switch — uninstall the
package instead.

## Package structure

A typical plugin package looks like this:

```text
my-modi-plugin/
├── pyproject.toml
└── modi_my_plugin/
    ├── __init__.py
    ├── agents/
    │   └── my-agent.md
    └── skills/
        └── my-skill/
            └── SKILL.md
```

The package name (`modi_my_plugin`) is your choice — only the entry point name
matters for discovery. By convention, plugin packages start with `modi_` so
they are easy to spot in a Python environment.

## The `get_plugin` function

Every plugin exposes one zero-argument callable that returns a dict. The dict
is the plugin manifest:

```python
# modi_my_plugin/__init__.py
from pathlib import Path
from typing import Any


def my_tool_handler(**kwargs: Any) -> dict[str, Any]:
    return {"result": "ok"}


def get_plugin() -> dict:
    return {
        "name": "my-plugin",
        "agents_dir": Path(__file__).parent / "agents",
        "skills_dir": Path(__file__).parent / "skills",
        "tools": [
            (
                {
                    "name": "my_tool",
                    "description": "Does X.",
                    "input_schema": {"type": "object", "properties": {}},
                    "risk_level": "L1",
                },
                my_tool_handler,
            ),
        ],
    }
```

Required keys:

| Key | Type | Notes |
|---|---|---|
| `name` | `str` | Plugin identifier, used in `modi plugins list` output and error messages. Must be a non-empty string. |

Optional keys (omit when not used; do not pass `None`):

| Key | Type | Notes |
|---|---|---|
| `agents_dir` | `Path \| str` | Must exist as a directory if provided. |
| `skills_dir` | `Path \| str` | Must exist as a directory if provided. |
| `tools` | `list[tuple[dict, Callable]]` | Each tuple is `(tool_spec_dict, handler)`. The spec dict must contain `name`, `description`, `input_schema`, `risk_level`. |

Use `Path(__file__).parent / "..."` so the plugin keeps working regardless of
where it is installed.

## Entry point declaration

Register your `get_plugin` callable under the `modi_harness.plugins` entry
point group in `pyproject.toml`:

```toml
[project]
name = "modi-my-plugin"
version = "0.1.0"
dependencies = [
    "modi-harness>=0.4.2,<0.5",
]

[project.entry-points."modi_harness.plugins"]
my-plugin = "modi_my_plugin:get_plugin"
```

The entry point key (`my-plugin` above) is what shows up in
`modi plugins list`; the value is `<module_path>:<callable>`. A single package
may register multiple entry points if it ships logically separate plugins.

## Three example shapes

### Agents-only plugin

A package that only ships agent definitions (no custom tools, no skills):

```python
# modi_my_agents/__init__.py
from pathlib import Path


def get_plugin() -> dict:
    return {
        "name": "my-agents",
        "agents_dir": Path(__file__).parent / "agents",
    }
```

```toml
[project.entry-points."modi_harness.plugins"]
my-agents = "modi_my_agents:get_plugin"
```

### Tools-only plugin

A package that adds tool functions but no agent or skill content:

```python
# modi_my_tools/__init__.py
from typing import Any


def lookup_user(*, user_id: str) -> dict[str, Any]:
    # ...real implementation...
    return {"user_id": user_id, "status": "active"}


def get_plugin() -> dict:
    return {
        "name": "my-tools",
        "tools": [
            (
                {
                    "name": "lookup_user",
                    "description": "Fetch user profile by id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"user_id": {"type": "string"}},
                        "required": ["user_id"],
                    },
                    "risk_level": "L1",
                },
                lookup_user,
            ),
        ],
    }
```

### Full plugin

A package that contributes agents, skills, and tools together:

```python
# modi_full_plugin/__init__.py
from pathlib import Path
from typing import Any

_PKG = Path(__file__).parent


def report_handler(**kwargs: Any) -> dict[str, str]:
    return {"status": "queued"}


def get_plugin() -> dict:
    return {
        "name": "ops-pack",
        "agents_dir": _PKG / "agents",
        "skills_dir": _PKG / "skills",
        "tools": [
            (
                {
                    "name": "ops_report",
                    "description": "File an ops report.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"],
                    },
                    "risk_level": "L3",
                },
                report_handler,
            ),
        ],
    }
```

## Local testing

While iterating on a plugin:

1. Install in editable mode from your plugin repo:

    ```bash
    pip install -e .
    ```

2. List discovered plugins to confirm Modi sees yours:

    ```bash
    modi plugins list
    ```

    You should see your plugin listed with the agent / skill / tool counts you
    expect:

    ```text
    Discovered plugins:
      my-plugin (entry_point:modi-my-plugin v0.1.0)
        agents: 1 (my-agent)
        skills: 1 (my-skill)
        tools:  1 (my_tool)

    (1 plugin, 1 agent, 1 skill, 1 tool)
    ```

3. Run an agent contributed by the plugin:

    ```bash
    modi run --agent my-agent --task task.json
    ```

If the plugin manifest is malformed, `modi plugins list` (and any
`ModiHarness(...)` construction) prints a `PluginLoadError` describing what
went wrong and the source the plugin came from.

## Validation rules

The validator (`modi_harness.plugins._validate_plugin_dict`) enforces the
following at discovery time:

- `name` must be a non-empty string.
- If `agents_dir` is provided, it must exist as a directory.
- If `skills_dir` is provided, it must exist as a directory.
- If `tools` is provided, it must be a list of `(dict, callable)` tuples.
- Each tool spec dict must contain at minimum `name`, `description`,
  `input_schema`, `risk_level`. (Other `ToolSpec` fields are optional.)
- Tool name collisions across plugins (or with the host harness's existing
  tools) raise `ToolDuplicateError` at registration time.

Validation failures are reported as `PluginLoadError` with the plugin name,
provenance (`entry_point:<dist> v<version>`), and a descriptive message. The
error is fail-fast: a single broken plugin prevents the whole harness from
constructing, so problems surface during `pip install` testing rather than
silently in production.

## Versioning guidance

Modi Harness uses semantic versioning at the minor level — additive features
land in minor versions, breaking changes to public contracts move the major.
For plugins:

- **Pin a Modi version range in your dependencies.** Express compatibility
  with the Modi line you tested against:

    ```toml
    dependencies = [
        "modi-harness>=0.4.2,<0.5",
    ]
    ```

  A new Modi minor release should be re-tested before widening the upper
  bound.

- **Document the supported Modi range in your plugin's README.** A line such
  as "Compatible with Modi Harness 0.4.x" saves users a trip into your
  `pyproject.toml`.

- **Treat tool-spec field additions as additive.** Modi may add optional
  `ToolSpec` fields in future versions; plugin spec dicts can ignore unknown
  fields safely. The four required fields (`name`, `description`,
  `input_schema`, `risk_level`) are guaranteed across the 0.x line.

- **Track entry-point shape changes.** The `get_plugin()` shape is part of the
  Modi public API. Backwards-incompatible changes (renaming keys, switching
  return types) will get a major version bump on Modi's side and a migration
  note here.

## Related docs

- [Agent Loader](architecture/01-agent-loader.md) — sources of agent files,
  including plugin dirs.
- [Skill Loader](architecture/02-skill-loader.md) — sources of skill packages.
- [Tool Gateway](architecture/05-tool-gateway.md) — the chain plugin tools run
  through.
- [Harness API](architecture/08-harness-api.md) — `plugins=` and
  `auto_discover_plugins=` parameters on `ModiHarness`.
- [CLI Guide](cli.md) — `modi plugins list` and other CLI subcommands.
