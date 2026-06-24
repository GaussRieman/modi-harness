"""Mode validation.

The product surface exposes exactly three modes: ``auto``, ``preview``,
``trust`` (see ``docs/architecture/tools-and-policy.md``). The legacy 4-mode
names (``ask``, ``plan``, ``bypass``) were removed in the intent-aligned
runtime redesign; nothing accepts them anymore.

- ``auto``    — ask when interactive, deny otherwise
- ``preview`` — intercept side effects; allow reversible work
- ``trust``   — disable the policy gate (gated by MODI_ALLOW_TRUST=1)
"""

from __future__ import annotations

import os
from typing import Literal

_TargetMode = Literal["auto", "preview", "trust"]

_VALID_MODES: frozenset[str] = frozenset({"auto", "preview", "trust"})


def normalize_mode(mode: str) -> _TargetMode:
    """Validate a mode name and return it unchanged.

    Raises ``ValueError`` for any value outside ``auto``/``preview``/``trust``
    (which now includes the removed legacy aliases ``ask``/``plan``/``bypass``).
    """
    if mode not in _VALID_MODES:
        raise ValueError(
            f"unknown mode: {mode!r} (expected one of: auto, preview, trust)"
        )
    return mode  # type: ignore[return-value]


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
