"""Cross-cutting exceptions for V0.5 ModiHarness/ModiSession API.

Module-local exceptions (e.g., AgentFrontmatterError) stay in their respective
<module>/errors.py.
"""

from __future__ import annotations

from pathlib import Path


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


class ModiConfigError(Exception):
    """Raised when project discovery configuration is invalid."""

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"invalid Modi config at {path}: {detail}")


class AgentFactoryError(Exception):
    """Raised when a trusted project Agent factory cannot be loaded."""

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"invalid Agent package at {path}: {detail}")


class AgentResolutionError(Exception):
    """Raised when a discovery query is missing, ambiguous, or invalid."""

    def __init__(
        self,
        query: str,
        candidates: tuple[str, ...],
        *,
        available: list[str] | None = None,
        detail: str | None = None,
    ) -> None:
        self.query = query
        self.candidates = candidates
        self.available = tuple(available or ())
        if detail:
            message = f"cannot resolve Agent {query!r}: {detail}"
        elif candidates:
            message = f"Agent {query!r} is ambiguous: {', '.join(candidates)}"
        else:
            message = f"Agent {query!r} was not found"
            if self.available:
                message += f"; available: {', '.join(self.available)}"
        super().__init__(message)


__all__ = [
    "AgentFactoryError",
    "AgentNameConflict",
    "AgentNotRegistered",
    "AgentResolutionError",
    "ModiConfigError",
    "ModiSessionConfigError",
]
