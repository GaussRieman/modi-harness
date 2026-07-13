"""Trusted Operations available inside Research Assistant autonomous Nodes."""

from .research import (
    FETCH_URL_SPEC,
    GENERATE_RESEARCH_DIGEST_SPEC,
    JUDGE_RESEARCH_DIGEST_SPEC,
    SOURCE_EXTRACT_SPEC,
    fetch_url,
    generate_research_digest,
    judge_research_digest,
    source_extract,
)

__all__ = [
    "FETCH_URL_SPEC",
    "GENERATE_RESEARCH_DIGEST_SPEC",
    "JUDGE_RESEARCH_DIGEST_SPEC",
    "SOURCE_EXTRACT_SPEC",
    "fetch_url",
    "generate_research_digest",
    "judge_research_digest",
    "source_extract",
]
