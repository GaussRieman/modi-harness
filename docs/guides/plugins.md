# Modi Harness Plugin Author Guide

This guide explains how to ship a Modi Harness plugin: a `pip install`-able
Python package that contributes **agents** and **kernel-scoped tools** to any
session it is discovered by. Discovery uses Python's standard
[`importlib.metadata` entry points](https://docs.python.org/3/library/importlib.metadata.html#entry-points),
so authors do not need to interact with Modi internals beyond a single
`get_plugin()` callable.

## Overview

A Modi plugin contributes two kinds of artefacts. Critically, **the plugin
parses its own files** — modi never reaches into a plugin's filesystem. Plugins
hand modi already-constructed `ModiAgent` objects, not directories.

| Manifest key | What it adds |
|---|---|
| `agents` | `list[ModiAgent]`. The plugin builds these itself (typically via `ModiAgent.load_dir(...)` or `ModiAgent.from_markdown(...)`). Each becomes a registered agent; declared subagents get a `delegate_to_<name>` tool automatically. |
| `kernel_tools` | `list[ToolBinding]`. New **kernel-scoped** tools contributed to the harness builtin set. They obey the full Tool Gateway chain (visibility, hooks, Policy Gate, trust annotation). |

> **`kernel_tools` vs `ModiHarness(builtin_tools=...)`:** `kernel_tools` adds
> kernel-scoped Tools explicitly; `builtin_tools=` filters only Modi's default
> builtin set. Plugin kernel Tools are not filtered by that whitelist.

A single plugin may contribute either subset (agents only, tools only, or both).
Discovery is **opt-in**: nothing runs at `ModiHarness(...)` construction. A
caller pulls plugins in explicitly via `ModiSession.from_discovery(...)` (or by
calling `discover_plugins()` directly). Discovery is fail-fast: if any installed
plugin fails to import, validate, or call cleanly, `discover_plugins()` raises
`PluginLoadError`.

## Package structure

A typical plugin package looks like this:

```text
my-modi-plugin/
├── pyproject.toml
└── modi_my_plugin/
    ├── __init__.py
    └── agents/
        └── my-agent.md
```

The package name (`modi_my_plugin`) is your choice — only the entry point name
matters for discovery. By convention, plugin packages start with `modi_` so
they are easy to spot in a Python environment.

## The `get_plugin` function

Every plugin exposes one zero-argument callable that returns a dict — the plugin
manifest. The plugin constructs its own `ModiAgent` / `ToolBinding` objects:

```python
# modi_my_plugin/__init__.py
from pathlib import Path
from typing import Any

from modi_harness import ModiAgent, ToolBinding

_PKG = Path(__file__).parent


def my_tool_handler(**kwargs: Any) -> dict[str, Any]:
    return {"result": "ok"}


def get_plugin() -> dict:
    return {
        "name": "my-plugin",
        # plugin parses its own markdown — modi never reads plugin files
        "agents": ModiAgent.load_dir(_PKG / "agents"),
        "kernel_tools": [
            ToolBinding(
                spec={
                    "name": "my_tool",
                    "description": "Does X.",
                    "input_schema": {"type": "object", "properties": {}},
                    "risk_level": "L1",
                },
                handler=my_tool_handler,
            ),
        ],
    }
```

Required keys:

| Key | Type | Notes |
|---|---|---|
| `name` | `str` | Plugin identifier, used in `modi plugins list` output and error messages. Must be a non-empty string. |

Optional keys (omit when not used; default to empty lists):

| Key | Type | Notes |
|---|---|---|
| `agents` | `list[ModiAgent]` | Each item must be a `ModiAgent`. Build via `ModiAgent.load_dir(...)` / `ModiAgent.from_markdown(...)` / direct construction. |
| `kernel_tools` | `list[ToolBinding]` | Each item is a `ToolBinding(spec, handler)`. The legacy `(spec, handler)` tuple is also accepted and normalized via `ToolBinding.from_tuple`. The spec dict must contain `name`, `description`, `input_schema`, `risk_level`. |

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
    "modi-harness>=0.5.0,<0.6",
]

[project.entry-points."modi_harness.plugins"]
my-plugin = "modi_my_plugin:get_plugin"
```

The entry point key (`my-plugin` above) is what shows up in
`modi plugins list`; the value is `<module_path>:<callable>`. A single package
may register multiple entry points if it ships logically separate plugins.

## Two example shapes

### Agents-only plugin

A package that only ships agent definitions (no custom tools):

```python
# modi_my_agents/__init__.py
from pathlib import Path

from modi_harness import ModiAgent


def get_plugin() -> dict:
    return {
        "name": "my-agents",
        "agents": ModiAgent.load_dir(Path(__file__).parent / "agents"),
    }
```

### Tools-only plugin

A package that adds kernel-scoped tool functions but no agent content:

```python
# modi_my_tools/__init__.py
from typing import Any

from modi_harness import ToolBinding


def lookup_user(*, user_id: str) -> dict[str, Any]:
    # ...real implementation...
    return {"user_id": user_id, "status": "active"}


def get_plugin() -> dict:
    return {
        "name": "my-tools",
        "kernel_tools": [
            ToolBinding(
                spec={
                    "name": "lookup_user",
                    "description": "Fetch user profile by id.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"user_id": {"type": "string"}},
                        "required": ["user_id"],
                    },
                    "risk_level": "L1",
                },
                handler=lookup_user,
            ),
        ],
    }
```

## Consuming plugins

Plugins are pulled in on the **session** side (agents belong to sessions, not to
the capability suite). The common path is `ModiSession.from_discovery`:

```python
from modi_harness import ModiHarness, ModiSession
from langgraph.checkpoint.memory import MemorySaver

harness = ModiHarness(chat_model=my_chat_model)

session = ModiSession.from_discovery(
    harness,
    checkpointer=MemorySaver(),
    workspace_root=".modi/workspace",
    memory_root="~/.modi/memory",
    plugins=None,            # None → discover_plugins() scans installed entry points
    agents_dir="./agents",   # optional: also load a local directory of agents
)
```

`from_discovery` concatenates `plugins[*].agents` +
`ModiAgent.load_dir(agents_dir)` + `extra_agents` into one agent list, then
applies the standard name-conflict / value-equal-dedupe rules. Plugin
`kernel_tools` are merged into the harness builtin set for the session. To
inspect what is installed without building a session, call `discover_plugins()`
directly.

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

    You should see your plugin listed with the agent / tool counts you expect:

    ```text
    Discovered plugins:
      my-plugin (entry_point:modi-my-plugin v0.1.0)
        agents: 1 (my-agent)
        tools:  1 (my_tool)

    (1 plugin, 1 agent, 1 tool)
    ```

3. Run an agent contributed by the plugin:

    ```bash
    modi run --agent my-agent --task task.json
    ```

If the plugin manifest is malformed, `modi plugins list` (and any
`discover_plugins()` call) prints a `PluginLoadError` describing what went wrong
and the source the plugin came from.

## Validation rules

The validator (`modi_harness.plugins._validate_plugin_dict`) enforces the
following at discovery time:

- `name` must be a non-empty string.
- If `agents` is provided, it must be a `list` and every item must be a
  `ModiAgent` instance.
- If `kernel_tools` is provided, it must be a `list`; each item must be a
  `ToolBinding` or a `(spec, handler)` tuple (normalized via
  `ToolBinding.from_tuple`).
- Each tool spec dict must contain at minimum `name`, `description`,
  `input_schema`, `risk_level`. (Other `ToolSpec` fields are optional.)
- Tool name collisions across plugins (or with the host harness's builtin
  tools) raise an error at registration time.

Validation failures are reported as `PluginLoadError` with the plugin name,
provenance (`entry_point:<dist> v<version>`), and a descriptive message. The
error is fail-fast: a single broken plugin aborts discovery, so problems surface
during `pip install` testing rather than silently in production.

## Migration from V0.4c

| V0.4c manifest | V0.5 manifest |
|---|---|
| `agents_dir: Path` | `agents: list[ModiAgent]` — plugin runs its own `ModiAgent.load_dir(...)` |
| `skills_dir: Path` | removed — skills attach to a `ModiAgent`, not a plugin dir |
| `tools: [(spec, handler), ...]` | `kernel_tools: list[ToolBinding]` |
| discovery auto-runs in `ModiHarness(...)` | discovery is opt-in via `ModiSession.from_discovery(...)` / `discover_plugins()` |

modi no longer crosses into a plugin's filesystem; the plugin owns all parsing.

## Versioning guidance

Modi Harness uses semantic versioning at the minor level — additive features
land in minor versions, breaking changes to public contracts move the minor/major.
For plugins:

- **Pin a Modi version range in your dependencies.**

    ```toml
    dependencies = [
        "modi-harness>=0.5.0,<0.6",
    ]
    ```

  A new Modi minor release should be re-tested before widening the upper
  bound.

- **Document the supported Modi range in your plugin's README.**

- **Treat tool-spec field additions as additive.** The four required fields
  (`name`, `description`, `input_schema`, `risk_level`) are guaranteed across
  the line.

- **Track manifest-shape changes.** The `get_plugin()` shape is part of the Modi
  public API. Backwards-incompatible changes will get a version bump and a
  migration note here.

## Related docs

- [Agent and Skill](../architecture/agent-and-skill.md) — markdown → `ModiAgent`.
- [Tools and Policy](../architecture/tools-and-policy.md) — the chain plugin tools run
  through.
- [Execution Runtime](../architecture/execution-runtime.md) — the three-object model and
  `ModiSession.from_discovery`.
- [CLI Guide](cli.md) — `modi plugins list` and other CLI subcommands.
