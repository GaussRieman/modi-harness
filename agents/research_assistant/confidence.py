"""Pure discrete confidence scoring for recorded research findings.

No I/O and no Harness/Runtime dependency. Six factors, each discretized to
``high | medium | low``, combined by taking the lowest of the six (ordinal
``min``): one bad factor caps the whole claim regardless of the other five.

This module is deliberately simple (v1): confidence is derived only from the
evidence tags a `verify_claim_evidence` call already produced and the task's
`verification_method`, both already validated elsewhere. It performs no
network or Harness state access so it can be unit tested in isolation.
"""

from __future__ import annotations

import datetime as _datetime
import re as _re
from collections.abc import Mapping, Sequence
from typing import Any

LEVELS = ("low", "medium", "high")
_ORDER = {level: index for index, level in enumerate(LEVELS)}

VERIFICATION_METHODS = (
    "single_source_sufficient",
    "dual_independent_required",
    "official_primary_required",
    "contradiction_sensitive",
    "unverifiable_flag",
)

_SOURCE_TYPE_LEVEL = {
    "official": "high",
    "primary": "high",
    "reputable_media": "medium",
    "industry_report": "medium",
    "job_board": "low",
    "secondary": "low",
}

_COVERAGE_GAP_MESSAGES = {
    "single_source_sufficient": (
        "verification_method single_source_sufficient requires at least one "
        "supporting source, but none was recorded"
    ),
    "dual_independent_required": (
        "verification_method dual_independent_required expects at least two "
        "independent supporting sources"
    ),
    "official_primary_required": (
        "verification_method official_primary_required expects official or "
        "primary supporting sources"
    ),
    "contradiction_sensitive": (
        "verification_method contradiction_sensitive expects at least two "
        "independent supporting sources after actively searching for "
        "contradicting evidence"
    ),
}


def _level(value: str) -> str:
    return value if value in _ORDER else "low"


def combine(factors: Mapping[str, str]) -> str:
    """Overall confidence is the lowest of the given factor levels."""

    if not factors:
        return "low"
    return min((_level(value) for value in factors.values()), key=lambda item: _ORDER[item])


def score_finding(
    evidence: Sequence[Mapping[str, Any]],
    verification_method: str,
    *,
    today: _datetime.date | None = None,
) -> dict[str, str]:
    """Compute the six discrete factors plus the combined confidence."""

    supporting = [item for item in evidence if item.get("stance") == "supporting"]
    contradicting = [item for item in evidence if item.get("stance") == "contradicting"]
    factors = {
        "source_quality": _source_quality_factor(supporting),
        "source_independence": _independence_factor(supporting),
        "directness": _directness_factor(supporting),
        "recency": _recency_factor(supporting, today=today),
        "consistency": _consistency_factor(supporting, contradicting),
        "coverage": _coverage_factor(supporting, verification_method),
    }
    factors["overall"] = combine(factors)
    return factors


def coverage_gap_message(
    evidence: Sequence[Mapping[str, Any]],
    verification_method: str,
) -> str | None:
    """A human-readable gap description, or None when coverage is fully met."""

    supporting = [item for item in evidence if item.get("stance") == "supporting"]
    if _coverage_factor(supporting, verification_method) == "high":
        return None
    return _COVERAGE_GAP_MESSAGES.get(verification_method)


def _source_quality_factor(supporting: Sequence[Mapping[str, Any]]) -> str:
    if not supporting:
        return "low"
    levels = [
        _SOURCE_TYPE_LEVEL.get(str(item.get("source_type") or "").lower(), "low")
        for item in supporting
    ]
    return min(levels, key=lambda level: _ORDER[level])


def _independence_factor(supporting: Sequence[Mapping[str, Any]]) -> str:
    independent_count = sum(
        1 for item in supporting if item.get("independence") == "independent"
    )
    if independent_count >= 2:
        return "high"
    if independent_count == 1:
        return "medium"
    return "low"


def _directness_factor(supporting: Sequence[Mapping[str, Any]]) -> str:
    if not supporting:
        return "low"
    levels = [
        "high" if item.get("directness") == "direct" else "medium" for item in supporting
    ]
    return min(levels, key=lambda level: _ORDER[level])


def _recency_factor(
    supporting: Sequence[Mapping[str, Any]],
    *,
    today: _datetime.date | None,
) -> str:
    if not supporting:
        return "low"
    reference = today or _datetime.date.today()
    levels: list[str] = []
    for item in supporting:
        parsed = _parse_as_of(str(item.get("as_of") or ""))
        if parsed is None:
            levels.append("low")
            continue
        age_days = (reference - parsed).days
        if age_days <= 90:
            levels.append("high")
        elif age_days <= 365:
            levels.append("medium")
        else:
            levels.append("low")
    return min(levels, key=lambda level: _ORDER[level])


def _consistency_factor(
    supporting: Sequence[Mapping[str, Any]],
    contradicting: Sequence[Mapping[str, Any]],
) -> str:
    if not contradicting:
        return "high"
    if len(supporting) > len(contradicting):
        return "medium"
    return "low"


def _coverage_factor(
    supporting: Sequence[Mapping[str, Any]],
    verification_method: str,
) -> str:
    independent_count = sum(
        1 for item in supporting if item.get("independence") == "independent"
    )
    official_primary_count = sum(
        1
        for item in supporting
        if str(item.get("source_type") or "").lower() in {"official", "primary"}
    )
    if verification_method == "single_source_sufficient":
        return "high" if supporting else "low"
    if verification_method in {"dual_independent_required", "contradiction_sensitive"}:
        if independent_count >= 2:
            return "high"
        if independent_count == 1:
            return "medium"
        return "low"
    if verification_method == "official_primary_required":
        if not supporting:
            return "low"
        if official_primary_count == len(supporting):
            return "high"
        if official_primary_count:
            return "medium"
        return "low"
    # unverifiable_flag findings are short-circuited to blocked before any
    # evidence is gathered; a defensive default keeps this function total.
    return "low"


_AS_OF_PATTERNS = (
    (_re.compile(r"^\d{4}-\d{2}-\d{2}$"), "day"),
    (_re.compile(r"^\d{4}-\d{2}$"), "month"),
    (_re.compile(r"^\d{4}$"), "year"),
)


def _parse_as_of(value: str) -> _datetime.date | None:
    text = value.strip()
    if not text:
        return None
    for pattern, granularity in _AS_OF_PATTERNS:
        if not pattern.match(text):
            continue
        if granularity == "year":
            return _datetime.date(int(text), 7, 1)
        if granularity == "month":
            year, month = (int(part) for part in text.split("-"))
            return _datetime.date(year, month, 15)
        return _datetime.date.fromisoformat(text)
    return None


__all__ = [
    "LEVELS",
    "VERIFICATION_METHODS",
    "combine",
    "coverage_gap_message",
    "score_finding",
]
