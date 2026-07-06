"""Builtin tools: workspace and memory primitives implicitly available to every agent.

These seven tools cover operations on resources the modi-harness kernel itself
manages (WorkspaceManager and MemoryStore). By default they are registered into
the Harness at construction and are visible without being listed in
``agent.md``; ``builtin_tools`` can restrict the set.

All seven still flow through the standard governance pipeline — schema
validation, denied-retry, hooks, PolicyGate, idempotency cache, trust
annotation, trace recording. Builtins are a bypass for boilerplate, not for
governance.

See ``docs/superpowers/specs/2026-06-01-v0.4d-builtin-tools-design.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Handler signature: handler(*, arguments, state, deps) -> dict[str, Any]
BuiltinHandler = Callable[..., dict[str, Any]]

BUILTIN_TOOL_NAMES: frozenset[str] = frozenset({
    "read_workspace_file",
    "list_workspace_dir",
    "save_artifact",
    "save_draft",
    "recall_memory",
    "propose_memory",
    "save_memory",
    "transition_stage",
})


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

_KINDS = ["input", "state", "reference", "artifact", "draft", "log"]


def _spec_read_workspace_file() -> dict[str, Any]:
    return {
        "name": "read_workspace_file",
        "description": (
            "Read a file from the current run's workspace — e.g. caller-provided "
            "input files, references, or prior drafts."
        ),
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
        "description": (
            "Write a finished output file under the current run's artifacts/ and "
            "return its artifact_id. Artifacts are workspace output files, not "
            "memory."
        ),
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
        "description": (
            "Write an intermediate working file under the current run's drafts/. "
            "Drafts are workspace output files, not memory. Pass a JSON object as "
            "content for structured drafts (auto-serialized to JSON), or a string "
            "for plain-text drafts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 256},
                "content": {"type": ["string", "object"]},
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
        "description": (
            "Search long-term memory for relevant prior preferences, methods, and "
            "reference pointers before you start work. Read-only; returns matching "
            "records. Scopes: user (cross-session preferences), workspace "
            "(project-scoped), agent (this agent's learned patterns), thread "
            "(current conversation)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scopes": {
                    "type": "array",
                    "items": {"type": "string", "enum": [
                        "user", "workspace", "agent", "thread",
                    ]},
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
        "description": (
            "Write a small reusable memory record directly. Scope must be 'thread' "
            "or 'agent'. Memory is not a place for raw content, full reports, "
            "drafts, or logs. For governed writes to durable scopes, use "
            "propose_memory instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 64},
                "scope": {"type": "string", "enum": ["thread", "agent"]},
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


def _spec_propose_memory() -> dict[str, Any]:
    return {
        "name": "propose_memory",
        "description": (
            "Propose saving a small reusable memory record — a preference, method, "
            "or reference pointer worth recalling in future runs. Durable scopes "
            "(user, workspace) may require human judgment; thread/agent are lighter. "
            "Set source_kind to note where the record came from (e.g. 'user' or "
            "'model'). Do not store raw source text, full reports, drafts, or run "
            "logs in memory; use save_draft/save_artifact for outputs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 64},
                "scope": {"type": "string", "enum": [
                    "thread", "agent", "workspace", "user",
                ]},
                "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"]},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "body": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_kind": {"type": "string"},
            },
            "required": ["id", "scope", "type", "body"],
            "additionalProperties": False,
        },
        "risk_level": "L1",
        "side_effect": True,
        "kind": "builtin",
    }


def _spec_transition_stage() -> dict[str, Any]:
    return {
        "name": "transition_stage",
        "description": (
            "Propose moving the run to a different stage of work — one of "
            "clarify, explore, plan, execute, verify, deliver. A stage is the "
            "phase you are in, not a micro-task; transitions are alignment-"
            "relevant, so the runtime may allow, redirect, or pause for human "
            "judgment (e.g. entering 'deliver' before the success bar exists). "
            "Call this when the work genuinely moves to a new phase; keep using "
            "the task protocol for work inside a stage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "enum": ["clarify", "explore", "plan", "execute", "verify", "deliver"],
                },
                "rationale": {"type": "string"},
            },
            "required": ["to"],
            "additionalProperties": False,
        },
        "risk_level": "L0",
        "side_effect": False,
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
    from ..memory import MemoryScopeKeys

    scopes = arguments.get("scopes")
    types = arguments.get("types")
    tags = arguments.get("tags")
    query = arguments.get("query")
    limit = arguments.get("limit") or 20
    limit = min(int(limit), 50)  # defensive clamp
    base_scope_keys = getattr(deps, "memory_scope_keys", None) or MemoryScopeKeys()
    scope_keys = base_scope_keys.for_run(
        agent_name=state.get("agent_name"),
        thread_id=state.get("thread_id"),
    )

    records = deps.memory.search(
        query=query,
        scopes=scopes,
        types=types,
        tags=tags,
        limit=limit,
        scope_keys=scope_keys,
    )
    return {
        "records": [dict(r) for r in records],
        "count": len(records),
    }


def _save_memory(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    scope = arguments.get("scope")
    if scope not in ("thread", "agent"):
        return {"error": f"scope {scope!r} not writable from agent context (allowed: thread, agent)"}
    return _commit_memory(arguments=arguments, state=state, deps=deps)


def _propose_memory(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    from .._utils import compute_fingerprint

    decision = deps.policy.decide(
        {
            "agent": {
                "name": state.get("agent_name", ""),
                "default_tools": [],
                "permission_profile": None,
            },
            "skill": None,
            "tool_spec": None,
            "state": state,
            "requested_action": {
                "kind": "memory_write",
                "tool_name": "propose_memory",
                "arguments": arguments,
                "target": {
                    "scope": arguments.get("scope"),
                    "source_kind": arguments.get("source_kind"),
                },
                "fingerprint": compute_fingerprint({"memory": arguments}),
            },
            "permission_mode": state.get("permission_mode", "auto"),
        }
    )
    if decision["decision"] == "deny":
        return {
            "status": "denied",
            "reason": decision["reason"],
        }
    if decision["decision"] in ("require_approval", "require_review"):
        return {
            "status": "approval_required",
            "approval_id": decision.get("approval_id"),
            "reason": decision["reason"],
            "scope": arguments.get("scope"),
        }
    committed = _commit_memory(arguments=arguments, state=state, deps=deps)
    if "error" in committed:
        return committed
    return {"status": "committed", **committed}


def _commit_memory(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    from ..memory import MemoryScopeKeys

    # Constrain the model: reject overwrites of any existing id in any scope.
    # Direct API callers (harness.add_memory) keep their trust-the-user
    # overwrite semantics — this guard is at the builtin layer only.
    from ..memory.errors import MemoryNotFoundError

    record_id = arguments["id"]
    try:
        base_scope_keys = getattr(deps, "memory_scope_keys", None) or MemoryScopeKeys()
        scope_keys = base_scope_keys.for_run(
            agent_name=state.get("agent_name"),
            thread_id=state.get("thread_id"),
        )
        deps.memory.read_record(record_id, scope_keys=scope_keys)
    except MemoryNotFoundError:
        pass
    else:
        return {"error": f"memory record {record_id!r} already exists; pick a different id"}

    record = {
        "id": record_id,
        "scope": arguments["scope"],
        "type": arguments["type"],
        "name": arguments.get("name", ""),
        "description": arguments.get("description", ""),
        "body": arguments["body"],
        "tags": arguments.get("tags", []),
        "source_run_id": state.get("run_id"),
        "metadata": {"source_kind": arguments.get("source_kind", "model")},
    }
    full = deps.memory.write_record(record, scope_keys=scope_keys)
    return {
        "id": full["id"],
        "scope": full["scope"],
        "type": full["type"],
        "created_at": full["created_at"],
    }


def _transition_stage(*, arguments: dict[str, Any], state: Any, deps: Any) -> dict[str, Any]:
    """Resolve a proposed stage transition into a target ``IntentStage``.

    This handler runs only after the ``AlignmentKernel`` has *allowed* the
    transition (the gateway routes ``transition_stage`` to the
    ``stage_transition`` action kind, so alignment — not this handler — decides
    whether it is permitted, redirected, or paused for judgment). Its job is the
    mechanical one: build the target stage descriptor the graph node will set as
    the run's new ``current_stage``. It never advances state itself.
    """
    from ..intent.stages import STAGE_ORDER, build_stage

    target = arguments.get("to")
    if target not in STAGE_ORDER:
        return {"error": f"unknown stage {target!r}; expected one of {', '.join(STAGE_ORDER)}"}

    intent = state.get("human_intent") if hasattr(state, "get") else None
    current = (intent or {}).get("current_stage") or {}
    from_kind = current.get("kind")

    rationale = arguments.get("rationale")
    stage = build_stage(target, goal=rationale) if rationale else build_stage(target)
    return {
        "status": "transitioned",
        "from_stage": from_kind,
        "to_stage": target,
        "stage": dict(stage),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_builtin_specs() -> list[tuple[dict[str, Any], BuiltinHandler]]:
    """Return all builtin (spec, handler) pairs for registration."""
    return [
        (_spec_read_workspace_file(), _read_workspace_file),
        (_spec_list_workspace_dir(), _list_workspace_dir),
        (_spec_save_artifact(), _save_artifact),
        (_spec_save_draft(), _save_draft),
        (_spec_recall_memory(), _recall_memory),
        (_spec_propose_memory(), _propose_memory),
        (_spec_save_memory(), _save_memory),
        (_spec_transition_stage(), _transition_stage),
    ]


__all__ = ["BUILTIN_TOOL_NAMES", "BuiltinHandler", "get_builtin_specs"]
