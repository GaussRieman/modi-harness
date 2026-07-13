"""Memory admission and authority classification."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, cast

from ..types import MemoryCandidate, MemoryRecord, SelectedMemory


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
        selected.append(
            SelectedMemory(
                record=record,
                authority=authority,
                score=candidate["score"],
                reasons=list(candidate["reasons"]),
            )
        )
    return selected


def annotate_selected(selected: SelectedMemory) -> MemoryRecord:
    record = dict(selected["record"])
    metadata = dict(selected["record"]["metadata"])
    metadata["authority"] = selected["authority"]
    metadata["selection_score"] = selected["score"]
    metadata["selection_reasons"] = list(selected["reasons"])
    record["metadata"] = metadata
    return cast(MemoryRecord, record)


def _authority_for(record: MemoryRecord) -> Literal["trusted", "context"]:
    metadata = record.get("metadata") or {}
    if metadata.get("authority") in ("trusted", "context"):
        return cast(Literal["trusted", "context"], metadata["authority"])
    if record.get("type") == "feedback":
        return "trusted"
    if record.get("type") == "project" and metadata.get("approved") is True:
        return "trusted"
    return "context"
