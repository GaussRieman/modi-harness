from __future__ import annotations

from typing import Any


def stable_trace_contract(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the stable trace surface used by golden regression tests."""
    event_types = [str(event.get("event_type") or "") for event in events]
    by_type = _by_type(events)
    stage_labels = _stage_labels(by_type.get("intent_initialized", []))
    model_results = by_type.get("model_result", [])
    tool_results = by_type.get("tool_result", [])
    run_end_payload = _payload(by_type.get("run_end", [])[-1]) if by_type.get("run_end") else {}

    return {
        "event_types": event_types,
        "steps": {
            "model": _step_ids(model_results),
            "tool": [
                {
                    "step_id": payload.get("step_id", ""),
                    "parent_step_id": payload.get("parent_step_id", ""),
                    "tool_name": payload.get("tool_name", ""),
                    "outcome": payload.get("outcome") or payload.get("status", ""),
                    "error_code": payload.get("error_code"),
                    "attempt": payload.get("attempt", 0),
                    "attempts": _attempt_count(payload),
                }
                for payload in (_payload(event) for event in tool_results)
            ],
            "validation": _step_ids(by_type.get("output_validation", [])),
            "output": _step_ids(by_type.get("output_submitted", [])),
            "run_end": _step_ids(by_type.get("run_end", [])),
        },
        "intent": {
            "initialized": _pick_first_payload(
                by_type.get("intent_initialized", []),
                ["stage"],
            ),
            "clarity": _pick_first_payload(
                by_type.get("intent_clarity_estimated", []),
                ["level"],
            ),
            "autonomy": _pick_first_payload(
                by_type.get("autonomy_scope_derived", []),
                ["mode"],
            ),
        },
        "actions": [
            _with_stage_label(
                _pick_payload(event, ["kind", "tool_name", "intent_version", "stage_id"]),
                stage_labels,
            )
            for event in by_type.get("action_proposed", [])
        ],
        "alignment": [
            _with_stage_label(
                _pick_payload(
                    event,
                    ["decision", "intent_version", "stage_id", "model_judged"],
                ),
                stage_labels,
            )
            for event in by_type.get("alignment_decision", [])
        ],
        "lineage": [
            _with_stage_label(
                _pick_payload(
                    event,
                    ["intent_version", "stage_id", "judgment_id"],
                ),
                stage_labels,
            )
            for event in by_type.get("intent_lineage_recorded", [])
        ],
        "output": _with_stage_label(
            _pick_first_payload(
                by_type.get("output_submitted", []),
                [
                    "step_id",
                    "validation_step_id",
                    "source",
                    "status",
                    "schema_valid",
                    "output_keys",
                    "intent_version",
                    "stage_id",
                ],
            ),
            stage_labels,
        ),
        "run_end": {
            "step_id": run_end_payload.get("step_id", ""),
            "previous_step_id": run_end_payload.get("previous_step_id", ""),
            "model_calls": run_end_payload.get("model_calls", 0),
            "model_usage_total_tokens_min": _at_least(
                run_end_payload.get("model_usage", {}).get("total_tokens", 0)
                if isinstance(run_end_payload.get("model_usage"), dict)
                else 0
            ),
            "model_latency_ms_min": _at_least(run_end_payload.get("model_latency_ms", 0)),
            "tool_attempts": run_end_payload.get("tool_attempts", 0),
            "tool_failures": run_end_payload.get("tool_failures", 0),
            "tool_latency_ms_min": _at_least(run_end_payload.get("tool_latency_ms", 0)),
        },
    }


def _by_type(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("event_type") or ""), []).append(event)
    return grouped


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _pick_payload(event: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    payload = _payload(event)
    return {key: payload.get(key) for key in keys}


def _stage_labels(intent_events: list[dict[str, Any]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for event in intent_events:
        payload = _payload(event)
        stage_id = str(payload.get("stage_id") or "")
        stage = str(payload.get("stage") or "")
        if stage_id and stage:
            labels[stage_id] = stage
    return labels


def _with_stage_label(payload: dict[str, Any], stage_labels: dict[str, str]) -> dict[str, Any]:
    stage_id = str(payload.pop("stage_id", "") or "")
    if stage_id:
        payload["stage"] = stage_labels.get(stage_id, "<stage-id>")
    return payload


def _pick_first_payload(events: list[dict[str, Any]], keys: list[str]) -> dict[str, Any]:
    if not events:
        return {}
    return _pick_payload(events[0], keys)


def _step_ids(events: list[dict[str, Any]]) -> list[str]:
    return [str(_payload(event).get("step_id") or "") for event in events]


def _attempt_count(payload: dict[str, Any]) -> int:
    attempts = payload.get("attempts")
    if isinstance(attempts, list):
        return len(attempts)
    return 0


def _at_least(value: Any) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        number = 0
    return "<0" if number < 0 else ">=0"
