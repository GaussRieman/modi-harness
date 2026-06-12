"""ModiSession — V0.5 harness/agents/infra binding.

The session compiles a langgraph at construction with the harness's
governance modules + the agent set + injected infra backends. It is the
sole execution entry point. Execution methods are added in N2.3c; this file
(N2.3b) covers construction + agent registry/lookup only.

See docs/superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md §3.3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from .._utils import compute_fingerprint, now_iso
from ..graph.deps import GraphDeps
from ..graph.harness_adapter import HarnessGraphAdapter, RunTaskInput
from ..hooks import HookDispatcher
from ..memory import MemoryPaths, MemoryScopeKeys, MemoryStore
from ..tools.gateway import ToolGateway
from ..tools.registry import ToolRegistry
from ..types import (
    AgentState,
    DeniedAction,
    HookResult,
    HookSpec,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    PermissionMode,
    RunTaskResponse,
    StreamEvent,
    ThreadInfo,
    TraceEvent,
    WorkspaceRef,
)
from ..workspace import WorkspaceManager
from ._session_helpers import (
    collect_discovery_agents,
    dedupe_top_level,
    delegate_tool_spec,
    flatten_and_validate,
    index_backed_loader,
    index_backed_skill_loader,
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
        project_root_path = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
        default_scope_keys = MemoryScopeKeys(
            user_key="default",
            agent_name=self._top_level_names[0],
            workspace_key=compute_fingerprint(str(project_root_path))[:16],
            thread_id="session",
        )
        self._workspace = WorkspaceManager(workspace_root=workspace_root)
        self._memory = MemoryStore(
            MemoryPaths(
                user=memory_root_path / "user",
                workspace=memory_root_path / "workspace",
                agent=memory_root_path / "agent",
                thread=memory_root_path / "thread",
            ),
            workspace_horizon_days=90,
        )
        self._memory_scope_keys = default_scope_keys
        self._hook_dispatcher = HookDispatcher(
            registry=harness.hook_registry,
            project_root=project_root_path,
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
            skills=index_backed_skill_loader(self._agents_index),
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
            memory_scope_keys=self._memory_scope_keys,
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

    @classmethod
    def from_discovery(
        cls,
        harness: ModiHarness,
        *,
        checkpointer: BaseCheckpointSaver,
        workspace_root: Path | str,
        memory_root: Path | str,
        plugins: list | None = None,
        agents_dir: Path | str | None = None,
        extra_agents: list[ModiAgent] | None = None,
        project_root: Path | str | None = None,
        hook_pass_env: list[str] | None = None,
        max_steps: int = 20,
        repair_budget: int = 3,
    ) -> ModiSession:
        """Build a session from plugin + directory + explicit agents.

        ``plugins`` defaults to ``discover_plugins()`` when None; pass ``[]``
        to skip discovery. Merge rules (spec §3.3.2): ``plugins[*].agents`` +
        ``load_dir(agents_dir)`` + ``extra_agents`` are concatenated, then the
        §3.3.1 name-conflict / value-equal-dedupe rules apply uniformly. All
        other arguments forward to :meth:`__init__`.
        """
        from ..plugins import discover_plugins

        if plugins is None:
            plugins = discover_plugins()
        merged = collect_discovery_agents(plugins, agents_dir, extra_agents)
        return cls(
            harness=harness,
            agents=merged,
            checkpointer=checkpointer,
            workspace_root=workspace_root,
            memory_root=memory_root,
            project_root=project_root,
            hook_pass_env=hook_pass_env,
            max_steps=max_steps,
            repair_budget=repair_budget,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_task(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        options: dict[str, Any] | None = None,
        mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> RunTaskResponse:
        self._require_top_level(agent)
        response = self._adapter.run(
            RunTaskInput(
                agent=agent,
                input=input,
                options=options or {},
                permission_mode=mode,
                thread_id=thread_id,
            )
        )
        tid = response.get("thread_id")
        if tid:
            self._touch_thread(tid, agent)
        return response

    def resume_task(
        self,
        *,
        thread_id: str,
        payload: dict[str, Any] | None = None,
    ) -> RunTaskResponse:
        response = self._adapter.resume(thread_id=thread_id, payload=payload)
        if thread_id in self._threads:
            self._threads[thread_id]["last_active_at"] = now_iso()
        return response

    def approve_action(
        self,
        *,
        thread_id: str,
        approval_id: str,
        decision: str = "approved",
    ) -> RunTaskResponse:
        return self._adapter.approve(
            thread_id=thread_id, approval_id=approval_id, decision=decision
        )

    def reject_action(
        self, *, thread_id: str, approval_id: str, reason: str
    ) -> RunTaskResponse:
        return self._adapter.reject(
            thread_id=thread_id, approval_id=approval_id, reason=reason
        )

    def stream(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        options: dict[str, Any] | None = None,
        mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> Iterable[StreamEvent]:
        self._require_top_level(agent)
        for ev in self._adapter.stream(
            RunTaskInput(
                agent=agent, input=input, options=options or {},
                permission_mode=mode, thread_id=thread_id,
            )
        ):
            yield ev
            if ev["event_type"] == "terminal":
                resp = ev.get("terminal_response")
                if resp and resp.get("thread_id"):
                    self._touch_thread(resp["thread_id"], agent)

    async def astream(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        options: dict[str, Any] | None = None,
        mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._require_top_level(agent)
        async for ev in self._adapter.astream(
            RunTaskInput(
                agent=agent, input=input, options=options or {},
                permission_mode=mode, thread_id=thread_id,
            )
        ):
            yield ev
            if ev["event_type"] == "terminal":
                resp = ev.get("terminal_response")
                if resp and resp.get("thread_id"):
                    self._touch_thread(resp["thread_id"], agent)

    # ------------------------------------------------------------------
    # Introspection (thread_id keyed)
    # ------------------------------------------------------------------

    def get_state(self, thread_id: str) -> AgentState | None:
        return self._adapter.get_state(thread_id)

    def get_artifacts(self, thread_id: str) -> list[WorkspaceRef]:
        state = self._adapter.get_state(thread_id)
        if state is None:
            return []
        run_id = state.get("root_run_id") or state.get("run_id")
        if not run_id:
            return []
        return self._workspace.index_workspace(run_id)

    def get_trace(self, thread_id: str) -> Iterable[TraceEvent]:
        return self._adapter.read_trace(thread_id)

    def get_denials(self, thread_id: str) -> list[DeniedAction]:
        state = self._adapter.get_state(thread_id)
        if state is None:
            return []
        return list(state.get("denied_actions") or [])

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
    # Memory
    # ------------------------------------------------------------------

    def add_memory(self, record: dict[str, Any]) -> MemoryRecord:
        return self._memory.write_record(record, scope_keys=self._memory_scope_keys)

    def list_memory(
        self,
        *,
        scopes: Iterable[MemoryScope] | None = None,
        types: Iterable[MemoryType] | None = None,
        tags: Iterable[str] | None = None,
    ) -> list[MemoryRecord]:
        return self._memory.search(
            scopes=scopes,
            types=types,
            tags=tags,
            scope_keys=self._memory_scope_keys,
        )

    def forget_memory(self, record_id: str) -> None:
        self._memory.delete_record(record_id, scope_keys=self._memory_scope_keys)

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def list_hooks(self, thread_id: str | None = None) -> list[HookSpec]:
        del thread_id
        return self._harness.hook_registry.all()

    def get_hook_results(
        self, *, thread_id: str, event_id: str | None = None
    ) -> list[HookResult]:
        del event_id
        out: list[HookResult] = []
        for ev in self.get_trace(thread_id):
            if ev["event_type"] == "hook_dispatch":
                results = ev["payload"].get("results", [])
                if isinstance(results, list):
                    out.extend(results)
        return out

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def end_thread(self, thread_id: str) -> None:
        if thread_id in self._threads:
            self._threads[thread_id]["status"] = "closed"

    def list_threads(self) -> list[ThreadInfo]:
        return list(self._threads.values())

    # ------------------------------------------------------------------
    # Resource cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release modi-owned resources (hook dispatcher subprocesses, trace
        handles). Caller-owned infra (checkpointer, model client) is the
        caller's responsibility. Currently a no-op placeholder; explicit
        close hooks may be added to HookDispatcher/TraceMiddleware later.
        """
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_top_level(self, name: str) -> None:
        if name not in self._top_level_names:
            raise AgentNotRegistered(name, available=self._top_level_names)

    def _touch_thread(self, thread_id: str, agent: str) -> None:
        existing = self._threads.get(thread_id)
        if existing is None:
            self._threads[thread_id] = ThreadInfo(  # type: ignore[typeddict-item]
                thread_id=thread_id,
                agent_name=agent,
                created_at=now_iso(),
                last_active_at=now_iso(),
                run_count=1,
                status="open",
            )
        else:
            existing["last_active_at"] = now_iso()
            existing["run_count"] += 1

    def _register_subagent_tools(self, registry: ToolRegistry) -> None:
        """delegate_to_<name> for every NESTED subagent (not top-level)."""
        subagent_names = set(self._agents_index.keys()) - set(self._top_level_names)
        for name in subagent_names:
            if registry.has(f"delegate_to_{name}"):
                continue
            registry.register_tool(delegate_tool_spec(name), lambda **_: None)


__all__ = ["ModiSession"]
