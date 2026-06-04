"""ModiSession — V0.5 harness/agents/infra binding.

The session compiles a langgraph at construction with the harness's
governance modules + the agent set + injected infra backends. It is the
sole execution entry point. Execution methods are added in N2.3c; this file
(N2.3b) covers construction + agent registry/lookup only.

See docs/superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md §3.3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from ..graph.deps import GraphDeps
from ..graph.harness_adapter import HarnessGraphAdapter
from ..hooks import HookDispatcher
from ..memory import MemoryPaths, MemoryStore
from ..tools.gateway import ToolGateway
from ..tools.registry import ToolRegistry
from ..workspace import WorkspaceManager
from ._session_helpers import (
    dedupe_top_level,
    flatten_and_validate,
    index_backed_loader,
    merge_tool_registries,
)
from .agent import ModiAgent
from .errors import AgentNotRegistered, ModiSessionConfigError
from .harness import ModiHarness


class ModiSession:
    """Binds a ModiHarness, a list of ModiAgents, and infra into something runnable.

    Construction validates the agent graph, builds the dependency bundle, and
    compiles a langgraph once. Mutating the agent set or tool set requires a
    new ModiSession.
    """

    def __init__(
        self,
        harness: ModiHarness,
        *,
        agents: list[ModiAgent],
        checkpointer: BaseCheckpointSaver,
        workspace_root: Path | str,
        memory_root: Path | str,
        project_root: Path | str | None = None,
        hook_pass_env: list[str] | None = None,
        max_steps: int = 20,
        repair_budget: int = 3,
    ) -> None:
        if not agents:
            raise ModiSessionConfigError("ModiSession requires at least one agent")
        self._harness = harness
        self._top_level_names = [a.name for a in dedupe_top_level(agents)]
        self._agents_index = flatten_and_validate(agents)

        memory_root_path = Path(str(memory_root)).expanduser().resolve()
        self._workspace = WorkspaceManager(workspace_root=workspace_root)
        self._memory = MemoryStore(
            MemoryPaths(
                user=memory_root_path / "user",
                agent=memory_root_path / "agent",
                project=memory_root_path / "project",
                conversation=memory_root_path / "conversation",
            )
        )
        self._hook_dispatcher = HookDispatcher(
            registry=harness.hook_registry,
            project_root=Path(project_root) if project_root else Path.cwd(),
            pass_env=hook_pass_env or ["PATH", "LANG", "LC_ALL"],
        )

        merged_registry = merge_tool_registries(
            harness.builtin_tools_registry, self._agents_index
        )
        self._register_subagent_tools(merged_registry)
        self._tool_gateway = ToolGateway(
            registry=merged_registry,
            policy=harness.policy,
            hooks=self._hook_dispatcher,
            result_inline_limit_bytes=8192,
        )

        self._agent_loader = index_backed_loader(self._agents_index)

        deps = GraphDeps(
            agents=self._agent_loader,
            skills=None,
            memory=self._memory,
            workspace=self._workspace,
            context=harness.context,
            model=harness.model,
            tools=self._tool_gateway,
            policy=harness.policy,
            output=harness.output,
            hooks=self._hook_dispatcher,
            model_cache=harness.model_cache,
            agents_index=self._agents_index,
            max_steps=max_steps,
            repair_budget=repair_budget,
        )
        self._adapter = HarnessGraphAdapter(
            deps=deps,
            checkpointer=checkpointer,
            max_steps=max_steps,
            repair_budget=repair_budget,
        )
        self._threads: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Agent lookup
    # ------------------------------------------------------------------

    def get_agent(self, name: str) -> ModiAgent:
        try:
            return self._agents_index[name]
        except KeyError:
            raise AgentNotRegistered(name, available=self._top_level_names) from None

    def list_agents(self) -> list[str]:
        return list(self._top_level_names)

    def list_all_agents(self) -> list[str]:
        return list(self._agents_index.keys())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _register_subagent_tools(self, registry: ToolRegistry) -> None:
        """delegate_to_<name> for every NESTED subagent (not top-level)."""
        subagent_names = set(self._agents_index.keys()) - set(self._top_level_names)
        for name in subagent_names:
            tool_name = f"delegate_to_{name}"
            if registry.has(tool_name):
                continue
            spec = {
                "name": tool_name,
                "description": f"Delegate a bounded sub-task to the {name} agent.",
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
                "subagent_target": name,
            }
            registry.register_tool(spec, lambda **_: None)


__all__ = ["ModiSession"]
