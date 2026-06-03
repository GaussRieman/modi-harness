"""Cross-cutting exceptions for V0.5 ModiHarness/ModiSession API.

Module-local exceptions (e.g., AgentFrontmatterError) stay in their respective
<module>/errors.py.
"""

from __future__ import annotations


class AgentNameConflict(Exception):
    """Raised by ModiSession.__init__ when two non-equal ModiAgent objects share a name."""

    def __init__(self, name: str, detail: str = "") -> None:
        self.agent_name = name
        msg = f"agent name conflict: '{name}'"
        if detail:
            msg = f"{msg} — {detail}"
        super().__init__(msg)


class AgentNotRegistered(Exception):
    """Raised by session.run_task when the requested agent is not a top-level entry."""

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        self.agent_name = name
        msg = f"agent '{name}' is not registered as a top-level (runnable) agent"
        if available:
            msg = f"{msg}; available: {', '.join(sorted(available))}"
        super().__init__(msg)


class ModiSessionConfigError(Exception):
    """Raised by ModiSession.__init__ on infra construction failure."""


__all__ = ["AgentNameConflict", "AgentNotRegistered", "ModiSessionConfigError"]
