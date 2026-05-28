"""Agent Loader exception types."""

from __future__ import annotations


class AgentLoaderError(Exception):
    """Base class for Agent Loader errors."""


class AgentNotFoundError(AgentLoaderError):
    pass


class AgentFrontmatterError(AgentLoaderError):
    pass


class AgentDuplicateError(AgentLoaderError):
    """Same agent name resolved from more than one source."""
