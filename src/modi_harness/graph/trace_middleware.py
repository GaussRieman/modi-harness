"""Cursor-based trace flusher for the V0.2 LangGraph runtime.

Nodes append :class:`TraceEvent`\\s to ``state["pending_trace_events"]``. The
list field has an ``operator.add`` reducer, so accumulated events grow
monotonically through the run. This middleware keeps a per-thread, per-process
write cursor so each event is written to ``trace.jsonl`` exactly once.

On resume in a fresh process, the cursor is empty; we rebuild it by reading
the existing ``trace.jsonl`` and indexing already-written events by
``event_id``. Subsequent writes skip duplicates.

Concurrent writers on the same host are serialized by the fcntl file lock
that :meth:`WorkspaceManager.append_log` already holds.
"""

from __future__ import annotations

import json
from typing import Any

from ..types import TraceEvent
from ..workspace import WorkspaceManager


class TraceMiddleware:
    """Drain ``pending_trace_events`` into ``trace.jsonl`` exactly once each."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._workspace = workspace
        self._written: dict[str, set[str]] = {}  # run_id -> set of event_ids

    def flush(self, state: dict[str, Any]) -> None:
        events: list[TraceEvent] = list(state.get("pending_trace_events") or [])
        if not events:
            return
        run_id = state.get("run_id")
        if not run_id:
            return

        seen = self._written.get(run_id)
        if seen is None:
            seen = self._rebuild_cursor(run_id)
            self._written[run_id] = seen

        for event in events:
            event_id = event.get("event_id")
            if not event_id or event_id in seen:
                continue
            line = json.dumps(event, ensure_ascii=False)
            self._workspace.append_log(run_id, "trace", line)
            seen.add(event_id)

    def _rebuild_cursor(self, run_id: str) -> set[str]:
        try:
            trace_path = self._workspace._run_dir(run_id) / "logs" / "trace.jsonl"
        except Exception:
            return set()
        if not trace_path.exists():
            return set()
        seen: set[str] = set()
        with trace_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event_id := event.get("event_id"):
                    seen.add(event_id)
        return seen


__all__ = ["TraceMiddleware"]
