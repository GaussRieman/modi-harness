"""Dependency bundle passed into LangGraph nodes via ``RunnableConfig``.

Nodes are pure functions; they receive their collaborators through this
struct rather than reaching into globals. Holding it in a dataclass keeps
:func:`build_main_graph` calls explicit and gives test doubles a single seam
to swap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..agents import AgentLoader
    from ..context import ContextManager
    from ..hooks import HookDispatcher
    from ..memory import MemoryScopeKeys, MemoryStore
    from ..memory.recall_cache import RunRecallCache
    from ..models import ModelAdapter, ModelAdapterCache
    from ..output import OutputController
    from ..policy import PolicyGate
    from ..skills import SkillLoader
    from ..tools import ToolGateway
    from ..workspace import WorkspaceManager


@dataclass
class GraphDeps:
    agents: AgentLoader
    skills: SkillLoader | None
    memory: MemoryStore
    workspace: WorkspaceManager
    context: ContextManager
    model: ModelAdapter
    tools: ToolGateway
    policy: PolicyGate
    output: OutputController
    hooks: HookDispatcher
    max_steps: int = 20
    repair_budget: int = 3
    subagent_max_depth: int = 3
    trace_redact_keys: tuple[str, ...] = ("api_key", "authorization", "password", "secret")
    trace_payload_inline_limit_bytes: int = 2048
    model_cache: ModelAdapterCache | None = None
    memory_scope_keys: MemoryScopeKeys | None = None
    recall_cache: RunRecallCache | None = None
    # V0.5: ModiAgent lookup for graph nodes that need agent metadata without
    # going through markdown re-parse. Populated by ModiSession; None when the
    # graph runs against a pure AgentLoader (legacy).
    agents_index: dict[str, Any] | None = None


CONFIG_DEPS_KEY = "modi_deps"


def deps_from_config(config: dict) -> GraphDeps:
    cfg = config.get("configurable") or {}
    deps = cfg.get(CONFIG_DEPS_KEY)
    if deps is None:
        raise RuntimeError(
            "GraphDeps missing from RunnableConfig.configurable[modi_deps]"
        )
    return deps
