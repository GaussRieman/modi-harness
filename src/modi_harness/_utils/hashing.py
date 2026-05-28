"""Canonical JSON serialization, fingerprint, context hash.

These are the deterministic-replay primitives. ``canonical_json`` is the only
serialization used for ``fingerprint`` (denied-retry) and ``context_hash``
(trace replay). Implementations must not introduce environment-dependent
ordering.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Fields excluded from context_hash. Volatile or implementation-detail fields
# whose change should not invalidate prompt caching / replay.
_CONTEXT_HASH_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "context_hash",
        "raw",
        "timestamp",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "decided_at",
        "requested_at",
    }
)


def canonical_json(obj: Any) -> bytes:
    """Serialize ``obj`` to canonical JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_default,
    ).encode("utf-8")


def compute_fingerprint(obj: Any) -> str:
    """SHA-256 hex digest of ``canonical_json(obj)``."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def compute_context_hash(pack: Any) -> str:
    """Stable hash for a ContextPack-shaped mapping.

    Strips volatile fields (timestamps, raw provider payloads, the prior
    context_hash) before hashing so replays reproduce the same value.
    """
    stripped = _strip_volatile(pack)
    return compute_fingerprint(stripped)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_volatile(v)
            for k, v in value.items()
            if k not in _CONTEXT_HASH_EXCLUDED_FIELDS
        }
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _default(value: Any) -> Any:
    # Fallback for objects json can't serialize. We try a few well-known shapes
    # and otherwise raise — silent best-effort here would mask determinism bugs.
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "_asdict"):
        return value._asdict()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"canonical_json: unsupported type {type(value).__name__}")
