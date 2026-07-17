"""Load the single supported Agent package format.

An Agent is a directory containing ``agent.toml`` and at least one explicit
``workflows/*.yaml`` definition. Historical Markdown declarations and Brain or
stage control files are rejected, not adapted.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any, cast

from ..models.factory import expand_env_vars
from ..tasks import TASK_PROTOCOL_TOOL_NAMES
from ..types import AgentProfile, OutputContract, PermissionProfile
from ..workflow import Workflow, WorkflowDefinitionError, parse_workflow_yaml
from .errors import AgentDuplicateError, AgentFrontmatterError, AgentNotFoundError

# Retained until the graph hard switch removes the old finalization path. The
# canonical Agent loader never auto-injects it.
SUBMIT_OUTPUT_TOOL_NAME = "submit_output"
REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"

_OBSOLETE_CONTROL_FILES = frozenset(
    {"agent.md", "brain.toml", "brain.md", "rules.toml", "stages.toml"}
)
_PACKAGE_METADATA_FILES: dict[str, str] = {
    "intent": "intent.toml",
    "loop": "loop.toml",
}
_AGENT_FIELDS = frozenset(
    {
        "name",
        "description",
        "instruction",
        "tools",
        "skills",
        "output_contract",
        "permission_profile",
        "safety_constraints",
        "tags",
        "model",
        "task_protocol",
        "interaction_protocol",
        "child_templates",
        "memory_level",
        "metadata",
        "factory",
    }
)


class AgentLoader:
    """Discover and load canonical Agent packages from configured roots."""

    def __init__(
        self,
        project_dir: Path | str | None = None,
        user_dir: Path | str | None = None,
        plugin_dirs: list[Path] | None = None,
    ) -> None:
        self._sources = [
            Path(directory)
            for directory in (project_dir, user_dir, *(plugin_dirs or []))
            if directory is not None
        ]
        self._cache: dict[Path, tuple[int, AgentProfile]] = {}

    def load_agent(self, name_or_path: str) -> AgentProfile:
        path = self._resolve(name_or_path)
        _validate_package_shape(path.parent)
        signature = _agent_definition_signature(path)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        profile = self._build_profile(path)
        self._cache[path] = (signature, profile)
        return profile

    def list_agent_names(self) -> list[str]:
        names: set[str] = set()
        for source in self._sources:
            if not source.exists():
                continue
            for entry in source.iterdir():
                if entry.is_dir() and (entry / "agent.toml").is_file():
                    names.add(entry.name)
        return sorted(names)

    def _resolve(self, name_or_path: str) -> Path:
        candidate = Path(name_or_path)
        if candidate.is_dir() and (candidate / "agent.toml").is_file():
            return (candidate / "agent.toml").resolve()
        if candidate.name == "agent.toml" and candidate.is_file():
            return candidate.resolve()

        matches = [
            (source / name_or_path / "agent.toml").resolve()
            for source in self._sources
            if (source / name_or_path / "agent.toml").is_file()
        ]
        if not matches:
            raise AgentNotFoundError(f"agent {name_or_path!r} not found in any source")
        if len(matches) > 1:
            joined = ", ".join(str(path) for path in matches)
            raise AgentDuplicateError(
                f"agent {name_or_path!r} defined in multiple sources: {joined}"
            )
        return matches[0]

    def _build_profile(self, path: Path) -> AgentProfile:
        raw = _load_toml(path)
        if set(raw) == {"factory"}:
            raise AgentFrontmatterError(
                f"{path}: factory manifest must be resolved by the discovery registry"
            )
        unknown = sorted(set(raw) - _AGENT_FIELDS)
        if unknown:
            raise AgentFrontmatterError(
                f"{path}: unknown agent.toml field(s): {', '.join(unknown)}"
            )
        if "factory" in raw:
            raise AgentFrontmatterError(
                f"{path}: factory cannot be merged with a declarative Agent package"
            )

        name = _require_string(raw, "name", path)
        description = _require_string(raw, "description", path)
        instruction = _require_string(raw, "instruction", path)
        default_tools = _string_list(raw.get("tools", []), "tools", path)
        default_skills = _string_list(raw.get("skills", []), "skills", path)
        safety_constraints = _string_list(
            raw.get("safety_constraints", []), "safety_constraints", path
        )
        tags = _string_list(raw.get("tags", []), "tags", path)
        output_contract = _normalize_output_contract(raw.get("output_contract"), path)
        permission_profile = _normalize_permission_profile(raw.get("permission_profile"), path)
        task_protocol = _normalize_task_protocol(raw.get("task_protocol"), path)
        interaction_protocol = _normalize_interaction_protocol(
            raw.get("interaction_protocol"), path
        )
        child_templates = _normalize_child_templates(raw.get("child_templates", []), path)

        if (
            interaction_protocol["startup"] == "agent"
            and REQUEST_USER_INPUT_TOOL_NAME not in default_tools
        ):
            default_tools.append(REQUEST_USER_INPUT_TOOL_NAME)
        if task_protocol["mode"] != "off":
            default_tools.extend(
                name for name in TASK_PROTOCOL_TOOL_NAMES if name not in default_tools
            )

        metadata = _metadata(raw, path)
        metadata["task_protocol"] = task_protocol
        metadata["interaction_protocol"] = interaction_protocol
        metadata["child_templates"] = child_templates
        if "model" in raw:
            metadata["model"] = _normalize_model(raw["model"], path)

        package_files: dict[str, Any] = {}
        for key, filename in _PACKAGE_METADATA_FILES.items():
            package_path = path.parent / filename
            if package_path.is_file():
                metadata[key] = _load_toml(package_path)
                package_files[key] = filename

        workflows, workflow_files = _load_package_workflows(
            path.parent,
            agent_tools=set(default_tools),
        )
        package_files["workflows"] = workflow_files
        metadata["package"] = {
            "root": str(path.parent),
            "files": package_files,
        }

        return AgentProfile(
            name=name,
            description=description,
            instruction=instruction,
            default_tools=default_tools,
            default_skills=default_skills,
            output_contract=output_contract,
            permission_profile=permission_profile,
            safety_constraints=safety_constraints,
            tags=tags,
            workflows=workflows,
            metadata=metadata,
        )


def _validate_package_shape(package_dir: Path) -> None:
    obsolete = sorted(
        filename for filename in _OBSOLETE_CONTROL_FILES if (package_dir / filename).exists()
    )
    if obsolete:
        raise AgentFrontmatterError(
            f"{package_dir}: obsolete control file(s) are forbidden: {', '.join(obsolete)}"
        )


def _load_package_workflows(
    package_dir: Path,
    *,
    agent_tools: set[str],
) -> tuple[list[Workflow], list[str]]:
    workflows_dir = package_dir / "workflows"
    if not workflows_dir.is_dir():
        raise AgentFrontmatterError(f"{package_dir}: Agent package requires at least one Workflow")
    paths = sorted(workflows_dir.glob("*.yaml"))
    if not paths:
        raise AgentFrontmatterError(f"{package_dir}: Agent package requires at least one Workflow")

    workflows: list[Workflow] = []
    ids: set[str] = set()
    for workflow_path in paths:
        try:
            workflow = parse_workflow_yaml(
                workflow_path.read_text(encoding="utf-8"),
                source=str(workflow_path),
                agent_tools=agent_tools,
            )
        except (OSError, WorkflowDefinitionError) as exc:
            raise AgentFrontmatterError(str(exc)) from exc
        if workflow.id in ids:
            raise AgentFrontmatterError(f"{workflows_dir}: duplicate Workflow id {workflow.id!r}")
        ids.add(workflow.id)
        workflows.append(workflow)
    return workflows, [f"workflows/{path.name}" for path in paths]


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentFrontmatterError(f"{path}: cannot read TOML: {exc}") from exc
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: TOML root must be a mapping")
    return raw


def _agent_definition_signature(path: Path) -> int:
    dependencies: set[Path] = {path, path.parent}
    for filename in _PACKAGE_METADATA_FILES.values():
        candidate = path.parent / filename
        if candidate.exists():
            dependencies.add(candidate)
    workflows_dir = path.parent / "workflows"
    if workflows_dir.exists():
        dependencies.add(workflows_dir)
        dependencies.update(workflows_dir.glob("*.yaml"))

    digest = hashlib.sha256()
    for dependency in sorted(dependencies):
        stat = dependency.stat()
        digest.update(str(dependency).encode())
        digest.update(str(stat.st_mtime_ns).encode())
    return int.from_bytes(digest.digest()[:8], "big")


def _require_string(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentFrontmatterError(f"{path}: {key!r} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, key: str, path: Path) -> list[str]:
    if not isinstance(value, list):
        raise AgentFrontmatterError(f"{path}: {key!r} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise AgentFrontmatterError(f"{path}: {key!r} values must be non-empty strings")
        result.append(item.strip())
    return result


_OUTPUT_CONTRACT_FIELD_DEFAULTS: dict[str, Any] = {
    "schema": None,
    "required_fields": [],
    "citation_required": False,
    "risk_label_required": False,
    "forbidden_patterns": [],
    "review_required": False,
}


def _normalize_output_contract(raw: Any, path: Path) -> OutputContract:
    if raw is None:
        return OutputContract(
            schema=None,
            required_fields=[],
            citation_required=False,
            risk_label_required=False,
            forbidden_patterns=[],
            review_required=False,
            free_form=True,
        )
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: 'output_contract' must be a mapping")
    unknown = sorted(set(raw) - {*_OUTPUT_CONTRACT_FIELD_DEFAULTS, "free_form"})
    if unknown:
        raise AgentFrontmatterError(
            f"{path}: unknown output_contract field(s): {', '.join(unknown)}"
        )
    merged = dict(_OUTPUT_CONTRACT_FIELD_DEFAULTS)
    merged.update(raw)
    merged["free_form"] = bool(raw.get("free_form", False))
    if not merged["free_form"] and merged["schema"] is None and merged["required_fields"]:
        merged["schema"] = _schema_from_required_fields(merged["required_fields"])
    return cast(OutputContract, merged)


def _schema_from_required_fields(required_fields: list[str]) -> dict[str, Any]:
    fields = [str(field) for field in required_fields]
    return {
        "type": "object",
        "properties": {field: {} for field in fields},
        "required": fields,
        "additionalProperties": True,
    }


def _normalize_permission_profile(raw: Any, path: Path) -> PermissionProfile | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: 'permission_profile' must be a mapping")
    unknown = sorted(
        set(raw)
        - {
            "mode",
            "preauthorized",
            "deny",
            "review_required",
        }
    )
    if unknown:
        raise AgentFrontmatterError(
            f"{path}: unknown permission_profile field(s): {', '.join(unknown)}"
        )
    mode = raw.get("mode")
    if mode is not None and mode not in {"auto", "preview", "trust"}:
        raise AgentFrontmatterError(f"{path}: invalid permission_profile.mode {mode!r}")
    return PermissionProfile(
        mode=mode,
        preauthorized=_string_list(raw.get("preauthorized", []), "preauthorized", path),
        deny=_string_list(raw.get("deny", []), "deny", path),
        review_required=_string_list(raw.get("review_required", []), "review_required", path),
    )


def _normalize_task_protocol(raw: Any, path: Path) -> dict[str, Any]:
    if raw is None:
        return {"mode": "off", "review": "never", "min_items": 1, "max_items": 8}
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: 'task_protocol' must be a mapping")
    unknown = sorted(set(raw) - {"mode", "review", "min_items", "max_items"})
    if unknown:
        raise AgentFrontmatterError(f"{path}: unknown task_protocol field(s): {', '.join(unknown)}")
    mode = raw.get("mode", "off")
    review = raw.get("review", "never")
    minimum = raw.get("min_items", 1)
    maximum = raw.get("max_items", 8)
    if mode not in {"off", "optional", "required"}:
        raise AgentFrontmatterError(f"{path}: invalid task_protocol.mode {mode!r}")
    if review not in {"never", "before_execution"}:
        raise AgentFrontmatterError(f"{path}: invalid task_protocol.review {review!r}")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or minimum < 1
        or maximum < minimum
        or maximum > 50
    ):
        raise AgentFrontmatterError(f"{path}: invalid task_protocol item bounds")
    if mode == "off" and review != "never":
        raise AgentFrontmatterError(f"{path}: task_protocol review requires an active mode")
    return {"mode": mode, "review": review, "min_items": minimum, "max_items": maximum}


def _normalize_interaction_protocol(raw: Any, path: Path) -> dict[str, str]:
    if raw is None:
        return {"startup": "prompt"}
    if not isinstance(raw, dict) or set(raw) - {"startup"}:
        raise AgentFrontmatterError(
            f"{path}: interaction_protocol accepts only the 'startup' field"
        )
    startup = raw.get("startup", "prompt")
    if startup not in {"prompt", "agent"}:
        raise AgentFrontmatterError(f"{path}: invalid interaction_protocol.startup {startup!r}")
    return {"startup": startup}


def _metadata(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise AgentFrontmatterError(f"{path}: 'metadata' must be a mapping")
    result = dict(metadata)
    result["memory_level"] = raw.get("memory_level", result.get("memory_level", "moderate"))
    return result


def _normalize_model(raw: Any, path: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: 'model' must be a mapping")
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "fallback" and isinstance(value, dict):
            result[key] = {
                nested_key: expand_env_vars(nested_value)
                if isinstance(nested_value, str)
                else nested_value
                for nested_key, nested_value in value.items()
            }
        elif isinstance(value, str):
            result[key] = expand_env_vars(value)
        else:
            result[key] = value
    return result


def _normalize_child_templates(raw: Any, path: Path) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise AgentFrontmatterError(f"{path}: 'child_templates' must be an array of tables")
    templates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        source = f"{path}: child_templates[{index}]"
        if not isinstance(item, dict):
            raise AgentFrontmatterError(f"{source} must be a mapping")
        required = {"id", "agent_name", "workflow_id", "limits"}
        if set(item) != required:
            unknown = sorted(set(item) - required)
            missing = sorted(required - set(item))
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if unknown:
                detail.append(f"unknown {', '.join(unknown)}")
            raise AgentFrontmatterError(f"{source} has invalid fields: {'; '.join(detail)}")
        template_id = _require_string(item, "id", path)
        if template_id in seen:
            raise AgentFrontmatterError(f"{path}: duplicate child template id {template_id!r}")
        seen.add(template_id)
        limits = item["limits"]
        if not isinstance(limits, dict) or set(limits) != {"max_steps", "timeout_seconds"}:
            raise AgentFrontmatterError(
                f"{source}.limits requires exactly max_steps and timeout_seconds"
            )
        normalized_limits: dict[str, int] = {}
        for key in ("max_steps", "timeout_seconds"):
            value = limits[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise AgentFrontmatterError(f"{source}.limits.{key} must be a positive integer")
            normalized_limits[key] = value
        templates.append(
            {
                "id": template_id,
                "agent_name": _require_string(item, "agent_name", path),
                "workflow_id": _require_string(item, "workflow_id", path),
                "limits": normalized_limits,
            }
        )
    return templates


def load_agent_object(
    path: Path,
    *,
    tools: list[Any] | None = None,
    skills: list[Any] | None = None,
) -> Any:
    """Load a complete ``ModiAgent`` from a canonical Agent package."""

    from ..api.agent import ModiAgent
    from ..long_task.templates import ChildTemplateLimits, ChildTemplateRef
    from ..types import InteractionProtocolConfig, TaskProtocolConfig, ToolBinding

    package = Path(path)
    manifest = package / "agent.toml" if package.is_dir() else package
    if manifest.name != "agent.toml":
        raise AgentFrontmatterError(
            f"{manifest}: only canonical Agent packages with agent.toml are supported"
        )
    profile = AgentLoader(project_dir=manifest.parent.parent).load_agent(str(manifest))
    metadata = dict(profile["metadata"])
    task_protocol = TaskProtocolConfig(**metadata.pop("task_protocol", {}))
    interaction_protocol = InteractionProtocolConfig(**metadata.pop("interaction_protocol", {}))
    child_templates = tuple(
        ChildTemplateRef(
            id=item["id"],
            agent_name=item["agent_name"],
            workflow_id=item["workflow_id"],
            limits=ChildTemplateLimits(**item["limits"]),
        )
        for item in metadata.pop("child_templates", [])
    )
    return ModiAgent(
        name=profile["name"],
        description=profile["description"],
        instruction=profile["instruction"],
        workflows=tuple(profile["workflows"]),
        child_templates=child_templates,
        tools=tuple(ToolBinding.from_tuple(tool) for tool in (tools or [])),
        skills=tuple(skills or ()),
        output_contract=profile["output_contract"],
        permission_profile=profile["permission_profile"],
        safety_constraints=tuple(profile["safety_constraints"]),
        task_protocol=task_protocol,
        interaction_protocol=interaction_protocol,
        metadata={
            **metadata,
            "_declared_tools": tuple(profile["default_tools"]),
            "_declared_skills": tuple(profile["default_skills"]),
        },
    )


__all__ = ["SUBMIT_OUTPUT_TOOL_NAME", "AgentLoader", "load_agent_object"]
