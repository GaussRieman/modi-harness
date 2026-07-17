"""ModiSession binds Agents and infrastructure to the Workflow runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from .._utils import compute_fingerprint, now_iso
from ..checkpoint import RootCheckpointStore
from ..hooks import HookDispatcher
from ..memory import MemoryPaths, MemoryScopeKeys, MemoryStore, safe_scope_key
from ..types import (
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
from ..workflow.session import RunInputFile, RunTaskInput, WorkflowSessionAdapter
from ..workspace import WorkspaceManager
from ._session_helpers import (
    collect_discovery_agents,
    dedupe_top_level,
    flatten_and_validate,
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
        checkpointer: BaseCheckpointSaver[Any],
        workspace_root: Path | str,
        memory_root: Path | str,
        project_root: Path | str | None = None,
        hook_pass_env: list[str] | None = None,
        max_steps: int = 20,
        repair_budget: int = 3,
        root_checkpoint_store: RootCheckpointStore | None = None,
    ) -> None:
        if not agents:
            raise ModiSessionConfigError("ModiSession requires at least one agent")
        self._harness = harness
        self._top_level_names = [a.name for a in dedupe_top_level(agents)]
        self._agents_index = flatten_and_validate(agents)
        if _has_task_graph(self._agents_index.values()) and root_checkpoint_store is None:
            raise ModiSessionConfigError(
                "Task-Graph-enabled Agents require a shared root checkpoint store"
            )

        memory_root_path = Path(str(memory_root)).expanduser().resolve()
        project_root_path = (
            Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
        )
        workspace_root_path = Path(str(workspace_root)).expanduser()
        default_scope_keys = MemoryScopeKeys(
            user_key="default",
            agent_name=self._top_level_names[0],
            workspace_key=_derive_workspace_key(workspace_root_path, project_root_path),
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

        merged_registry = merge_tool_registries(harness.builtin_tools_registry, self._agents_index)
        self._adapter = WorkflowSessionAdapter(
            agents=self._agents_index,
            tools=merged_registry,
            policy=harness.policy,
            hooks=self._hook_dispatcher,
            model=harness.model,
            output=harness.output,
            checkpointer=checkpointer,
            workspace=self._workspace,
            memory=self._memory,
            memory_scope_keys=self._memory_scope_keys,
            max_steps=max_steps,
            root_checkpoint_store=root_checkpoint_store,
        )
        self._threads: dict[str, Any] = {}

    @classmethod
    def from_discovery(
        cls,
        harness: ModiHarness,
        *,
        checkpointer: BaseCheckpointSaver[Any],
        workspace_root: Path | str,
        memory_root: Path | str,
        plugins: list[Any] | None = None,
        agents_dir: Path | str | None = None,
        extra_agents: list[ModiAgent] | None = None,
        project_root: Path | str | None = None,
        hook_pass_env: list[str] | None = None,
        max_steps: int = 20,
        repair_budget: int = 3,
        root_checkpoint_store: RootCheckpointStore | None = None,
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
            root_checkpoint_store=root_checkpoint_store,
        )

    @classmethod
    def from_registry(
        cls,
        harness: ModiHarness,
        *,
        registry: Any,
        agent: str,
        checkpointer: BaseCheckpointSaver[Any],
        workspace_root: Path | str,
        memory_root: Path | str,
        project_root: Path | str | None = None,
        hook_pass_env: list[str] | None = None,
        max_steps: int = 20,
        repair_budget: int = 3,
        root_checkpoint_store: RootCheckpointStore | None = None,
    ) -> ModiSession:
        """Resolve one runnable Agent from a discovery registry and bind a session."""
        descriptor = registry.resolve(agent)
        return cls(
            harness=harness,
            agents=[descriptor.agent],
            checkpointer=checkpointer,
            workspace_root=workspace_root,
            memory_root=memory_root,
            project_root=project_root,
            hook_pass_env=hook_pass_env,
            max_steps=max_steps,
            repair_budget=repair_budget,
            root_checkpoint_store=root_checkpoint_store,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_task(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        workflow_id: str | None = None,
        options: dict[str, Any] | None = None,
        inputs: Iterable[RunInputFile | dict[str, Any]] | None = None,
        mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> RunTaskResponse:
        self._require_top_level(agent)
        response = self._adapter.run(
            RunTaskInput(
                agent=agent,
                input=input,
                workflow_id=workflow_id,
                inputs=list(inputs or []),
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

    def respond_to_judgment(
        self,
        *,
        thread_id: str,
        judgment_id: str,
        kind: str,
        rationale: str | None = None,
        intent_updates: dict[str, Any] | None = None,
    ) -> RunTaskResponse:
        """Resume an interrupted run with a human judgment — the primary HITL API.

        Human participation is judgment, not just approval. ``kind`` is one of
        ``approve`` / ``reject`` / ``revise`` / ``redirect`` / ``constrain`` /
        ``clarify`` / ``cancel``. ``approve`` authorizes the reviewed action;
        every other kind declines it. Any kind may carry ``intent_updates`` (an
        ``IntentPatch``) to edit the live intent field; clarity and autonomy are
        recomputed and the agent re-plans under the corrected intent.
        """
        response = self._adapter.respond_to_judgment(
            thread_id=thread_id,
            judgment_id=judgment_id,
            kind=kind,
            rationale=rationale,
            intent_updates=intent_updates,
        )
        if thread_id in self._threads:
            self._threads[thread_id]["last_active_at"] = now_iso()
        return response

    def respond_to_interaction(
        self,
        *,
        thread_id: str,
        interaction_id: str,
        decision: str,
        feedback: str | None = None,
        value: Any = None,
    ) -> RunTaskResponse:
        payload: dict[str, Any] = {
            "interaction_id": interaction_id,
            "decision": decision,
        }
        if feedback is not None:
            payload["feedback"] = feedback
        if value is not None:
            payload["value"] = value
        return self.resume_task(thread_id=thread_id, payload=payload)

    def stream(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        workflow_id: str | None = None,
        options: dict[str, Any] | None = None,
        inputs: Iterable[RunInputFile | dict[str, Any]] | None = None,
        mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> Iterable[StreamEvent]:
        self._require_top_level(agent)
        for ev in self._adapter.stream(
            RunTaskInput(
                agent=agent,
                input=input,
                workflow_id=workflow_id,
                inputs=list(inputs or []),
                options=options or {},
                permission_mode=mode,
                thread_id=thread_id,
            )
        ):
            yield ev
            if ev["event_type"] == "terminal":
                resp = ev.get("terminal_response")
                tid = resp.get("thread_id") if resp else None
                if isinstance(tid, str):
                    self._touch_thread(tid, agent)

    async def astream(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        workflow_id: str | None = None,
        options: dict[str, Any] | None = None,
        inputs: Iterable[RunInputFile | dict[str, Any]] | None = None,
        mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._require_top_level(agent)
        async for ev in self._adapter.astream(
            RunTaskInput(
                agent=agent,
                input=input,
                workflow_id=workflow_id,
                inputs=list(inputs or []),
                options=options or {},
                permission_mode=mode,
                thread_id=thread_id,
            )
        ):
            yield ev
            if ev["event_type"] == "terminal":
                resp = ev.get("terminal_response")
                tid = resp.get("thread_id") if resp else None
                if isinstance(tid, str):
                    self._touch_thread(tid, agent)

    async def astream_resume(
        self,
        *,
        thread_id: str,
        payload: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Resume an interrupted thread and stream its remaining execution."""
        async for ev in self._adapter.astream_resume(thread_id=thread_id, payload=payload):
            yield ev
            if ev["event_type"] == "terminal":
                if thread_id in self._threads:
                    self._threads[thread_id]["last_active_at"] = now_iso()

    # ------------------------------------------------------------------
    # Introspection (thread_id keyed)
    # ------------------------------------------------------------------

    def get_state(self, thread_id: str) -> dict[str, Any] | None:
        return self._adapter.get_state(thread_id)

    def get_task_plan(self, thread_id: str) -> dict[str, Any] | None:
        state = self._adapter.get_state(thread_id)
        if state is None:
            return None
        return state.get("task_plan") or state.get("pending_task_plan")

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

    def get_hook_results(self, *, thread_id: str, event_id: str | None = None) -> list[HookResult]:
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
            self._threads[thread_id] = ThreadInfo(
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


__all__ = ["ModiSession"]


_GENERIC_WORKSPACE_ROOT_NAMES = {"", ".", ".modi", "workspace", "workspaces", "ws"}


def _has_task_graph(agents: Iterable[ModiAgent]) -> bool:
    return any(
        node.execution == "task_graph"
        for agent in agents
        for workflow in agent.workflows
        for node in workflow.nodes
    )


def _derive_workspace_key(workspace_root: Path, project_root: Path) -> str:
    """Return a readable workspace key when the run-file root has a real name."""
    key = safe_scope_key(workspace_root.name)
    if key and key not in _GENERIC_WORKSPACE_ROOT_NAMES:
        return key
    return compute_fingerprint(str(project_root.resolve()))[:16]
