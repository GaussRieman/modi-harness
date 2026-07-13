"""Identifier helpers: ULID generation."""

from __future__ import annotations

from threading import Lock

from ulid import ULID

_LOCK = Lock()
_LAST_VALUE = -1


def new_ulid() -> str:
    """Return a process-monotonic 26-character Crockford ULID."""

    global _LAST_VALUE
    with _LOCK:
        value = int.from_bytes(ULID().bytes)
        if value <= _LAST_VALUE:
            value = _LAST_VALUE + 1
        _LAST_VALUE = value
        return str(ULID.from_int(value))
