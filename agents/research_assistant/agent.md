---
name: research-assistant
description: Compatibility note for the split Brain-loop package.
---
This Agent has moved to the split Brain-loop package format.

- `agent.toml` declares identity, skills, permissions, task protocol, and output contract.
- `brain.md` contains the slow Brain control instruction.
- `brain.toml`, `rules.toml`, `stages.toml`, `intent.toml`, and `loop.toml` declare control metadata.
- `runtime.py` remains the trusted project factory that binds Python tool handlers.

Load this Agent through `agent.toml` / project discovery, not this compatibility note.
