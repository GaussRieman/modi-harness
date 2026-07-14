"""Trusted Operations available inside Research Assistant autonomous Nodes."""

from .research import (
    PUBLIC_WEB_RESEARCH_SPEC,
    PUBLIC_WEB_SEARCH_SPEC,
    REJECT_RESEARCH_REQUEST_SPEC,
    public_web_research,
    public_web_search,
    reject_research_request,
)

__all__ = [
    "PUBLIC_WEB_RESEARCH_SPEC",
    "PUBLIC_WEB_SEARCH_SPEC",
    "REJECT_RESEARCH_REQUEST_SPEC",
    "public_web_research",
    "public_web_search",
    "reject_research_request",
]
