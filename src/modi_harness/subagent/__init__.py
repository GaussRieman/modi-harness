"""Subagent Runtime entry points."""

from __future__ import annotations

from .dispatcher import SubagentError, dispatch_subagent

__all__ = ["SubagentError", "dispatch_subagent"]
