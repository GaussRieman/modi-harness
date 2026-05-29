"""Agent Loader implementation.

Resolves agent files from project / user / plugin sources, parses Markdown
frontmatter into an AgentProfile, and applies the OutputContract /
PermissionProfile defaults defined in types-reference.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .._utils import parse_frontmatter
from ..models.factory import expand_env_vars
from ..types import AgentProfile, OutputContract, PermissionProfile
from .errors import AgentDuplicateError, AgentFrontmatterError, AgentNotFoundError

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
    }
)


class AgentLoader:
    """Load Markdown agent definitions into AgentProfile records.

    Sources are searched in order: project, user, plugin. A name found in
    more than one source is an error (no implicit override).
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

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def load_agent(self, name_or_path: str) -> AgentProfile:
        path = self._resolve(name_or_path)
        text = path.read_text(encoding="utf-8")
        try:
            fm, body = parse_frontmatter(text)
        except ValueError as exc:
            raise AgentFrontmatterError(f"{path}: {exc}") from exc
        return self._build_profile(fm, body, path)

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
        return sorted(names)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _resolve(self, name_or_path: str) -> Path:
        # Direct path: trust it.
        candidate = Path(name_or_path)
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

        metadata: dict[str, Any] = {
            k: v for k, v in fm.items() if k not in _KNOWN_FIELDS
        }

        # Default memory_level to "moderate" if not specified in frontmatter.
        if "memory_level" not in metadata:
            metadata["memory_level"] = "moderate"

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
    return OutputContract(**merged)  # type: ignore[typeddict-item]


def _normalize_permission_profile(raw: Any, path: Path) -> PermissionProfile | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AgentFrontmatterError(f"{path}: 'permission_profile' must be a mapping")

    mode = raw.get("mode")
    if mode is not None and mode not in ("ask", "auto", "plan", "bypass"):
        raise AgentFrontmatterError(
            f"{path}: 'permission_profile.mode' must be one of ask/auto/plan/bypass"
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
