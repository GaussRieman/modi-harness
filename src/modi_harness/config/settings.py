"""Typed Settings backed by Pydantic + manual env parsing.

We deliberately avoid pydantic-settings' source customization complexity. The
flat ``MODI_*`` env keys are mapped to nested groups by an explicit table,
which is more readable and easier to test than override hooks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

PermissionMode = Literal["ask", "auto", "plan", "bypass", "preview", "trust"]


def _expand(path: str | Path) -> Path:
    return Path(str(path)).expanduser().resolve()


def _split_csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [item.strip() for item in value.split(",") if item.strip()]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ModelSettings(_Frozen):
    provider: Literal["openai", "anthropic"] = "openai"
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    fallback: str = ""
    fallback_provider: str = ""
    fallback_name: str = ""
    fallback_api_key: str = ""
    fallback_base_url: str = ""
    retry_attempts: int = 2
    retry_backoff: float = 1.5


class RuntimeSettings(_Frozen):
    permission_mode: PermissionMode = "ask"
    max_steps: int = 20
    repair_budget: int = 3


class StorageSettings(_Frozen):
    workspace_root: Path = Path(".modi/workspace")
    workspace_snapshot_limit: int = 100
    trace_root: Path | None = None
    trace_redact_keys: list[str] = Field(
        default_factory=lambda: ["api_key", "authorization", "password", "secret"]
    )
    trace_payload_inline_limit_bytes: int = 2048

    @field_validator("workspace_root", mode="before")
    @classmethod
    def _ws(cls, v: str | Path | None) -> Path:
        if v in (None, ""):
            return Path(".modi/workspace")
        return Path(str(v))

    @field_validator("trace_root", mode="before")
    @classmethod
    def _tr(cls, v: str | Path | None) -> Path | None:
        if v in (None, ""):
            return None
        return _expand(v)

    @field_validator("trace_redact_keys", mode="before")
    @classmethod
    def _redact(cls, v: str | list[str] | None) -> list[str]:
        if v in (None, ""):
            return ["api_key", "authorization", "password", "secret"]
        return _split_csv(v)


class LoaderSettings(_Frozen):
    agent_project_dir: Path = Path("agents")
    agent_user_dir: Path = Path("~/.modi/agents").expanduser()
    skill_project_dir: Path = Path("skills")
    skill_user_dir: Path = Path("~/.modi/skills").expanduser()

    @field_validator("agent_user_dir", "skill_user_dir", mode="before")
    @classmethod
    def _user_dir(cls, v: str | Path) -> Path:
        return _expand(v)


class ToolSettings(_Frozen):
    timeout_default: int = 30
    result_inline_limit_bytes: int = 8192


class PolicySettings(_Frozen):
    rule_packs: list[str] = Field(default_factory=lambda: ["core"])

    @field_validator("rule_packs", mode="before")
    @classmethod
    def _packs(cls, v: str | list[str] | None) -> list[str]:
        if v in (None, ""):
            return ["core"]
        items = _split_csv(v)
        if "core" not in items:
            items = ["core", *items]
        return items


class MemorySettings(_Frozen):
    root: Path = Path("~/.modi/memory").expanduser()
    user_key: str = "default"
    workspace_key: str = ""
    token_budget: int = 2000
    workspace_horizon_days: int = 90
    retrieval_backend: str = "local"
    vector_backend: str = "none"
    consolidation: str = "off"

    @field_validator("root", mode="before")
    @classmethod
    def _root(cls, v: str | Path) -> Path:
        return _expand(v)


class HookSettings(_Frozen):
    user_settings: Path = Path("~/.modi/settings.json").expanduser()
    project_settings: Path = Path(".modi/settings.json")
    timeout_default: int = 10
    pass_env: list[str] = Field(default_factory=lambda: ["PATH", "LANG", "LC_ALL"])

    @field_validator("user_settings", mode="before")
    @classmethod
    def _us(cls, v: str | Path) -> Path:
        return _expand(v)

    @field_validator("pass_env", mode="before")
    @classmethod
    def _pass(cls, v: str | list[str] | None) -> list[str]:
        if v in (None, ""):
            return ["PATH", "LANG", "LC_ALL"]
        return _split_csv(v)


CheckpointBackend = Literal["sqlite", "postgres", "memory"]


class CheckpointSettings(_Frozen):
    backend: CheckpointBackend = "sqlite"
    sqlite_path: Path = Path(".modi/checkpoint.sqlite")
    postgres_dsn: str = ""

    @field_validator("sqlite_path", mode="before")
    @classmethod
    def _sqlite(cls, v: str | Path | None) -> Path:
        if v in (None, ""):
            return Path(".modi/checkpoint.sqlite")
        return Path(str(v))


class SubagentSettings(_Frozen):
    max_depth: int = 3


class PermissionsSettings(_Frozen):
    """User/project ``settings.json`` permissions layer.

    Each list accepts either tool names (exact match) or risk-level tokens
    (``L0``..``L4``). Empty lists mean "no override at this layer". Loaded
    from ``settings.json`` (not env), merged user→project at the gate.
    """

    always_allow: list[str] = Field(default_factory=list)
    always_deny: list[str] = Field(default_factory=list)
    always_ask: list[str] = Field(default_factory=list)


# Flat MODI_<KEY> -> (group, field).
_FLAT_FIELD_MAP: dict[str, tuple[str, str]] = {
    "MODEL_PROVIDER": ("model", "provider"),
    "MODEL_NAME": ("model", "name"),
    "MODEL_API_KEY": ("model", "api_key"),
    "MODEL_BASE_URL": ("model", "base_url"),
    "MODEL_FALLBACK": ("model", "fallback"),
    "MODEL_FALLBACK_PROVIDER": ("model", "fallback_provider"),
    "MODEL_FALLBACK_NAME": ("model", "fallback_name"),
    "MODEL_FALLBACK_API_KEY": ("model", "fallback_api_key"),
    "MODEL_FALLBACK_BASE_URL": ("model", "fallback_base_url"),
    "MODEL_RETRY_ATTEMPTS": ("model", "retry_attempts"),
    "MODEL_RETRY_BACKOFF": ("model", "retry_backoff"),
    "PERMISSION_MODE": ("runtime", "permission_mode"),
    "MODE": ("runtime", "permission_mode"),
    "MAX_STEPS": ("runtime", "max_steps"),
    "REPAIR_BUDGET": ("runtime", "repair_budget"),
    "WORKSPACE_ROOT": ("storage", "workspace_root"),
    "WORKSPACE_SNAPSHOT_LIMIT": ("storage", "workspace_snapshot_limit"),
    "TRACE_ROOT": ("storage", "trace_root"),
    "TRACE_REDACT_KEYS": ("storage", "trace_redact_keys"),
    "TRACE_PAYLOAD_INLINE_LIMIT_BYTES": ("storage", "trace_payload_inline_limit_bytes"),
    "AGENT_PROJECT_DIR": ("loaders", "agent_project_dir"),
    "AGENT_USER_DIR": ("loaders", "agent_user_dir"),
    "SKILL_PROJECT_DIR": ("loaders", "skill_project_dir"),
    "SKILL_USER_DIR": ("loaders", "skill_user_dir"),
    "TOOL_TIMEOUT_DEFAULT": ("tools", "timeout_default"),
    "TOOL_RESULT_INLINE_LIMIT_BYTES": ("tools", "result_inline_limit_bytes"),
    "POLICY_RULE_PACKS": ("policy", "rule_packs"),
    "MEMORY_ROOT": ("memory", "root"),
    "MEMORY_USER_KEY": ("memory", "user_key"),
    "MEMORY_WORKSPACE_KEY": ("memory", "workspace_key"),
    "MEMORY_TOKEN_BUDGET": ("memory", "token_budget"),
    "MEMORY_WORKSPACE_HORIZON_DAYS": ("memory", "workspace_horizon_days"),
    "MEMORY_RETRIEVAL_BACKEND": ("memory", "retrieval_backend"),
    "MEMORY_VECTOR_BACKEND": ("memory", "vector_backend"),
    "MEMORY_CONSOLIDATION": ("memory", "consolidation"),
    "HOOK_USER_SETTINGS": ("hooks", "user_settings"),
    "HOOK_PROJECT_SETTINGS": ("hooks", "project_settings"),
    "HOOK_TIMEOUT_DEFAULT": ("hooks", "timeout_default"),
    "HOOK_PASS_ENV": ("hooks", "pass_env"),
    "CHECKPOINT_BACKEND": ("checkpoint", "backend"),
    "CHECKPOINT_SQLITE_PATH": ("checkpoint", "sqlite_path"),
    "CHECKPOINT_POSTGRES_DSN": ("checkpoint", "postgres_dsn"),
    "SUBAGENT_MAX_DEPTH": ("subagent", "max_depth"),
}


_GROUP_TYPES: dict[str, type[_Frozen]] = {
    "model": ModelSettings,
    "runtime": RuntimeSettings,
    "storage": StorageSettings,
    "loaders": LoaderSettings,
    "tools": ToolSettings,
    "policy": PolicySettings,
    "memory": MemorySettings,
    "hooks": HookSettings,
    "checkpoint": CheckpointSettings,
    "subagent": SubagentSettings,
}


class Settings(BaseModel):
    """Top-level Settings facade. Frozen after construction."""

    model_config = ConfigDict(frozen=True)

    model: ModelSettings = Field(default_factory=ModelSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    loaders: LoaderSettings = Field(default_factory=LoaderSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    hooks: HookSettings = Field(default_factory=HookSettings)
    checkpoint: CheckpointSettings = Field(default_factory=CheckpointSettings)
    subagent: SubagentSettings = Field(default_factory=SubagentSettings)
    permissions: PermissionsSettings = Field(default_factory=PermissionsSettings)

    def __init__(self, _env_file: str | os.PathLike[str] | None = ".env", **overrides: Any) -> None:
        merged = _collect_env(_env_file)
        # MODI_MODE wins over MODI_PERMISSION_MODE when both are set —
        # MODE is the new product-surface name; PERMISSION_MODE is the
        # legacy alias kept for one minor release.
        if "MODE" in merged:
            merged["PERMISSION_MODE"] = merged["MODE"]
        grouped: dict[str, dict[str, Any]] = {g: {} for g in _GROUP_TYPES}
        for key, value in merged.items():
            mapped = _FLAT_FIELD_MAP.get(key)
            if mapped is None:
                continue
            group, field = mapped
            grouped[group][field] = value

        kwargs: dict[str, Any] = {}
        for group, group_cls in _GROUP_TYPES.items():
            kwargs[group] = group_cls(**grouped[group])
        kwargs.update(overrides)
        super().__init__(**kwargs)


def _collect_env(env_file: str | os.PathLike[str] | None) -> dict[str, str]:
    """Merge .env (lower precedence) with os.environ. Strip MODI_ prefix.

    Empty strings are skipped so they don't override defaults.
    """
    merged: dict[str, str] = {}
    if env_file is not None:
        path = Path(env_file)
        if path.exists():
            for k, v in dotenv_values(path).items():
                if k and v not in (None, "") and k.startswith("MODI_"):
                    merged[k[len("MODI_"):]] = v  # type: ignore[index]
    for k, v in os.environ.items():
        if k.startswith("MODI_") and v != "":
            merged[k[len("MODI_"):]] = v
    return merged
