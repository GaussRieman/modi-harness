"""Pure helpers for ModiSession agent-graph assembly (V0.5).

Separated from session.py so the flatten/dedupe/projection logic is unit-
testable without constructing infra. See spec §3.3.1.
"""

from __future__ import annotations

from typing import Any

from ..tasks import TASK_PROTOCOL_TOOL_NAMES
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
    """Return the unique top-level Agent index."""

    return {agent.name: agent for agent in dedupe_top_level(agents)}


def agent_to_profile(agent: ModiAgent) -> dict[str, Any]:
    """Project a ModiAgent into an AgentProfile-shaped dict for graph nodes."""
    metadata = dict(agent.metadata)
    metadata["task_protocol"] = {
        "mode": agent.task_protocol.mode,
        "review": agent.task_protocol.review,
        "min_items": agent.task_protocol.min_items,
        "max_items": agent.task_protocol.max_items,
    }
    metadata["interaction_protocol"] = {
        "startup": agent.interaction_protocol.startup,
    }
    if agent.model_override is not None:
        ms = agent.model_override
        # Match the dict shape the old frontmatter `model:` block produced,
        # which ModelAdapterCache.get_or_create consumes.
        model_dict: dict[str, Any] = {"provider": ms.provider, "name": ms.name}
        if ms.api_key is not None:
            model_dict["api_key"] = ms.api_key
        if ms.base_url is not None:
            model_dict["base_url"] = ms.base_url
        if ms.extra:
            model_dict.update(ms.extra)
        metadata["model"] = model_dict
    tb_names = [tb.spec["name"] for tb in agent.tools]
    # Frontmatter-declared tools (e.g. delegate_to_*) stay in metadata as
    # plain name strings — no ToolBinding wrapper because they're registered
    # in the session's tool gateway and resolved by name at call time.
    fm_names = list(metadata.pop("_frontmatter_tools", ()))
    default_tools = tb_names + fm_names
    if agent.task_protocol.mode != "off":
        default_tools.extend(name for name in TASK_PROTOCOL_TOOL_NAMES if name not in default_tools)
    if agent.interaction_protocol.startup == "agent" and "request_user_input" not in default_tools:
        default_tools.append("request_user_input")
    return {
        "name": agent.name,
        "description": agent.description,
        "instruction": agent.instruction,
        "default_tools": default_tools,
        "default_skills": [s.name for s in agent.skills],
        "output_contract": agent.output_contract,
        "permission_profile": agent.permission_profile,
        "safety_constraints": list(agent.safety_constraints),
        "tags": [],
        "workflows": list(agent.workflows),
        "metadata": metadata,
    }


def merge_tool_registries(
    builtin_registry: ToolRegistry,
    agents_index: dict[str, ModiAgent],
) -> ToolRegistry:
    """Merge kernel builtins + per-agent scoped tools into one ToolRegistry.

    Every tool ends up in one flat registry. Per-agent scoping is enforced
    elsewhere: ``agent_to_profile`` projects each ModiAgent's tool names into
    its ``default_tools``, and the tool gateway / policy gate gate visibility
    on ``default_tools``. So agent A simply never has agent B's tool in its
    profile and cannot call it. Builtins are visible to all agents because
    they are injected into every agent's effective tool set by the kernel.

    A tool name registered by an earlier agent wins; later duplicates are
    skipped (first-writer-wins) to keep the registry single-valued.
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
            merged.register_tool(dict(tb.spec), tb.handler, dry_run=tb.dry_run)
            seen.add(tool_name)
    return merged


def collect_discovery_agents(
    plugins: list[Any],
    agents_dir: Any | None,
    extra_agents: list[ModiAgent] | None,
) -> list[ModiAgent]:
    """Merge plugin agents + a directory of agents + explicit extras into one
    list, in that order. Conflict/dedupe is applied later by ModiSession."""
    from pathlib import Path

    merged: list[ModiAgent] = []
    for p in plugins:
        merged.extend(p.get("agents", []))
    if agents_dir is not None:
        merged.extend(ModiAgent.load_dir(Path(agents_dir)))
    if extra_agents:
        merged.extend(extra_agents)
    return merged


__all__ = [
    "agent_to_profile",
    "collect_discovery_agents",
    "dedupe_top_level",
    "flatten_and_validate",
    "merge_tool_registries",
]
