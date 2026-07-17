"""ModiAgent — immutable definition of a Workflow-governed agent.

A complete, self-contained, immutable definition: profile + agent-scoped tools
+ skills + optional model override. Every Agent owns at
least one explicit Workflow. No run method —
execution lives on ModiSession only.

See docs/superpowers/specs/2026-07-12-single-brain-mandatory-workflow-hard-cut-design.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from ..types import (
    InteractionProtocolConfig,
    ModelSpec,
    OutputContract,
    PermissionProfile,
    Skill,
    TaskProtocolConfig,
    ToolBinding,
)
from ..workflow import CompletionValidator, PinnedComponent, Workflow

_EMPTY_META: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, eq=True)
class ModiAgent:
    """One governable Workflow-owned agent.

    Hashability caveat: ToolBinding.spec is a dict, so __hash__ raises in the
    general case. ModiSession dedupes by == + linear scan (N is small).
    """

    name: str
    description: str
    instruction: str
    workflows: tuple[Workflow, ...]
    completion_validators: tuple[CompletionValidator, ...] = ()
    task_graph_components: tuple[PinnedComponent, ...] = ()
    tools: tuple[ToolBinding, ...] = ()
    skills: tuple[Skill, ...] = ()
    output_contract: OutputContract | None = None
    permission_profile: PermissionProfile | None = None
    safety_constraints: tuple[str, ...] = ()
    model_override: ModelSpec | None = None
    metadata: Mapping[str, Any] = _EMPTY_META
    task_protocol: TaskProtocolConfig = field(default_factory=TaskProtocolConfig)
    interaction_protocol: InteractionProtocolConfig = field(
        default_factory=InteractionProtocolConfig
    )

    def __post_init__(self) -> None:
        # Normalize list/tuple-like inputs to tuple; dict to MappingProxyType.
        # Use object.__setattr__ because frozen dataclasses forbid normal
        # assignment.
        object.__setattr__(self, "tools", _normalize_tools(self.tools))
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "workflows", tuple(self.workflows))
        object.__setattr__(self, "completion_validators", tuple(self.completion_validators))
        object.__setattr__(self, "task_graph_components", tuple(self.task_graph_components))
        if not self.workflows:
            raise ValueError("ModiAgent requires at least one Workflow")
        workflow_ids = [workflow.id for workflow in self.workflows]
        if len(workflow_ids) != len(set(workflow_ids)):
            raise ValueError("ModiAgent Workflow ids must be unique")
        validator_ids = [validator.id for validator in self.completion_validators]
        if len(validator_ids) != len(set(validator_ids)):
            raise ValueError("ModiAgent completion validator ids must be unique")
        component_ids = [component.id for component in self.task_graph_components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("ModiAgent Task Graph component ids must be unique")
        declared_validator_ids = {
            node.completion_validator
            for workflow in self.workflows
            for node in workflow.nodes
            if node.completion_validator is not None
        }
        missing_validators = declared_validator_ids - set(validator_ids)
        if missing_validators:
            joined = ", ".join(sorted(missing_validators))
            raise ValueError(f"Workflow declares unbound completion validator(s): {joined}")
        object.__setattr__(self, "safety_constraints", tuple(self.safety_constraints))
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_package(
        cls,
        path: Path,
        *,
        tools: Iterable[ToolBinding | tuple[dict[str, Any], Any]] | None = None,
        skills: Iterable[Skill] | None = None,
    ) -> ModiAgent:
        """Load a canonical Agent package directory or its ``agent.toml``."""

        from ..agents.loader import load_agent_object

        return cast(
            ModiAgent,
            load_agent_object(
                path,
                tools=list(tools) if tools is not None else None,
                skills=list(skills) if skills is not None else None,
            ),
        )

    @classmethod
    def load_dir(cls, directory: Path) -> list[ModiAgent]:
        """Load every canonical ``<name>/agent.toml`` package under a root."""
        directory = Path(directory)
        agents: list[ModiAgent] = []
        if not directory.exists():
            return agents
        for entry in sorted(directory.iterdir()):
            if entry.is_dir() and (entry / "agent.toml").is_file():
                agents.append(cls.from_package(entry))
        return agents


def _normalize_tools(
    raw: Iterable[ToolBinding | tuple[dict[str, Any], Any]],
) -> tuple[ToolBinding, ...]:
    return tuple(ToolBinding.from_tuple(t) for t in raw)


__all__ = ["ModiAgent"]
