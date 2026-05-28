"""Frontmatter parser shared by Agent Loader, Skill Loader, Memory Store.

Accepts hyphen and underscore spellings; canonicalizes to underscore on output.
"""

from __future__ import annotations

from typing import Any

import yaml

_FENCE = "---"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into (frontmatter dict, body).

    Returns ``({}, text)`` when no frontmatter fence is present.
    Hyphenated top-level keys (``allowed-tools``) are normalized to underscore.
    Raises ``ValueError`` when an opening fence has no closing fence.
    """
    if not text.startswith(_FENCE):
        return {}, text

    rest = text[len(_FENCE):]
    if rest.startswith("\n"):
        rest = rest[1:]
    elif rest.startswith(_FENCE):
        # Empty fence pair "------" with no newline; treat as empty frontmatter.
        body = rest[len(_FENCE):]
        if body.startswith("\n"):
            body = body[1:]
        return {}, body
    else:
        return {}, text

    # Closing fence may appear at line start either as "\n---" or as the very
    # first characters when the YAML block is empty.
    if rest.startswith(_FENCE):
        body = rest[len(_FENCE):]
        if body.startswith("\n"):
            body = body[1:]
        return {}, body

    end = rest.find(f"\n{_FENCE}")
    if end == -1:
        raise ValueError("frontmatter opened with --- but no closing --- found")

    raw = rest[:end]
    body_start = end + len(f"\n{_FENCE}")
    body = rest[body_start:]
    if body.startswith("\n"):
        body = body[1:]

    try:
        data = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a YAML mapping")

    return _normalize_keys(data), body


def _normalize_keys(d: dict[str, Any]) -> dict[str, Any]:
    """Top-level only: hyphenated keys become underscored.

    When both spellings exist for the same logical key, the underscore form wins.
    """
    out: dict[str, Any] = {}
    for k, v in d.items():
        if "-" in k:
            normalized = k.replace("-", "_")
            if normalized in d:
                continue  # underscore form already present, drop hyphen variant
            out[normalized] = v
        else:
            out[k] = v
    return out
