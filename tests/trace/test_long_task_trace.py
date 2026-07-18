"""Compact trace projections for durable long-Task AuditEvents."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from modi_harness.long_task import AuditEvent
from modi_harness.trace import TraceRecorder
from modi_harness.workflow.session import _long_task_trace_events
from modi_harness.workspace import WorkspaceManager


def _recorder(tmp_path: Path) -> TraceRecorder:
    workspace = WorkspaceManager(workspace_root=tmp_path / "workspace")
    workspace.create_run("root-1")
    return TraceRecorder(
        workspace=workspace,
        run_id="root-1",
        root_run_id="root-1",
        parent_run_id=None,
        thread_id="thread-1",
        redact_keys=set(),
        payload_inline_limit_bytes=2048,
    )


def _event(event_id: str, revision: int) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        event_type="task_completed",
        root_revision=revision,
        payload={
            "graph_id": "graph-1",
            "task_id": "task-1",
            "status": "completed",
            "artifact_refs": ["workspace://root-1/result.json"],
            "root_revision": 999,
            "response": {"comment": "private human response"},
            "new_intent": {"goal": "private replacement intent"},
            "patch": {"changes": [{"value": "private patch body"}]},
            "prompt": "private prompt",
            "transcript": [{"content": "private transcript"}],
            "candidate": {
                "body": "private artifact body",
                "result": {"secret": "private result"},
                "safe_ref": "artifact-1",
            },
        },
    )


def test_recorder_writes_compact_root_revision_event(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)

    recorded = recorder.record_long_task_event(_event("event-1", 17))

    assert recorded["event_type"] == "task_completed"
    assert recorded["payload"] == {
        "root_revision": 17,
        "graph_id": "graph-1",
        "task_id": "task-1",
        "status": "completed",
        "artifact_refs": ["workspace://root-1/result.json"],
    }
    assert "private" not in str(recorded)
    assert recorded["payload_ref"] is None
    assert list(recorder.read_trace()) == [recorded]


def test_workflow_projection_emits_only_new_compact_audit_events() -> None:
    first = _event("event-1", 16)
    second = _event("event-2", 17)
    previous = SimpleNamespace(events=(first,))
    current = SimpleNamespace(events=(first, second))

    projected = _long_task_trace_events(previous, current)

    assert projected == [
        (
            "task_completed",
            {
                "root_revision": 17,
                "graph_id": "graph-1",
                "task_id": "task-1",
                "status": "completed",
                "artifact_refs": ["workspace://root-1/result.json"],
            },
        )
    ]
    assert _long_task_trace_events(current, current) == []
