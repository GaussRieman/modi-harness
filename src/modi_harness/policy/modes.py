"""Mode normalization.

The product surface exposes three modes: ``auto``, ``preview``, ``trust``
(see ``docs/architecture/tools-and-policy.md``). The legacy 4-mode names
(``ask``, ``plan``, ``bypass``) remain accepted for one minor release as
deprecation aliases.

Legacy mapping
--------------

- ``ask`` → ``auto``        (the new ``auto`` asks when interactive, denies otherwise)
- ``plan`` → ``preview``    (renamed for clarity, plus the intercept rule)
- ``bypass`` → ``trust``    (renamed; gains MODI_ALLOW_TRUST=1 startup guard elsewhere)
- ``auto`` → ``auto``       (unchanged)
- ``preview`` / ``trust``   (target names, no warning)
"""

from __future__ import annotations

import os
import warnings
from typing import Literal

# Subset of PermissionMode that this module emits. Callers should still
# treat the result as ``PermissionMode`` for typing purposes.
_TargetMode = Literal["auto", "preview", "trust"]

_ALIAS_MAP: dict[str, tuple[_TargetMode, bool]] = {
    # legacy → (target, is_deprecated)
    "ask": ("auto", True),
    "plan": ("preview", True),
    "bypass": ("trust", True),
    # target names pass through unchanged
    "auto": ("auto", False),
    "preview": ("preview", False),
    "trust": ("trust", False),
}


def normalize_mode(mode: str) -> _TargetMode:
    """Normalize a possibly-legacy mode name to a target name.

    Emits ``DeprecationWarning`` for legacy aliases (``ask``, ``plan``,
    ``bypass``). Raises ``ValueError`` for unknown values.
    """
    if mode not in _ALIAS_MAP:
        raise ValueError(
            f"unknown mode: {mode!r} (expected one of: auto, preview, trust)"
        )
    target, deprecated = _ALIAS_MAP[mode]
    if deprecated:
        warnings.warn(
            f"mode={mode!r} is deprecated; use {target!r} instead. "
            "See docs/architecture/tools-and-policy.md for the migration map.",
            DeprecationWarning,
            stacklevel=3,
        )
    return target


__all__ = ["normalize_mode", "enforce_trust_guard"]


def enforce_trust_guard(mode: str) -> None:
    """Raise RuntimeError if ``mode == 'trust'`` and the environment is not opted in.

    ``trust`` mode disables the policy gate. To use it, the operator must set
    ``MODI_ALLOW_TRUST=1`` (exactly the string ``'1'``) in the environment.
    Any other value, including unset, blocks the mode at run start.

    The intent is to make it impossible to ship a config that quietly enables
    ``trust`` mode in production. The env var is a one-line acknowledgement
    that the operator knows they're disabling governance.
    """
    if mode != "trust":
        return
    if os.environ.get("MODI_ALLOW_TRUST") != "1":
        raise RuntimeError(
            "mode='trust' disables the policy gate and is not allowed unless "
            "MODI_ALLOW_TRUST=1 is set in the environment. "
            "See docs/architecture/tools-and-policy.md for rationale."
        )
