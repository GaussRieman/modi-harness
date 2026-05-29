"""ModiHarness facade.

Thin wrapper that wires the existing modules together. Holds:

- AgentLoader / SkillLoader (sources)
- WorkspaceManager (storage root)
- MemoryStore
- HookRegistry / HookDispatcher
- PolicyGate
- ToolGateway (with ToolRegistry for tool registration)
- ContextManager
- ModelAdapter
- OutputController
- RuntimeAdapter (orchestrator)

V0.1 keeps Harness API in-process. HTTP and CLI adapters wrap this object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from langchain_core.language_models import BaseChatModel

from .._utils import new_ulid, now_iso
from ..agents import AgentLoader
from ..context import ContextManager
from ..hooks import HookDispatcher, HookRegistry
from ..memory import MemoryPaths, MemoryStore
from ..models import ModelAdapter
from ..output import OutputController
from ..policy import PolicyGate
from ..runtime import RunTaskInput, RuntimeAdapter
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
    """The single public entry point for V0.1."""

    def __init__(
        self,
        *,
        agents_dir: Path | str | None = None,
        skills_dir: Path | str | None = None,
        workspace_root: Path | str = ".modi/workspace",
        memory_root: Path | str = "~/.modi/memory",
        rule_packs: list[str] | None = None,
        chat_model: BaseChatModel | None = None,
        max_steps: int = 20,
        repair_budget: int = 3,
        hook_user_settings: Path | str | None = None,
        hook_project_settings: Path | str | None = None,
        hook_pass_env: list[str] | None = None,
    ) -> None:
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
        self._policy = PolicyGate(rule_packs=rule_packs)
        self._tools_registry = ToolRegistry()
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
        self._output = OutputController()
        self._runtime = RuntimeAdapter(
            agent_loader=AgentLoader(project_dir=agents_dir),
            skill_loader=SkillLoader(project_dir=skills_dir) if skills_dir else None,
            memory_store=self._memory,
            workspace=self._workspace,
            context_manager=self._context,
            model_adapter=self._model,
            tool_gateway=self._tool_gateway,
            policy=self._policy,
            output_controller=self._output,
            hooks=self._hooks,
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
        permission_mode: PermissionMode | None = None,
        thread_id: str | None = None,
    ) -> RunTaskResponse:
        if thread_id is not None and thread_id in self._threads:
            self._threads[thread_id]["last_active_at"] = now_iso()
            self._threads[thread_id]["run_count"] += 1
        return self._runtime.run(
            RunTaskInput(
                agent=agent,
                input=input,
                options=options or {},
                permission_mode=permission_mode,
                thread_id=thread_id,
            )
        )

    def resume_task(self, *, run_id: str, input: dict[str, Any]) -> RunTaskResponse:
        # V0.1: resume is implicitly handled via approve_action / reject_action.
        raise NotImplementedError("resume_task: V0.1 uses approve/reject only")

    def approve_action(
        self,
        *,
        run_id: str,
        approval_id: str,
        decision: str = "approved",
    ) -> RunTaskResponse:
        return self._runtime.approve(run_id=run_id, approval_id=approval_id, decision=decision)

    def reject_action(
        self,
        *,
        run_id: str,
        approval_id: str,
        reason: str,
    ) -> RunTaskResponse:
        return self._runtime.reject(run_id=run_id, approval_id=approval_id, reason=reason)

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    def get_state(self, run_id: str) -> AgentState | None:
        ctx = self._runtime._runs.get(run_id)
        return ctx.state if ctx is not None else None

    def get_artifacts(self, run_id: str) -> list[WorkspaceRef]:
        return self._workspace.index_workspace(run_id)

    def get_trace(self, run_id: str) -> Iterable[TraceEvent]:
        return self._runtime.read_trace(run_id)

    def get_denials(self, run_id: str) -> list[DeniedAction]:
        ctx = self._runtime._runs.get(run_id)
        return list(ctx.state["denied_actions"]) if ctx is not None else []

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

    def start_thread(self, *, agent: str, options: dict[str, Any] | None = None) -> ThreadInfo:
        thread_id = (options or {}).get("thread_id") or new_ulid()
        info = ThreadInfo(  # type: ignore[typeddict-item]
            thread_id=thread_id,
            agent_name=agent,
            created_at=now_iso(),
            last_active_at=now_iso(),
            run_count=0,
            status="open",
        )
        self._threads[thread_id] = info
        return info

    def end_thread(self, thread_id: str) -> None:
        if thread_id in self._threads:
            self._threads[thread_id]["status"] = "closed"

    def list_threads(self) -> list[ThreadInfo]:
        return list(self._threads.values())

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------

    def list_hooks(self, run_id: str | None = None) -> list[HookSpec]:
        del run_id  # V0.1: hooks are global; placeholder for future per-run filters.
        return self._hook_registry.all()

    def get_hook_result(self, run_id: str, hook_dispatch_id: str) -> list[HookResult]:
        # V0.1: hook results live in the trace event payload. Caller filters trace.
        del hook_dispatch_id
        events = list(self.get_trace(run_id))
        out: list[HookResult] = []
        for ev in events:
            if ev["event_type"] == "hook_dispatch":
                results = ev["payload"].get("results", [])
                if isinstance(results, list):
                    out.extend(results)
        return out
