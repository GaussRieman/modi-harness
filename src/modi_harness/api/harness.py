"""ModiHarness facade — V0.2.

Thin wrapper over a single :class:`HarnessGraphAdapter` (which itself wraps a
LangGraph compiled graph + checkpointer). Threads are persisted by the
checkpointer; the harness keeps light per-thread metadata (created_at,
last_active_at) in memory for ``list_threads()`` until V0.3 indexes the
checkpointer directly.

Breaking changes from V0.1:
- Introspection (``get_state``, ``get_artifacts``, ``get_trace``,
  ``get_denials``) is keyed by ``thread_id`` instead of ``run_id``.
- ``approve_action`` / ``reject_action`` take ``thread_id`` instead of
  ``run_id``.
- ``start_thread`` is removed; threads are implicit on first ``run_task``.
- ``resume_task(thread_id, payload=None)`` is the canonical way to feed a
  ``Command(resume=...)`` payload back into the graph.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from .._utils import now_iso
from ..agents import AgentLoader
from ..config.settings import Settings
from ..context import ContextManager
from ..graph import GraphDeps
from ..graph.harness_adapter import HarnessGraphAdapter, RunTaskInput
from ..hooks import HookDispatcher, HookRegistry
from ..memory import MemoryPaths, MemoryStore
from ..models import ModelAdapter, ModelAdapterCache
from ..output import OutputController
from ..plugins import PluginInfo
from ..policy import PolicyGate
from ..policy.permissions import load_permissions
from ..skills import SkillLoader
from ..tools import ToolGateway, ToolRegistry
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
    ThreadInfo,
    TraceEvent,
    WorkspaceRef,
)
from ..workspace import WorkspaceManager


class ModiHarness:
    """The single public entry point for V0.2."""

    def __init__(
        self,
        *,
        agents_dir: Path | str | None = None,
        skills_dir: Path | str | None = None,
        workspace_root: Path | str = ".modi/workspace",
        memory_root: Path | str = "~/.modi/memory",
        rule_packs: list[str] | None = None,
        chat_model: BaseChatModel | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        max_steps: int = 20,
        repair_budget: int = 3,
        hook_user_settings: Path | str | None = None,
        hook_project_settings: Path | str | None = None,
        hook_pass_env: list[str] | None = None,
        plugins: list[PluginInfo] | None = None,
        auto_discover_plugins: bool = True,
        enable_builtin_tools: bool = True,
        builtin_tools: list[str] | None = None,
    ) -> None:
        if plugins is None:
            if auto_discover_plugins:
                from ..plugins import discover_plugins

                plugins = discover_plugins()
            else:
                plugins = []
        self._plugins = plugins
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
        self._policy = PolicyGate(
            rule_packs=rule_packs,
            permissions=load_permissions(
                user_settings=hook_user_settings,
                project_settings=hook_project_settings,
            ),
        )
        self._tools_registry = ToolRegistry()
        if enable_builtin_tools:
            self._register_builtin_tools(only=builtin_tools)
        self._hook_registry = HookRegistry.from_files(
            user_settings=hook_user_settings,
            project_settings=hook_project_settings,
        )
        self._hooks = HookDispatcher(
            registry=self._hook_registry,
            project_root=Path.cwd(),
            pass_env=hook_pass_env or ["PATH", "LANG", "LC_ALL"],
        )
        self._tool_gateway = ToolGateway(
            registry=self._tools_registry,
            policy=self._policy,
            hooks=self._hooks,
            result_inline_limit_bytes=8192,
        )
        self._context = ContextManager(policy=self._policy)
        self._model = ModelAdapter(chat_model=chat_model)
        # Per-agent provider override cache. Uses the harness's primary
        # ModelAdapter as the default (so existing chat_model wiring still
        # applies for agents without a per-agent ``model:`` block).
        try:
            settings = Settings()
        except Exception:
            settings = None  # pragma: no cover — defensive
        self._model_cache = (
            ModelAdapterCache(settings.model, default_adapter=self._model)
            if settings is not None
            else None
        )
        self._output = OutputController()
        plugin_agent_dirs = [p["agents_dir"] for p in plugins if p.get("agents_dir")]
        self._agent_loader = AgentLoader(
            project_dir=agents_dir, plugin_dirs=plugin_agent_dirs
        )
        plugin_skill_dirs = [p["skills_dir"] for p in plugins if p.get("skills_dir")]
        self._skill_loader = (
            SkillLoader(project_dir=skills_dir, plugin_dirs=plugin_skill_dirs)
            if (skills_dir or plugin_skill_dirs)
            else None
        )
        # Register plugin-contributed tools so subagent auto-registration
        # can see plugin agents and create delegate_to_<plugin-agent> tools.
        for plugin in plugins:
            for spec, handler in plugin.get("tools", []):
                self._tools_registry.register_tool(spec, handler)
        # Auto-register delegate_to_<agent> tools for every discovered agent.
        self._register_subagent_tools()
        deps = GraphDeps(
            agents=self._agent_loader,
            skills=self._skill_loader,
            memory=self._memory,
            workspace=self._workspace,
            context=self._context,
            model=self._model,
            tools=self._tool_gateway,
            policy=self._policy,
            output=self._output,
            hooks=self._hooks,
            model_cache=self._model_cache,
        )
        self._runtime = HarnessGraphAdapter(
            deps=deps,
            checkpointer=checkpointer or MemorySaver(),
            max_steps=max_steps,
            repair_budget=repair_budget,
        )
        self._threads: dict[str, ThreadInfo] = {}

    # ------------------------------------------------------------------
    # tool registration
    # ------------------------------------------------------------------

    def register_tool(
        self,
        spec: dict[str, Any],
        handler: Callable[..., Any],
        *,
        dry_run: Callable[..., Any] | None = None,
    ) -> None:
        self._tools_registry.register_tool(spec, handler, dry_run=dry_run)

    # ------------------------------------------------------------------
    # run lifecycle
    # ------------------------------------------------------------------

    def run_task(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        options: dict[str, Any] | None = None,
        mode: PermissionMode | None = None,
        permission_mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> RunTaskResponse:
        chosen_mode = mode if mode is not None else permission_mode
        response = self._runtime.run(
            RunTaskInput(
                agent=agent,
                input=input,
                options=options or {},
                permission_mode=chosen_mode,
                thread_id=thread_id,
            )
        )
        tid = response["thread_id"]
        if tid:
            self._touch_thread(tid, agent)
        return response

    def resume_task(
        self,
        *,
        thread_id: str,
        payload: dict[str, Any] | None = None,
    ) -> RunTaskResponse:
        response = self._runtime.resume(thread_id=thread_id, payload=payload)
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
        return self._runtime.approve(
            thread_id=thread_id, approval_id=approval_id, decision=decision
        )

    def reject_action(
        self,
        *,
        thread_id: str,
        approval_id: str,
        reason: str,
    ) -> RunTaskResponse:
        return self._runtime.reject(
            thread_id=thread_id, approval_id=approval_id, reason=reason
        )

    def stream(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        options: dict[str, Any] | None = None,
        mode: PermissionMode | None = None,
        permission_mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        """Iterate :class:`StreamEvent`-shaped dicts as the run progresses."""
        chosen_mode = mode if mode is not None else permission_mode
        for event in self._runtime.stream(
            RunTaskInput(
                agent=agent,
                input=input,
                options=options or {},
                permission_mode=chosen_mode,
                thread_id=thread_id,
            )
        ):
            yield event
            if event["event_type"] == "terminal":
                resp = event.get("terminal_response")
                if resp and resp.get("thread_id"):
                    self._touch_thread(resp["thread_id"], agent)

    async def astream(
        self,
        *,
        agent: str,
        input: dict[str, Any],
        options: dict[str, Any] | None = None,
        mode: PermissionMode | None = None,
        permission_mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async variant of :meth:`stream`. Yields StreamEvent dicts."""
        chosen_mode = mode if mode is not None else permission_mode
        async for event in self._runtime.astream(
            RunTaskInput(
                agent=agent,
                input=input,
                options=options or {},
                permission_mode=chosen_mode,
                thread_id=thread_id,
            )
        ):
            yield event
            if event["event_type"] == "terminal":
                resp = event.get("terminal_response")
                if resp and resp.get("thread_id"):
                    self._touch_thread(resp["thread_id"], agent)

    # ------------------------------------------------------------------
    # introspection (keyed by thread_id)
    # ------------------------------------------------------------------

    def get_state(self, thread_id: str) -> AgentState | None:
        return self._runtime.get_state(thread_id)

    def get_artifacts(self, thread_id: str) -> list[WorkspaceRef]:
        state = self._runtime.get_state(thread_id)
        if state is None:
            return []
        run_id = state.get("root_run_id") or state.get("run_id")
        if not run_id:
            return []
        return self._workspace.index_workspace(run_id)

    def get_trace(self, thread_id: str) -> Iterable[TraceEvent]:
        return self._runtime.read_trace(thread_id)

    def get_denials(self, thread_id: str) -> list[DeniedAction]:
        state = self._runtime.get_state(thread_id)
        if state is None:
            return []
        return list(state.get("denied_actions") or [])

    # ------------------------------------------------------------------
    # memory
    # ------------------------------------------------------------------

    def add_memory(self, record: dict[str, Any]) -> MemoryRecord:
        return self._memory.write_record(record)

    def list_memory(
        self,
        *,
        scopes: Iterable[MemoryScope] | None = None,
        types: Iterable[MemoryType] | None = None,
        tags: Iterable[str] | None = None,
    ) -> list[MemoryRecord]:
        return self._memory.search(scopes=scopes, types=types, tags=tags)

    def forget_memory(self, record_id: str) -> None:
        self._memory.delete_record(record_id)

    # ------------------------------------------------------------------
    # threads
    # ------------------------------------------------------------------

    def end_thread(self, thread_id: str) -> None:
        if thread_id in self._threads:
            self._threads[thread_id]["status"] = "closed"

    def list_threads(self) -> list[ThreadInfo]:
        return list(self._threads.values())

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------

    def list_hooks(self, thread_id: str | None = None) -> list[HookSpec]:
        del thread_id
        return self._hook_registry.all()

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
    # internals
    # ------------------------------------------------------------------

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

    def _register_builtin_tools(self, *, only: list[str] | None) -> None:
        """Register the kernel-level builtin tools into ``ToolRegistry``."""
        from ..tools.builtin import get_builtin_specs

        allow = set(only) if only is not None else None
        for spec, handler in get_builtin_specs():
            if allow is not None and spec["name"] not in allow:
                continue
            self._tools_registry.register_tool(spec, handler)

    def _register_subagent_tools(self) -> None:
        """Auto-register a ``delegate_to_<name>`` tool per discovered agent."""
        try:
            names = self._agent_loader.list_agent_names()
        except Exception:
            return
        for name in names:
            tool_name = f"delegate_to_{name}"
            if self._tools_registry.has(tool_name):
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
            self._tools_registry.register_tool(spec, lambda **_: None)
