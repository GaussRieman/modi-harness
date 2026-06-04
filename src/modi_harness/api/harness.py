"""ModiHarness — V0.5 slim capability suite.

The harness layer captures *what* governs a model: policy, hooks, output
contracts, context, model adapter. It does **not** know about specific
agents and does **not** bind infra (checkpointer, workspace, memory).
Those belong to ModiSession.

See docs/superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md §3.1.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from ..config.settings import PermissionsSettings
from ..context import ContextManager
from ..hooks import HookRegistry
from ..models import ModelAdapter, ModelAdapterCache
from ..output import OutputController
from ..policy import PolicyGate
from ..tools.builtin import BUILTIN_TOOL_NAMES, get_builtin_specs
from ..tools.registry import ToolRegistry
from ..types import HookSpec, PermissionsConfig, ToolBinding


class ModiHarness:
    """V0.5 capability suite. Immutable after construction; shareable.

    The constructor performs registry assembly only — no infra side effects.
    Use ModiSession to bind a harness to checkpointer/workspace/memory roots
    and execute threads.
    """

    def __init__(
        self,
        chat_model: BaseChatModel,
        *,
        rule_packs: list[str] | None = None,
        permissions: PermissionsConfig | None = None,
        hook_specs: list[HookSpec] | None = None,
        builtin_tools: list[str] | None = None,
        kernel_tools: list[ToolBinding] | None = None,
    ) -> None:
        self.chat_model = chat_model
        self.permissions = permissions

        # Map our PermissionsConfig (V0.5 typed dataclass) → existing
        # PermissionsSettings (Pydantic model used by PolicyGate).
        # Note the field-name divergence:
        #   preauthorized   ↔ always_allow
        #   deny            ↔ always_deny
        #   review_required ↔ always_ask
        # The 'mode' field of PermissionsConfig is a different concept and
        # is NOT carried into PermissionsSettings (which has no 'mode').
        # Permission-mode is per-call via session.run_task(mode=...).
        perms_settings: PermissionsSettings | None = None
        if permissions is not None:
            perms_settings = PermissionsSettings(
                always_allow=list(permissions.preauthorized),
                always_deny=list(permissions.deny),
                always_ask=list(permissions.review_required),
            )

        self.policy = PolicyGate(
            rule_packs=rule_packs,
            permissions=perms_settings,
        )

        # HookRegistry's constructor takes the hook list directly.
        self.hook_registry = HookRegistry(hook_specs or [])

        self.context = ContextManager(policy=self.policy)
        self.output = OutputController()
        self.model = ModelAdapter(chat_model=chat_model)

        # Per-agent override cache; default adapter wraps chat_model.
        try:
            from ..config.settings import Settings

            settings: Settings | None = Settings()
        except (ValidationError, OSError):
            settings = None
        self.model_cache: ModelAdapterCache | None = (
            ModelAdapterCache(settings.model, default_adapter=self.model)
            if settings is not None
            else None
        )

        # Builtin tool registry — kernel-scoped, every agent sees this.
        self.builtin_tools_registry = ToolRegistry()
        self.builtin_tool_names = _resolve_builtin_whitelist(builtin_tools)
        for spec, handler in get_builtin_specs():
            if spec["name"] in self.builtin_tool_names:
                self.builtin_tools_registry.register_tool(spec, handler)

        # Plugin-contributed kernel tools extend the builtin registry. The
        # builtin_tools whitelist does NOT filter these — they're explicitly
        # provided, not defaults.
        for tb in kernel_tools or []:
            self.builtin_tools_registry.register_tool(
                dict(tb.spec), tb.handler, dry_run=tb.dry_run
            )


def _resolve_builtin_whitelist(raw: list[str] | None) -> set[str]:
    """Resolve builtin_tools= argument per spec §3.1.

    - None → all builtins enabled
    - [] → none enabled
    - [...] → explicit whitelist intersected with known builtins
    """
    if raw is None:
        return set(BUILTIN_TOOL_NAMES)
    return set(raw) & set(BUILTIN_TOOL_NAMES) if raw else set()


__all__ = ["ModiHarness"]
