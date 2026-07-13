"""Explainable local memory retrieval."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from ..types import MemoryCandidate, MemoryRecord


def rank_records(
    records: Iterable[MemoryRecord],
    *,
    query: str | None = None,
    types: set[str] | None = None,
    tags: set[str] | None = None,
) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    q = (query or "").strip().lower()
    for record in records:
        score = 0.0
        reasons: list[str] = []
        signals: dict[str, float] = {}

        if types is not None:
            if record["type"] not in types:
                continue
            score += 1.0
            signals["type"] = 1.0
            reasons.append(f"type:{record['type']}")

        if tags is not None:
            matched = sorted(set(record["tags"]) & tags)
            if not matched:
                continue
            tag_score = float(len(matched))
            score += tag_score
            signals["tag"] = tag_score
            reasons.extend(f"tag:{tag}" for tag in matched)

        if q:
            query_score = 0.0
            if q in record["name"].lower():
                query_score += 3.0
                reasons.append("query:name")
            if q in record["description"].lower():
                query_score += 2.0
                reasons.append("query:description")
            if q in record["body"].lower():
                query_score += 1.0
                reasons.append("query:body")
            if query_score <= 0:
                continue
            score += query_score
            signals["query"] = query_score

        recency = _recency_signal(record)
        if recency > 0:
            score += recency
            signals["recency"] = recency
            reasons.append("recency")

        if not reasons:
            reasons.append("scope")

        candidates.append(
            {
                "record": record,
                "score": score,
                "reasons": reasons,
                "signals": signals,
            }
        )

    return sorted(
        candidates,
        key=lambda c: (
            -c["score"],
            _timestamp_sort_key(c["record"]),
            c["record"]["scope"],
            c["record"]["id"],
        ),
    )


def _recency_signal(record: MemoryRecord) -> float:
    stamp = _parse_iso(record.get("updated_at") or record.get("created_at") or "")
    if stamp is None:
        return 0.0
    # Tiny stable signal: enough to break ties without dominating relevance.
    return max(0.0, min(0.999, stamp.timestamp() / 4_000_000_000))


def _timestamp_sort_key(record: MemoryRecord) -> float:
    stamp = _parse_iso(record.get("updated_at") or record.get("created_at") or "")
    return -(stamp.timestamp() if stamp is not None else 0.0)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
