"""ModiAgent — V0.5 declaration of a governable agent.

A complete, self-contained, immutable definition: profile + agent-scoped tools
+ skills + recursive subagents + optional model override. No run method —
execution lives on ModiSession only.

See docs/superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md §3.2.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..types import (
    ModelSpec,
    OutputContract,
    PermissionProfile,
    Skill,
    ToolBinding,
)

_EMPTY_META: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, eq=True)
class ModiAgent:
    """One governable agent. See spec §3.2 for full contract.

    Hashability caveat: ToolBinding.spec is a dict, so __hash__ raises in the
    general case. ModiSession dedupes by == + linear scan (N is small).
    """

    name: str
    description: str
    instruction: str
    tools: tuple[ToolBinding, ...] = ()
    skills: tuple[Skill, ...] = ()
    subagents: tuple[ModiAgent, ...] = ()
    output_contract: OutputContract | None = None
    permission_profile: PermissionProfile | None = None
    safety_constraints: tuple[str, ...] = ()
    model_override: ModelSpec | None = None
    metadata: Mapping[str, Any] = _EMPTY_META

    def __post_init__(self) -> None:
        # Normalize list/tuple-like inputs to tuple; dict to MappingProxyType.
        # Use object.__setattr__ because frozen dataclasses forbid normal
        # assignment.
        object.__setattr__(self, "tools", _normalize_tools(self.tools))
        object.__setattr__(self, "skills", tuple(self.skills))
        object.__setattr__(self, "subagents", tuple(self.subagents))
        object.__setattr__(
            self, "safety_constraints", tuple(self.safety_constraints)
        )
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(
                self, "metadata", MappingProxyType(dict(self.metadata))
            )

    # ------------------------------------------------------------------
    # Factories — defined in N0.3
    # ------------------------------------------------------------------


def _normalize_tools(
    raw: Iterable[ToolBinding | tuple[dict[str, Any], Any]],
) -> tuple[ToolBinding, ...]:
    return tuple(ToolBinding.from_tuple(t) for t in raw)


__all__ = ["ModiAgent"]
