"""Trusted Operations available inside Research Assistant autonomous Nodes."""

from .research import (
    FETCH_URL_SPEC,
    GENERATE_RESEARCH_DIGEST_SPEC,
    JUDGE_RESEARCH_DIGEST_SPEC,
    WEB_SEARCH_SPEC,
    fetch_url,
    generate_research_digest,
    judge_research_digest,
    web_search,
)

__all__ = [
    "FETCH_URL_SPEC",
    "GENERATE_RESEARCH_DIGEST_SPEC",
    "JUDGE_RESEARCH_DIGEST_SPEC",
    "WEB_SEARCH_SPEC",
    "fetch_url",
    "generate_research_digest",
    "judge_research_digest",
    "web_search",
]
