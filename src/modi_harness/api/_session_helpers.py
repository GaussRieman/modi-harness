"""Pure helpers for ModiSession agent-graph assembly (V0.5).

Separated from session.py so the flatten/dedupe/projection logic is unit-
testable without constructing infra. See spec §3.3.1.
"""

from __future__ import annotations

from typing import Any

from ..agents import AgentLoader
from ..tools.registry import ToolRegistry
from .agent import ModiAgent
from .errors import AgentNameConflict


def dedupe_top_level(agents: list[ModiAgent]) -> list[ModiAgent]:
    """Dedupe top-level agents by name; conflicting non-equal entries raise."""
    seen: dict[str, ModiAgent] = {}
    for a in agents:
        if a.name in seen:
            if seen[a.name] != a:
                raise AgentNameConflict(a.name, "two non-equal top-level agents")
            continue
        seen[a.name] = a
    return list(seen.values())


def flatten_and_validate(agents: list[ModiAgent]) -> dict[str, ModiAgent]:
    """Walk subagents recursively into a flat name→agent index.

    Same-name + equal content → silent dedupe.
    Same-name + non-equal content → AgentNameConflict.
    """
    index: dict[str, ModiAgent] = {}

    def visit(a: ModiAgent) -> None:
        existing = index.get(a.name)
        if existing is None:
            index[a.name] = a
        elif existing != a:
            raise AgentNameConflict(a.name, "two non-equal agents share this name")
        for child in a.subagents:
            visit(child)

    for top in dedupe_top_level(agents):
        visit(top)
    return index


def agent_to_profile(agent: ModiAgent) -> dict[str, Any]:
    """Project a ModiAgent into an AgentProfile-shaped dict for graph nodes."""
    return {
        "name": agent.name,
        "description": agent.description,
        "instruction": agent.instruction,
        "default_tools": [tb.spec["name"] for tb in agent.tools],
        "default_skills": [s.name for s in agent.skills],
        "output_contract": agent.output_contract,
        "permission_profile": agent.permission_profile,
        "safety_constraints": list(agent.safety_constraints),
        "tags": [],
        "metadata": dict(agent.metadata),
    }


def index_backed_loader(index: dict[str, ModiAgent]) -> AgentLoader:
    """Return an AgentLoader-shaped object serving from a pre-built index.

    Graph nodes call ``loader.load_agent(name) -> AgentProfile`` and
    ``loader.list_agent_names() -> list[str]``. We satisfy both by projecting
    ModiAgent → AgentProfile on demand. Transitional shim: a future cleanup
    can teach nodes to read ``deps.agents_index`` directly.
    """
    loader = AgentLoader()

    def _load(name: str) -> dict[str, Any]:
        agent = index.get(name)
        if agent is None:
            from ..agents.errors import AgentNotFoundError
            raise AgentNotFoundError(f"agent '{name}' not in session")
        return agent_to_profile(agent)

    def _list_names() -> list[str]:
        return sorted(index.keys())

    loader.load_agent = _load  # type: ignore[assignment]
    loader.list_agent_names = _list_names  # type: ignore[assignment]
    return loader


def merge_tool_registries(
    builtin_registry: ToolRegistry,
    agents_index: dict[str, ModiAgent],
) -> ToolRegistry:
    """Merge kernel builtins + per-agent scoped tools into one ToolRegistry.

    Agent-scoped tools get ``allowed_agents = [agent_name]`` so the existing
    gateway visibility check restricts them to their owner. Builtins have no
    such restriction (visible to all).
    """
    merged = ToolRegistry()
    for name in builtin_registry.names():
        entry = builtin_registry.get_entry(name)
        merged.register_tool(dict(entry.spec), entry.handler, dry_run=entry.dry_run)

    seen: set[str] = set(merged.names())
    for agent in agents_index.values():
        for tb in agent.tools:
            tool_name = tb.spec["name"]
            if tool_name in seen:
                continue
            spec = dict(tb.spec)
            # Restrict this tool's visibility to the owning agent.
            existing_allowed = spec.get("allowed_agents") or []
            if agent.name not in existing_allowed:
                spec["allowed_agents"] = [*existing_allowed, agent.name]
            merged.register_tool(spec, tb.handler, dry_run=tb.dry_run)
            seen.add(tool_name)
    return merged


__all__ = [
    "agent_to_profile",
    "dedupe_top_level",
    "flatten_and_validate",
    "index_backed_loader",
    "merge_tool_registries",
]
