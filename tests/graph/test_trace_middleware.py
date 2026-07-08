"""TraceMiddleware cursor + idempotency."""

from __future__ import annotations

import json
from pathlib import Path

from modi_harness._utils import new_ulid, now_iso
from modi_harness.graph import TraceMiddleware
from modi_harness.types import TraceEvent
from modi_harness.workspace import WorkspaceManager


def _event(run_id: str, event_type: str, payload=None) -> TraceEvent:
    return TraceEvent(  # type: ignore[typeddict-item]
        event_id=new_ulid(),
        run_id=run_id,
        root_run_id=run_id,
        parent_run_id=None,
        thread_id=run_id,
        timestamp=now_iso(),
        event_type=event_type,
        payload=payload or {},
        payload_ref=None,
    )


def test_flush_writes_each_event_once(tmp_path: Path) -> None:
    ws = WorkspaceManager(workspace_root=tmp_path / "ws")
    run_id = new_ulid()
    ws.create_run(run_id)
    mw = TraceMiddleware(ws)
    e1 = _event(run_id, "run_start")
    e2 = _event(run_id, "run_end")
    mw.flush({"run_id": run_id, "pending_trace_events": [e1, e2]})
    mw.flush({"run_id": run_id, "pending_trace_events": [e1, e2]})  # second flush — dedupe

    trace = (tmp_path / "ws" / run_id / "logs" / "trace.jsonl").read_text().splitlines()
    assert len(trace) == 2


def test_cursor_rebuilds_from_disk(tmp_path: Path) -> None:
    ws = WorkspaceManager(workspace_root=tmp_path / "ws")
    run_id = new_ulid()
    ws.create_run(run_id)
    mw1 = TraceMiddleware(ws)
    e1 = _event(run_id, "run_start")
    mw1.flush({"run_id": run_id, "pending_trace_events": [e1]})

    # Simulate a fresh process: new middleware with empty in-memory cursor.
    mw2 = TraceMiddleware(ws)
    e2 = _event(run_id, "run_end")
    mw2.flush({"run_id": run_id, "pending_trace_events": [e1, e2]})  # e1 must dedupe

    trace = (tmp_path / "ws" / run_id / "logs" / "trace.jsonl").read_text().splitlines()
    assert len(trace) == 2


def test_run_end_is_enriched_with_runtime_summary(tmp_path: Path) -> None:
    ws = WorkspaceManager(workspace_root=tmp_path / "ws")
    run_id = new_ulid()
    ws.create_run(run_id)
    mw = TraceMiddleware(ws)
    model = _event(
        run_id,
        "model_result",
        {
            "elapsed_ms": 12,
            "fallback_used": True,
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "total_tokens": 12,
                "cache_read_tokens": 2,
                "cache_write_tokens": 3,
                "cost_usd": None,
            },
        },
    )
    tool_ok = _event(
        run_id,
        "tool_result",
        {"attempt": 1, "elapsed_ms": 4, "error_code": None},
    )
    tool_failed = _event(
        run_id,
        "tool_result",
        {"attempt": 1, "elapsed_ms": 6, "error_code": "schema_validation_failed"},
    )
    end = _event(run_id, "run_end", {"status": "completed"})

    mw.flush({"run_id": run_id, "pending_trace_events": [model, tool_ok, tool_failed, end]})

    rows = [
        json.loads(line)
        for line in (tmp_path / "ws" / run_id / "logs" / "trace.jsonl").read_text().splitlines()
    ]
    payload = rows[-1]["payload"]
    assert payload["model_calls"] == 1
    assert payload["model_latency_ms"] == 12
    assert payload["model_fallback_used"] is True
    assert payload["model_usage"]["total_tokens"] == 12
    assert payload["model_usage"]["cache_read_tokens"] == 2
    assert payload["model_usage"]["cost_usd"] is None
    assert payload["tool_attempts"] == 2
    assert payload["tool_failures"] == 1
    assert payload["tool_latency_ms"] == 10
