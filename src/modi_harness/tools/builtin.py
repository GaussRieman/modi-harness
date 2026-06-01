"""Builtin tools: workspace and memory primitives implicitly available to every agent.

These six tools cover operations on resources the modi-harness kernel itself
manages (WorkspaceManager and MemoryStore). They are registered into the harness
at construction time when ``enable_builtin_tools=True`` (the default), and are
visible to every agent without being listed in ``agent.md``'s ``tools:`` field.

All six still flow through the standard governance pipeline — schema
validation, denied-retry, hooks, PolicyGate, idempotency cache, trust
annotation, trace recording. Builtins are a bypass for boilerplate, not for
governance.

See ``docs/superpowers/specs/2026-06-01-v0.4d-builtin-tools-design.md``.
"""

from __future__ import annotations

from typing import Any, Callable

# Handler signature: handler(*, arguments, state, deps) -> dict[str, Any]
BuiltinHandler = Callable[..., dict[str, Any]]

BUILTIN_TOOL_NAMES: frozenset[str] = frozenset({
    "read_workspace_file",
    "list_workspace_dir",
    "save_artifact",
    "save_draft",
    "recall_memory",
    "save_memory",
})


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

_KINDS = ["input", "state", "reference", "artifact", "draft", "log"]


def _spec_read_workspace_file() -> dict[str, Any]:
    return {
        "name": "read_workspace_file",
        "description": "Read a file from the current run's workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": _KINDS},
                "name": {"type": "string", "minLength": 1, "maxLength": 256},
                "encoding": {"type": "string", "enum": ["text", "bytes"]},
            },
            "required": ["kind", "name"],
            "additionalProperties": False,
        },
        "risk_level": "L0",
        "side_effect": False,
        "kind": "builtin",
    }


def _spec_list_workspace_dir() -> dict[str, Any]:
    return {
        "name": "list_workspace_dir",
        "description": "List files under one workspace kind for the current run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": _KINDS},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        "risk_level": "L0",
        "side_effect": False,
        "kind": "builtin",
    }


def _spec_save_artifact() -> dict[str, Any]:
    return {
        "name": "save_artifact",
        "description": "Write a file under the current run's artifacts/ and return its artifact_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 256},
                "content": {"type": "string"},
                "mime_type": {"type": "string"},
            },
            "required": ["name", "content"],
            "additionalProperties": False,
        },
        "risk_level": "L1",
        "side_effect": True,
        "kind": "builtin",
    }


def _spec_save_draft() -> dict[str, Any]:
    return {
        "name": "save_draft",
        "description": "Write a draft file under the current run's drafts/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 256},
                "content": {"type": "string"},
            },
            "required": ["name", "content"],
            "additionalProperties": False,
        },
        "risk_level": "L1",
        "side_effect": True,
        "kind": "builtin",
    }


def _spec_recall_memory() -> dict[str, Any]:
    return {
        "name": "recall_memory",
        "description": "Query the memory store. Returns matching records (read-only).",
        "input_schema": {
            "type": "object",
            "properties": {
                "scopes": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["user", "agent", "project", "conversation"]},
                },
                "types": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        "risk_level": "L0",
        "side_effect": False,
        "kind": "builtin",
    }


def _spec_save_memory() -> dict[str, Any]:
    return {
        "name": "save_memory",
        "description": "Write a memory record. Scope must be 'conversation' or 'agent'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 64},
                "scope": {"type": "string", "enum": ["conversation", "agent"]},
                "type": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "body": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "scope", "type", "body"],
            "additionalProperties": False,
        },
        "risk_level": "L1",
        "side_effect": True,
        "kind": "builtin",
    }


# ---------------------------------------------------------------------------
# Handlers (stubs — filled in by Tasks 3-9)
# ---------------------------------------------------------------------------


def _read_workspace_file(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    kind = arguments["kind"]
    name = arguments["name"]
    encoding = arguments.get("encoding", "text")
    run_id = state["run_id"]
    workspace = deps.workspace

    path = workspace._safe_join(run_id, kind, name)
    if not path.exists() or not path.is_file():
        return {"error": f"not found: {kind}/{name}"}

    if encoding == "bytes":
        data = path.read_bytes()
        return {
            "kind": kind,
            "name": name,
            "size_bytes": len(data),
            "content_b64": __import__("base64").b64encode(data).decode("ascii"),
        }
    text = path.read_text(encoding="utf-8")
    return {
        "kind": kind,
        "name": name,
        "size_bytes": len(text.encode("utf-8")),
        "content": text,
    }


def _list_workspace_dir(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    kind = arguments["kind"]
    run_id = state["run_id"]
    workspace = deps.workspace

    # Resolve the directory via _safe_join with no extra parts (validates kind).
    sub_dir = workspace._safe_join(run_id, kind)
    files: list[dict[str, Any]] = []
    if sub_dir.is_dir():
        for entry in sorted(sub_dir.rglob("*")):
            if entry.is_file():
                rel = entry.relative_to(sub_dir)
                files.append({
                    "name": str(rel),
                    "size_bytes": entry.stat().st_size,
                })
    return {"kind": kind, "files": files, "count": len(files)}


def _save_artifact(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    name = arguments["name"]
    content = arguments["content"]
    mime_type = arguments.get("mime_type")
    run_id = state["run_id"]
    workspace = deps.workspace

    ref = workspace.save_artifact(
        run_id,
        name,
        content.encode("utf-8"),
        trust="trusted",
        mime_type=mime_type,
    )
    return {
        "artifact_id": ref["artifact_id"],
        "name": name,
        "path": ref["path"],
        "size_bytes": ref["size_bytes"],
    }


def _save_draft(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    name = arguments["name"]
    content = arguments["content"]
    run_id = state["run_id"]
    workspace = deps.workspace

    ref = workspace.save_draft(run_id, name, content)
    return {
        "name": name,
        "path": ref["path"],
        "size_bytes": ref["size_bytes"],
    }


def _recall_memory(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    raise NotImplementedError


def _save_memory(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_builtin_specs() -> list[tuple[dict[str, Any], BuiltinHandler]]:
    """Return all six (spec, handler) pairs for registration."""
    return [
        (_spec_read_workspace_file(), _read_workspace_file),
        (_spec_list_workspace_dir(), _list_workspace_dir),
        (_spec_save_artifact(), _save_artifact),
        (_spec_save_draft(), _save_draft),
        (_spec_recall_memory(), _recall_memory),
        (_spec_save_memory(), _save_memory),
    ]


__all__ = ["BUILTIN_TOOL_NAMES", "get_builtin_specs", "BuiltinHandler"]
