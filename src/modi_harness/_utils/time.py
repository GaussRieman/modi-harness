"""Time helpers: ISO 8601 UTC with millisecond precision."""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """Return current UTC time as ``YYYY-MM-DDTHH:MM:SS.sssZ``."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"
