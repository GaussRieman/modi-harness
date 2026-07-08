"""Agent Loader implementation.

Resolves agent files from project / user / plugin sources, parses Markdown
frontmatter into an AgentProfile, and applies the OutputContract /
PermissionProfile defaults defined in types-reference.md.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

from .._utils import parse_frontmatter
from ..models.factory import expand_env_vars
from ..tasks import TASK_PROTOCOL_TOOL_NAMES
from ..types import AgentProfile, OutputContract, PermissionProfile
from .errors import AgentDuplicateError, AgentFrontmatterError, AgentNotFoundError

# The synthetic tool agents use to deliver a structured final payload. Auto-
# injected into ``default_tools`` for any agent whose ``output_contract`` is
# structured (``free_form=False`` with a ``schema``). The graph intercepts
# calls to this name in ``model_turn_node`` and treats the SDK-parsed args as
# the validated draft, never dispatching to the tool gateway.
SUBMIT_OUTPUT_TOOL_NAME = "submit_output"
REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"

# Frontmatter top-level keys that map directly to AgentProfile fields. Anything
# else falls into ``metadata``.
_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "tools",
        "skills",
        "output_contract",
        "permission_profile",
        "safety_constraints",
        "tags",
        "model",
        "task_protocol",
        "interaction_protocol",
    }
)

_PACKAGE_METADATA_FILES: dict[str, str] = {
    "brain": "brain.toml",
    "rules": "rules.toml",
    "stages": "stages.toml",
    "intent": "intent.toml",
    "loop": "loop.toml",
}


class AgentLoader:
    """Load Markdown agent definitions into AgentProfile records.

    Sources are searched in order: project, user, plugin. A name found in
    more than one source is an error (no implicit override).

    Parsed profiles are cached per resolved file path and invalidated when
    the file's mtime moves. ``stat()`` is cheap (microseconds) compared to
    the YAML parse (~277 µs) so this preserves dev-time edit-and-rerun
    behavior while eliminating redundant parsing across multi-step runs.
    """

    def __init__(
        self,
        project_dir: Path | str | None = None,
        user_dir: Path | str | None = None,
        plugin_dirs: list[Path] | None = None,
    ) -> None:
        self._sources: list[Path] = []
        for d in (project_dir, user_dir, *(plugin_dirs or [])):
            if d is None:
                continue
            self._sources.append(Path(d))
        # path -> (dependency mtime signature, profile)
        self._cache: dict[Path, tuple[int, AgentProfile]] = {}

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def load_agent(self, name_or_path: str) -> AgentProfile:
        path = self._resolve(name_or_path)
        mtime_ns = _agent_definition_mtime_ns(path)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]
        if path.name == "agent.toml":
            profile = self._build_toml_profile(path)
        else:
            text = path.read_text(encoding="utf-8")
            try:
                fm, body = parse_frontmatter(text)
            except ValueError as exc:
                raise AgentFrontmatterError(f"{path}: {exc}") from exc
            profile = self._build_profile(fm, body, path)
        profile = self._with_package_metadata(profile, path)
        self._cache[path] = (mtime_ns, profile)
        return profile

    def list_agent_names(self) -> list[str]:
        """Discover agent names by scanning all configured sources.

        Returns sorted unique names found as either ``<name>.md`` or
        ``<name>/agent.md`` under each source. Used by Subagent Runtime to
        auto-register ``delegate_to_<name>`` tools at harness construction.
        """
        names: set[str] = set()
        for source in self._sources:
            if not source.exists():
                continue
            for entry in source.iterdir():
                if entry.is_file() and entry.suffix == ".md":
                    names.add(entry.stem)
                elif entry.is_dir() and (entry / "agent.md").exists():
                    names.add(entry.name)
                elif entry.is_dir() and (entry / "agent.toml").exists():
                    names.add(entry.name)
        return sorted(names)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _resolve(self, name_or_path: str) -> Path:
        # Direct path: trust it.
        candidate = Path(name_or_path)
        if candidate.name in {"agent.md", "agent.toml"} and candidate.exists():
            return candidate
        if candidate.suffix == ".md" and candidate.exists():
            return candidate

        matches: list[Path] = []
        for source in self._sources:
            p = source / f"{name_or_path}.md"
            if p.exists():
                matches.append(p)
            # Also support agents/<name>/agent.md layout (V0.1 sample style).
            bundled = source / name_or_path / "agent.md"
            if bundled.exists():
                matches.append(bundled)
            package_toml = source / name_or_path / "agent.toml"
            if package_toml.exists():
                matches.append(package_toml)

        if not matches:
            raise AgentNotFoundError(f"agent '{name_or_path}' not found in any source")
        if len(matches) > 1:
            joined = ", ".join(str(m) for m in matches)
            raise AgentDuplicateError(
                f"agent '{name_or_path}' defined in multiple sources: {joined}"
            )
        return matches[0]

    def _build_profile(self, fm: dict[str, Any], body: str, path: Path) -> AgentProfile:
        try:
            name = self._require_str(fm, "name", path)
            description = self._require_str(fm, "description", path)
        except KeyError as exc:
            raise AgentFrontmatterError(f"{path}: missing required frontmatter field {exc}") from None

        default_tools = self._as_list(fm.get("tools", []), "tools", path)
        default_skills = self._as_list(fm.get("skills", []), "skills", path)
        safety_constraints = self._as_list(
            fm.get("safety_constraints", []), "safety_constraints", path
        )
        tags = self._as_list(fm.get("tags", []), "tags", path)

        output_contract = _normalize_output_contract(fm.get("output_contract"), path)
        permission_profile = _normalize_permission_profile(fm.get("permission_profile"), path)
        task_protocol = _normalize_task_protocol(fm.get("task_protocol"), path)
        interaction_protocol = _normalize_interaction_protocol(
            fm.get("interaction_protocol"), path
        )
        if (
            interaction_protocol["startup"] == "agent"
            and REQUEST_USER_INPUT_TOOL_NAME not in default_tools
        ):
            default_tools.append(REQUEST_USER_INPUT_TOOL_NAME)
        if task_protocol["mode"] != "off":
            default_tools.extend(
                name for name in TASK_PROTOCOL_TOOL_NAMES if name not in default_tools
            )

        # Auto-inject submit_output for structured contracts. The model uses
        # this tool to deliver the final structured payload as guaranteed-dict
        # SDK-parsed args, bypassing fragile message.content JSON parsing.
        # See docs/architecture/output-and-hooks.md.
        if (
            not output_contract["free_form"]
            and output_contract.get("schema")
            and SUBMIT_OUTPUT_TOOL_NAME not in default_tools
        ):
            default_tools = [*default_tools, SUBMIT_OUTPUT_TOOL_NAME]

        metadata: dict[str, Any] = {
            k: v for k, v in fm.items() if k not in _KNOWN_FIELDS
        }

        # Default memory_level to "moderate" if not specified in frontmatter.
        if "memory_level" not in metadata:
            metadata["memory_level"] = "moderate"
        if "task_protocol" in fm:
            metadata["task_protocol"] = task_protocol
        if "interaction_protocol" in fm:
            metadata["interaction_protocol"] = interaction_protocol

        # Parse per-agent model block (with env var expansion in string values).
        if "model" in fm:
            model_block = fm["model"]
            if model_block is not None:
                if not isinstance(model_block, dict):
                    raise AgentFrontmatterError(f"{path}: 'model' must be a mapping")
                metadata["model"] = _expand_model_block(model_block)

        return AgentProfile(
            name=name,
            description=description,
            instruction=body.strip(),
            default_tools=default_tools,
            default_skills=default_skills,
            output_contract=output_contract,
            permission_profile=permission_profile,
            safety_constraints=safety_constraints,
            tags=tags,
            metadata=metadata,
        )

    def _build_toml_profile(self, path: Path) -> AgentProfile:
        try:
            raw = _load_package_toml(path)
        except AgentFrontmatterError:
            raise
        if set(raw) == {"factory"}:
            raise AgentFrontmatterError(
                f"{path}: factory agent.toml must be loaded by the project discovery factory"
            )

        fm = _profile_frontmatter_from_toml(raw, path)
        instruction = _instruction_from_toml(raw, path)
        return self._build_profile(fm, instruction, path)

    def _with_package_metadata(self, profile: AgentProfile, path: Path) -> AgentProfile:
        package_dir = path.parent if path.name in {"agent.md", "agent.toml"} else None
        if package_dir is None:
            return profile

        package_metadata: dict[str, Any] = {}
        for key, filename in _PACKAGE_METADATA_FILES.items():
            package_file = package_dir / filename
            if not package_file.exists():
                continue
            package_metadata[key] = _load_package_toml(package_file)

        if not package_metadata and path.name != "agent.toml":
            return profile

        updated = AgentProfile(**profile)
        metadata = dict(profile["metadata"])
        package = dict(metadata.get("package") or {})
        package["root"] = str(package_dir)
        package["files"] = {
            key: filename
            for key, filename in _PACKAGE_METADATA_FILES.items()
            if (package_dir / filename).exists()
        }
        metadata["package"] = package

        for key, value in package_metadata.items():
            metadata[key] = _merge_package_block(metadata.get(key), value)
        if "rules" in package_metadata:
            brain = dict(metadata.get("brain") or {})
            fast_rules = dict(brain.get("fast_rules") or {})
            fast_rules.update(dict(package_metadata["rules"]))
            brain["fast_rules"] = fast_rules
            metadata["brain"] = brain

        updated["metadata"] = metadata
        return updated

    @staticmethod
    def _require_str(fm: dict[str, Any], key: str, path: Path) -> str:
        if key not in fm:
            raise AgentFrontmatterError(f"{path}: missing required frontmatter field '{key}'")
        value = fm[key]
        if not isinstance(value, str) or not value.strip():
            raise AgentFrontmatterError(f"{path}: '{key}' must be a non-empty string")
        return value

    @staticmethod
    def _as_list(value: Any, key: str, path: Path) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        raise AgentFrontmatterError(f"{path}: '{key}' must be a list")


# ----------------------------------------------------------------------
# normalization helpers
# ----------------------------------------------------------------------


_OUTPUT_CONTRACT_FIELD_DEFAULTS: dict[str, Any] = {
    "schema": None,
    "required_fields": [],
    "citation_required": False,
    "risk_label_required": False,
    "forbidden_patterns": [],
    "review_required": False,
}


def _normalize_output_contract(raw: Any, path: Path) -> OutputContract:
    """Apply defaults per types-reference §3.

    - Absent block → free_form pass-through (free_form=True, others default).
    - Present block → free_form defaults to False; declared fields override.
    """
    if raw is None:
        return OutputContract(  # type: ignore[typeddict-item]
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

    merged: dict[str, Any] = dict(_OUTPUT_CONTRACT_FIELD_DEFAULTS)
    merged["free_form"] = bool(raw.get("free_form", False))
    for field, default in _OUTPUT_CONTRACT_FIELD_DEFAULTS.items():
        if field in raw:
            merged[field] = raw[field]
        else:
            merged[field] = default
    if (
        not merged["free_form"]
        and merged.get("schema") is None
        and merged.get("required_fields")
    ):
        merged["schema"] = _schema_from_required_fields(merged["required_fields"])
    return OutputContract(**merged)  # type: ignore[typeddict-item]


def _schema_from_required_fields(required_fields: list[str]) -> dict[str, Any]:
    """Build the minimal object schema needed for structured submission.

    Agent authors can still provide a richer schema. This fallback exists so a
    declared structured contract with only required fields can use the
    submit_output protocol instead of asking the model to hand-write JSON text.
    """
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

    mode = raw.get("mode")
    if mode is not None and mode not in ("auto", "preview", "trust"):
        raise AgentFrontmatterError(
            f"{path}: 'permission_profile.mode' must be one of auto/preview/trust"
        )

    max_depth_raw = raw.get("subagent_max_depth")
    if max_depth_raw is not None and not isinstance(max_depth_raw, int):
        raise AgentFrontmatterError(
            f"{path}: 'permission_profile.subagent_max_depth' must be an integer or null"
        )

    return PermissionProfile(
        mode=mode,
        preauthorized=list(raw.get("preauthorized", []) or []),
        deny=list(raw.get("deny", []) or []),
        review_required=list(raw.get("review_required", []) or []),
        allowed_subagents=list(raw.get("allowed_subagents", []) or []),
        subagent_max_depth=max_depth_raw,
    )


def _normalize_task_protocol(raw: Any, path: Path) -> dict[str, Any]:
    if raw is None:
        return {"mode": "off", "review": "never", "min_items": 1, "max_items": 8}
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: 'task_protocol' must be a mapping")
    unknown = sorted(set(raw) - {"mode", "review", "min_items", "max_items"})
    if unknown:
        raise AgentFrontmatterError(
            f"{path}: unknown task_protocol field(s): {', '.join(unknown)}"
        )
    mode = raw.get("mode", "off")
    review = raw.get("review", "never")
    min_items = raw.get("min_items", 1)
    max_items = raw.get("max_items", 8)
    if mode not in ("off", "optional", "required"):
        raise AgentFrontmatterError(f"{path}: invalid task_protocol.mode: {mode!r}")
    if review not in ("never", "before_execution"):
        raise AgentFrontmatterError(f"{path}: invalid task_protocol.review: {review!r}")
    if not isinstance(min_items, int) or not isinstance(max_items, int):
        raise AgentFrontmatterError(f"{path}: task_protocol min/max items must be integers")
    if min_items < 1 or max_items < min_items or max_items > 50:
        raise AgentFrontmatterError(f"{path}: invalid task_protocol item bounds")
    if mode == "off" and review != "never":
        raise AgentFrontmatterError(f"{path}: task_protocol review requires mode optional/required")
    return {
        "mode": mode,
        "review": review,
        "min_items": min_items,
        "max_items": max_items,
    }


def _normalize_interaction_protocol(raw: Any, path: Path) -> dict[str, str]:
    if raw is None:
        return {"startup": "prompt"}
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: 'interaction_protocol' must be a mapping")
    unknown = sorted(set(raw) - {"startup"})
    if unknown:
        raise AgentFrontmatterError(
            f"{path}: unknown interaction_protocol field(s): {', '.join(unknown)}"
        )
    startup = raw.get("startup", "prompt")
    if startup not in ("prompt", "agent"):
        raise AgentFrontmatterError(
            f"{path}: invalid interaction_protocol.startup: {startup!r}"
        )
    return {"startup": startup}


def _load_package_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentFrontmatterError(f"{path}: cannot read TOML: {exc}") from exc
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: TOML root must be a mapping")
    return raw


def _agent_definition_mtime_ns(path: Path) -> int:
    mtimes = [path.stat().st_mtime_ns]
    package_dir = path.parent if path.name in {"agent.md", "agent.toml"} else None
    if package_dir is not None:
        for filename in _PACKAGE_METADATA_FILES.values():
            package_file = package_dir / filename
            if package_file.exists():
                mtimes.append(package_file.stat().st_mtime_ns)
        if path.name == "agent.toml":
            raw = _load_package_toml(path)
            instruction_file = raw.get("instruction_file")
            if isinstance(instruction_file, str) and instruction_file.strip():
                instruction_path = (package_dir / instruction_file).resolve()
                try:
                    instruction_path.relative_to(package_dir.resolve())
                except ValueError:
                    return max(mtimes)
                if instruction_path.exists():
                    mtimes.append(instruction_path.stat().st_mtime_ns)
            elif (package_dir / "agent.md").exists():
                mtimes.append((package_dir / "agent.md").stat().st_mtime_ns)
    return max(mtimes)


def _profile_frontmatter_from_toml(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    allowed = {
        "name",
        "description",
        "tools",
        "skills",
        "output_contract",
        "permission_profile",
        "safety_constraints",
        "tags",
        "model",
        "task_protocol",
        "interaction_protocol",
        "memory_level",
        "metadata",
        "instruction",
        "instruction_file",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise AgentFrontmatterError(
            f"{path}: unknown agent.toml field(s): {', '.join(unknown)}"
        )

    fm: dict[str, Any] = {
        key: value
        for key, value in raw.items()
        if key not in {"instruction", "instruction_file", "metadata"}
    }
    metadata = raw.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise AgentFrontmatterError(f"{path}: 'metadata' must be a mapping")
        fm.update(metadata)
    return fm


def _instruction_from_toml(raw: dict[str, Any], path: Path) -> str:
    direct = raw.get("instruction")
    file_ref = raw.get("instruction_file")
    if direct is not None and file_ref is not None:
        raise AgentFrontmatterError(
            f"{path}: declare only one of instruction or instruction_file"
        )
    if direct is not None:
        if not isinstance(direct, str):
            raise AgentFrontmatterError(f"{path}: 'instruction' must be a string")
        return direct
    if file_ref is None:
        fallback = path.parent / "agent.md"
        if fallback.exists():
            return _instruction_body_from_markdown(fallback)
        return ""
    if not isinstance(file_ref, str) or not file_ref.strip():
        raise AgentFrontmatterError(f"{path}: 'instruction_file' must be a non-empty string")
    instruction_path = (path.parent / file_ref).resolve()
    package_dir = path.parent.resolve()
    try:
        instruction_path.relative_to(package_dir)
    except ValueError:
        raise AgentFrontmatterError(
            f"{path}: instruction_file must stay inside the agent package"
        ) from None
    if not instruction_path.exists():
        raise AgentFrontmatterError(f"{path}: instruction_file not found: {file_ref}")
    if instruction_path.suffix == ".md":
        return _instruction_body_from_markdown(instruction_path)
    return instruction_path.read_text(encoding="utf-8").strip()


def _instruction_body_from_markdown(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    try:
        _fm, body = parse_frontmatter(text)
    except ValueError as exc:
        raise AgentFrontmatterError(f"{path}: {exc}") from exc
    return body.strip()


def _merge_package_block(existing: Any, package_value: dict[str, Any]) -> dict[str, Any]:
    base = dict(existing or {}) if isinstance(existing, dict) else {}
    incoming = dict(package_value)
    if "fast_rules" in incoming:
        fast_rules = dict(base.get("fast_rules") or {})
        raw_fast_rules = incoming.pop("fast_rules")
        if isinstance(raw_fast_rules, dict):
            fast_rules.update(raw_fast_rules)
        base["fast_rules"] = fast_rules
    if "rules" in incoming and "fast_rules" not in base:
        rules = incoming.get("rules")
        if isinstance(rules, dict):
            base["fast_rules"] = dict(rules)
    base.update(incoming)
    return base


# Re-exported for convenience: when a test or caller already has parsed YAML.
def parse_yaml(text: str) -> Any:  # pragma: no cover — thin alias
    return yaml.safe_load(text)


def _expand_model_block(block: dict[str, Any]) -> dict[str, Any]:
    """Apply ``expand_env_vars`` to all string values, recursing into ``fallback``."""
    out: dict[str, Any] = {}
    for key, val in block.items():
        if key == "fallback" and isinstance(val, dict):
            out[key] = {
                k: expand_env_vars(v) if isinstance(v, str) else v
                for k, v in val.items()
            }
        elif isinstance(val, str):
            out[key] = expand_env_vars(val)
        else:
            out[key] = val
    return out


def load_agent_object(
    path: Path,
    *,
    tools: list[Any] | None = None,
    skills: list[Any] | None = None,
    subagents: list[Any] | None = None,
) -> Any:
    """Parse a markdown file at ``path`` and return a ModiAgent.

    Defers import of ModiAgent / ToolBinding to avoid circulars; the import
    is local to keep agents.loader module-import-time free of api/agent.
    """
    from ..api.agent import ModiAgent
    from ..types import ToolBinding

    text = path.read_text(encoding="utf-8")
    try:
        fm, body = parse_frontmatter(text)
    except ValueError as exc:
        raise AgentFrontmatterError(f"{path}: {exc}") from exc
    # Reuse the existing AgentProfile builder so frontmatter rules stay one
    # source of truth, then project to ModiAgent fields.
    loader = AgentLoader(project_dir=path.parent)
    profile = loader._build_profile(fm, body, path)

    metadata = dict(profile["metadata"])
    task_protocol_raw = metadata.pop("task_protocol", {})
    interaction_protocol_raw = metadata.pop("interaction_protocol", {})
    from ..types import InteractionProtocolConfig, TaskProtocolConfig

    return ModiAgent(
        name=profile["name"],
        description=profile["description"],
        instruction=profile["instruction"],
        tools=tuple(ToolBinding.from_tuple(t) for t in (tools or [])),
        skills=tuple(skills or ()),
        subagents=tuple(subagents or ()),
        output_contract=profile["output_contract"],
        permission_profile=profile["permission_profile"],
        safety_constraints=tuple(profile["safety_constraints"]),
        task_protocol=TaskProtocolConfig(**task_protocol_raw),
        interaction_protocol=InteractionProtocolConfig(**interaction_protocol_raw),
        metadata={
            **metadata,
            # Carry frontmatter-declared tool names forward so agent_to_profile
            # includes them in default_tools alongside attached ToolBindings.
            # Without this, tools declared only in agent.md (e.g. delegate_to_*)
            # are invisible to the model at call time.
            "_frontmatter_tools": tuple(profile["default_tools"]),
        },
    )
