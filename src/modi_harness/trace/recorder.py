"""Trace Recorder implementation.

Writes one JSONL line per ``TraceEvent`` under the run workspace's ``logs/``.
Large payloads are spilled to ``logs/payloads/`` and replaced by a ``payload_ref``.
Sensitive keys (configurable) are redacted in-place before serialization.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from .._utils import canonical_json, new_ulid, now_iso
from ..types import TraceEvent
from ..workspace import WorkspaceManager

_REDACTED = "[REDACTED]"
_LONG_TASK_TRACE_KEYS = frozenset(
    {
        "artifact_refs",
        "attempt_id",
        "base_revision",
        "candidate_refs",
        "cancellation_attempt_ids",
        "child_run_id",
        "component_id",
        "component_ids",
        "confirmation_proof_id",
        "contract_id",
        "criterion_id",
        "dispatch_key",
        "executor_mode",
        "feedback",
        "final",
        "fingerprint",
        "graph_id",
        "graph_revision",
        "group_id",
        "idempotency_key",
        "intent_fingerprint",
        "intent_id",
        "intent_version",
        "lease_epoch",
        "needs_fresh_context",
        "reason",
        "record_id",
        "repair_attempt",
        "received_at",
        "request_id",
        "required_criteria",
        "reuse_proof_ids",
        "status",
        "submission_id",
        "summary",
        "target_ref",
        "task_id",
        "task_revision",
        "trigger",
        "trigger_key",
        "validator_id",
    }
)
_LONG_TASK_PRIVATE_KEYS = frozenset(
    {
        "arguments",
        "artifact_body",
        "body",
        "candidate",
        "confirmation",
        "content",
        "decision",
        "details",
        "new_intent",
        "outcome",
        "output",
        "patch",
        "prompt",
        "response",
        "result",
        "submission_snapshot",
        "transcript",
        "value",
    }
)


class TraceRecorder:
    """Single-writer per run; append-only JSONL."""

    def __init__(
        self,
        *,
        workspace: WorkspaceManager,
        run_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        thread_id: str | None,
        redact_keys: set[str],
        payload_inline_limit_bytes: int,
    ) -> None:
        self._ws = workspace
        self._run_id = run_id
        self._root_run_id = root_run_id
        self._parent_run_id = parent_run_id
        self._thread_id = thread_id
        self._redact_keys = {k.lower() for k in redact_keys}
        self._payload_inline_limit = payload_inline_limit_bytes

    def record(self, event_type: str, payload: dict[str, Any]) -> TraceEvent:
        redacted = _redact(payload, self._redact_keys)
        encoded = canonical_json(redacted)
        payload_ref: str | None = None
        inline_payload: dict[str, Any] = redacted
        if len(encoded) > self._payload_inline_limit:
            payload_ref = self._ws.write_payload(self._run_id, encoded)
            inline_payload = {}

        event: TraceEvent = TraceEvent(
            event_id=new_ulid(),
            run_id=self._run_id,
            root_run_id=self._root_run_id,
            parent_run_id=self._parent_run_id,
            thread_id=self._thread_id,
            timestamp=now_iso(),
            event_type=event_type,
            payload=inline_payload,
            payload_ref=payload_ref,
        )
        line = json.dumps(event, ensure_ascii=False)
        self._ws.append_log(self._run_id, "trace", line)
        return event

    def record_long_task_event(self, event: Any) -> TraceEvent:
        """Record one compact root AuditEvent without prompt or artifact bodies."""

        payload = _compact_long_task_payload(event.payload)
        return self.record(
            str(event.event_type),
            {
                **payload,
                "root_revision": int(event.root_revision),
            },
        )

    def read_trace(self) -> Iterator[TraceEvent]:
        trace_path = self._ws._run_dir(self._run_id) / "logs" / "trace.jsonl"
        if not trace_path.exists():
            return iter(())

        def _gen() -> Iterator[TraceEvent]:
            with trace_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)

        return _gen()


def _redact(value: Any, redact_keys: set[str]) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in redact_keys:
                out[k] = _REDACTED
            else:
                out[k] = _redact(v, redact_keys)
        return out
    if isinstance(value, list):
        return [_redact(v, redact_keys) for v in value]
    return value


def _compact_long_task_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}

    return {
        str(key): _strip_long_task_private_values(child)
        for key, child in value.items()
        if str(key).casefold() in _LONG_TASK_TRACE_KEYS
    }


def _strip_long_task_private_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_long_task_private_values(child)
            for key, child in value.items()
            if str(key).casefold() not in _LONG_TASK_PRIVATE_KEYS
        }
    if isinstance(value, list | tuple):
        return [_strip_long_task_private_values(child) for child in value[:50]]
    if isinstance(value, str):
        return value if len(value) <= 500 else f"{value[:497]}..."
    return value
