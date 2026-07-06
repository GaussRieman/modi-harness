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

        for event in _with_run_summaries(events):
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


def _with_run_summaries(events: list[TraceEvent]) -> list[TraceEvent]:
    summary = _run_summary(events)
    if summary is None:
        return events
    enriched: list[TraceEvent] = []
    for event in events:
        if event.get("event_type") != "run_end":
            enriched.append(event)
            continue
        cloned = dict(event)
        payload = dict(cloned.get("payload") or {})
        for key, value in summary.items():
            payload.setdefault(key, value)
        cloned["payload"] = payload
        enriched.append(cloned)  # type: ignore[arg-type]
    return enriched


def _run_summary(events: list[TraceEvent]) -> dict[str, Any] | None:
    if not any(event.get("event_type") == "run_end" for event in events):
        return None

    model_count = 0
    model_latency_ms = 0
    model_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": None,
    }
    known_cost = True
    total_cost = 0.0
    fallback_used = False
    tool_attempts = 0
    tool_failures = 0
    tool_latency_ms = 0

    for event in events:
        payload = event.get("payload") or {}
        if event.get("event_type") == "model_result":
            model_count += 1
            model_latency_ms += int(payload.get("elapsed_ms") or 0)
            usage = payload.get("usage") or {}
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            ):
                model_usage[key] += int(usage.get(key) or 0)
            cost = usage.get("cost_usd")
            if cost is None:
                known_cost = False
            else:
                total_cost += float(cost)
            fallback_used = fallback_used or bool(payload.get("fallback_used"))
        elif event.get("event_type") == "tool_result":
            tool_attempts += int(payload.get("attempt") or 1)
            if payload.get("error_code"):
                tool_failures += 1
            tool_latency_ms += int(payload.get("elapsed_ms") or 0)

    if known_cost:
        model_usage["cost_usd"] = total_cost

    return {
        "model_calls": model_count,
        "model_latency_ms": model_latency_ms,
        "model_usage": model_usage,
        "model_fallback_used": fallback_used,
        "tool_attempts": tool_attempts,
        "tool_failures": tool_failures,
        "tool_latency_ms": tool_latency_ms,
    }


__all__ = ["TraceMiddleware"]
