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
    metadata = dict(agent.metadata)
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
    return {
        "name": agent.name,
        "description": agent.description,
        "instruction": agent.instruction,
        "default_tools": tb_names + fm_names,
        "default_skills": [s.name for s in agent.skills],
        "output_contract": agent.output_contract,
        "permission_profile": agent.permission_profile,
        "safety_constraints": list(agent.safety_constraints),
        "tags": [],
        "metadata": metadata,
    }


def index_backed_skill_loader(agents_index: dict[str, ModiAgent]):
    """Build a SkillLoader-shaped shim serving LoadedSkill profiles from the
    Skill objects attached to registered ModiAgents.

    Graph nodes call ``deps.skills.load_skills(names) -> list[LoadedSkill]``.
    We collect every agent's Skill objects into a name→LoadedSkill map and
    serve from it. Returns None if no agent declares any skill (so the node's
    ``if not deps.skills`` short-circuit keeps working with zero overhead).
    """
    skill_map: dict[str, Any] = {}
    for agent in agents_index.values():
        for sk in agent.skills:
            skill_map.setdefault(sk.name, sk.profile)
    if not skill_map:
        return None

    from ..skills import SkillLoader

    loader = SkillLoader()

    def _load(names: list[str]) -> list:
        return [skill_map[n] for n in names if n in skill_map]

    loader.load_skills = _load  # type: ignore[assignment]
    return loader


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


def delegate_tool_spec(target: str) -> dict[str, Any]:
    """Build the ``delegate_to_<target>`` subagent tool spec for one subagent."""
    return {
        "name": f"delegate_to_{target}",
        "description": f"Delegate a bounded sub-task to the {target} agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "object"},
                "permission_mode": {
                    "type": "string",
                    "enum": ["ask", "auto", "plan", "bypass", "preview", "trust"],
                },
                "rationale": {"type": "string"},
            },
            "required": ["task", "rationale"],
        },
        "risk_level": "L2",
        "side_effect": True,
        "kind": "subagent",
        "subagent_target": target,
    }


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
    "delegate_tool_spec",
    "flatten_and_validate",
    "index_backed_loader",
    "index_backed_skill_loader",
    "merge_tool_registries",
]
