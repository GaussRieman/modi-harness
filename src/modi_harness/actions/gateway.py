"""Action Gateway for Workflow Runtime Operations."""

from __future__ import annotations

from typing import Any

from ..hooks import HookDispatcher
from ..policy import PolicyGate
from ..tools.gateway import ToolDispatchResult, ToolGateway
from ..tools.registry import ToolRegistry
from ..types import AgentProfile, AgentState, ToolCallProposal


class ActionGateway:
    """The single governed path from a Runtime Operation to a Tool handler.

    Workflow and AgentLoop own control decisions. The Action Gateway owns
    schema validation, visibility, policy, hooks, dry-run and execution.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyGate,
        hooks: HookDispatcher,
        result_inline_limit_bytes: int,
        interactive: bool | None = None,
    ) -> None:
        self._tools = ToolGateway(
            registry=registry,
            policy=policy,
            hooks=hooks,
            result_inline_limit_bytes=result_inline_limit_bytes,
            interactive=interactive,
        )

    @property
    def registry(self) -> ToolRegistry:
        return self._tools._registry

    def execute_tool_call(
        self,
        proposal: ToolCallProposal,
        *,
        agent: AgentProfile,
        state: AgentState,
        runtime_deps: Any | None = None,
    ) -> ToolDispatchResult:
        return self._tools.execute_tool_call(
            proposal,
            agent=agent,
            state=state,
            graph_deps=runtime_deps,
        )


__all__ = ["ActionGateway"]
