"""Tests for TraceRecorder."""

from __future__ import annotations

import json
from pathlib import Path

from modi_harness.trace import TraceRecorder
from modi_harness.workspace import WorkspaceManager


def _setup(tmp_path: Path) -> tuple[WorkspaceManager, TraceRecorder]:
    wm = WorkspaceManager(workspace_root=tmp_path / "ws")
    wm.create_run("r1")
    tr = TraceRecorder(
        workspace=wm,
        run_id="r1",
        root_run_id="r1",
        parent_run_id=None,
        thread_id=None,
        redact_keys={"api_key", "authorization", "password", "secret"},
        payload_inline_limit_bytes=2048,
    )
    return wm, tr


def test_record_writes_jsonl_line(tmp_path: Path) -> None:
    _wm, tr = _setup(tmp_path)
    tr.record("run_start", {"agent": "x"})
    trace_path = tmp_path / "ws" / "r1" / "logs" / "trace.jsonl"
    assert trace_path.exists()
    line = json.loads(trace_path.read_text().splitlines()[0])
    assert line["event_type"] == "run_start"
    assert line["payload"]["agent"] == "x"
    assert line["run_id"] == "r1"
    assert line["event_id"]
    assert line["timestamp"]


def test_record_preserves_order(tmp_path: Path) -> None:
    _, tr = _setup(tmp_path)
    for i in range(5):
        tr.record("state_transition", {"step": i})
    events = list(tr.read_trace())
    assert [e["payload"]["step"] for e in events] == [0, 1, 2, 3, 4]


def test_redaction_strips_sensitive_keys(tmp_path: Path) -> None:
    _, tr = _setup(tmp_path)
    tr.record(
        "model_call",
        {
            "api_key": "sk-secret",
            "model_name": "gpt-4o-mini",
            "headers": {"authorization": "Bearer x"},
        },
    )
    events = list(tr.read_trace())
    payload = events[0]["payload"]
    assert payload["api_key"] == "[REDACTED]"
    assert payload["headers"]["authorization"] == "[REDACTED]"
    assert payload["model_name"] == "gpt-4o-mini"


def test_large_payload_offloaded(tmp_path: Path) -> None:
    _, tr = _setup(tmp_path)
    huge = {"blob": "x" * 5000}  # > 2048 limit
    tr.record("tool_result", huge)
    events = list(tr.read_trace())
    ev = events[0]
    assert ev["payload_ref"] is not None
    assert ev["payload"] == {}
    payload_path = tmp_path / "ws" / "r1" / ev["payload_ref"]
    assert payload_path.exists()


def test_read_trace_is_lazy(tmp_path: Path) -> None:
    _, tr = _setup(tmp_path)
    for i in range(3):
        tr.record("state_transition", {"step": i})
    it = tr.read_trace()
    # Iterator, not a list.
    assert iter(it) is it or hasattr(it, "__next__") or hasattr(it, "__iter__")
    materialized = list(it)
    assert len(materialized) == 3


def test_ids_unique_and_monotonic(tmp_path: Path) -> None:
    _, tr = _setup(tmp_path)
    tr.record("state_transition", {"i": 1})
    tr.record("state_transition", {"i": 2})
    events = list(tr.read_trace())
    ids = [e["event_id"] for e in events]
    assert len(set(ids)) == 2
    assert ids == sorted(ids)
