"""Agent Loader: turn a Markdown agent file into an AgentProfile or ModiAgent."""

from __future__ import annotations

from .errors import AgentDuplicateError, AgentFrontmatterError, AgentNotFoundError
from .loader import (
    SUBMIT_OUTPUT_TOOL_NAME,
    AgentLoader,
    load_agent_object,
)

__all__ = [
    "SUBMIT_OUTPUT_TOOL_NAME",
    "AgentDuplicateError",
    "AgentFrontmatterError",
    "AgentLoader",
    "AgentNotFoundError",
    "load_agent_object",
]
