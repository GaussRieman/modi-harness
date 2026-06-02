"""User/project ``settings.json`` permissions loader.

The ``permissions`` block in ``settings.json`` lets the operator override the
gate without writing a rule pack. Three lists, each accepting either an exact
tool name or a risk-level token (``L0``..``L4``):

.. code-block:: json

    {
      "permissions": {
        "always_allow": ["save_draft", "save_artifact"],
        "always_deny":  ["send_email_blast", "L4"],
        "always_ask":   ["L3"]
      }
    }

User and project files are merged with project taking precedence on the same
entry; overlapping entries are deduplicated while preserving order. Loading
returns a :class:`~modi_harness.config.settings.PermissionsSettings`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..config.settings import PermissionsSettings


def load_permissions(
    user_settings: Path | str | None,
    project_settings: Path | str | None,
) -> PermissionsSettings:
    """Read and merge user + project ``settings.json`` permissions blocks."""
    user = _load_one(user_settings)
    project = _load_one(project_settings)
    return PermissionsSettings(
        always_allow=_merge(user.always_allow, project.always_allow),
        always_deny=_merge(user.always_deny, project.always_deny),
        always_ask=_merge(user.always_ask, project.always_ask),
    )


def _load_one(source: Path | str | None) -> PermissionsSettings:
    if source is None:
        return PermissionsSettings()
    p = Path(source)
    if not p.exists():
        return PermissionsSettings()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return PermissionsSettings()
    block = data.get("permissions") or {}
    return PermissionsSettings(
        always_allow=list(block.get("always_allow") or []),
        always_deny=list(block.get("always_deny") or []),
        always_ask=list(block.get("always_ask") or []),
    )


def _merge(*sources: Iterable[str]) -> list[str]:
    """Deduplicate while preserving first-seen order across sources."""
    seen: set[str] = set()
    out: list[str] = []
    for src in sources:
        for item in src:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


__all__ = ["load_permissions"]
