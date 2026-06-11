"""Memory admission and authority classification."""

from __future__ import annotations

from typing import Iterable

from ..types import MemoryCandidate, SelectedMemory


def admit_candidates(candidates: Iterable[MemoryCandidate]) -> list[SelectedMemory]:
    selected: list[SelectedMemory] = []
    for candidate in candidates:
        record = candidate["record"]
        confidence = (record.get("metadata") or {}).get("confidence", 1.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 1.0
        if confidence_value < 0.2:
            continue

        authority = _authority_for(record)
        selected.append({
            "record": record,
            "authority": authority,
            "score": candidate["score"],
            "reasons": list(candidate["reasons"]),
        })
    return selected


def annotate_selected(selected: SelectedMemory) -> dict:
    record = dict(selected["record"])
    metadata = dict(record.get("metadata") or {})
    metadata["authority"] = selected["authority"]
    metadata["selection_score"] = selected["score"]
    metadata["selection_reasons"] = list(selected["reasons"])
    record["metadata"] = metadata
    return record


def _authority_for(record: dict) -> str:
    metadata = record.get("metadata") or {}
    if metadata.get("authority") in ("trusted", "context"):
        return metadata["authority"]
    if record.get("type") == "feedback":
        return "trusted"
    if record.get("type") == "project" and metadata.get("approved") is True:
        return "trusted"
    return "context"
