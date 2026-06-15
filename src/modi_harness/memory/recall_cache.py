"""In-process per-run cache for memory recall and selection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class RunRecallCache:
    """Cache one run's memory recall/selection result until invalidated."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[Any, Any]] = {}

    def get_or_compute(
        self,
        run_id: str,
        compute_fn: Callable[[], tuple[Any, Any]],
    ) -> tuple[Any, Any]:
        if run_id not in self._entries:
            self._entries[run_id] = compute_fn()
        return self._entries[run_id]

    def invalidate(self, run_id: str) -> None:
        self._entries.pop(run_id, None)


__all__ = ["RunRecallCache"]
