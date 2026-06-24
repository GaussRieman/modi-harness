"""Autonomy modes — how much freedom the agent has inside the intent field.

``AutonomyMode`` replaces ``PermissionMode`` as the runtime's description of
agent freedom. It is *derived* from intent clarity and active boundaries
(plan N2, spec D3), not selected as a permission posture.

- ``guided``     — high human involvement; ambiguous starts.
- ``bounded``    — default; act freely inside declared boundaries.
- ``delegated``  — high autonomy; goal and boundaries are clear.
- ``constrained``— low autonomy; risky or responsibility-heavy work.
"""

from __future__ import annotations

from typing import Literal

from modi_harness.intent.types import IntentClarityLevel

AutonomyMode = Literal["guided", "bounded", "delegated", "constrained"]

# Default clarity -> mode mapping (spec AutonomyScope section). ``partial`` maps
# to ``guided`` (the safe end of the documented "guided/bounded" range); it is
# distinguished from ``thin`` by the scope's allowed actions, not the mode name.
_CLARITY_TO_MODE: dict[IntentClarityLevel, AutonomyMode] = {
    "thin": "guided",
    "partial": "guided",
    "operational": "bounded",
    "stable": "delegated",
}


def mode_for_clarity(level: IntentClarityLevel) -> AutonomyMode:
    """Map a clarity level to its default autonomy mode (before boundary override)."""
    return _CLARITY_TO_MODE[level]


__all__ = ["AutonomyMode", "mode_for_clarity"]
