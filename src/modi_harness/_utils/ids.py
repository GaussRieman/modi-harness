"""Identifier helpers: ULID generation."""

from __future__ import annotations

from ulid import ULID


def new_ulid() -> str:
    """Return a new ULID as a 26-char Crockford base32 string."""
    return str(ULID())
