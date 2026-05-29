"""Hook System: load hook config, dispatch events, capture results."""

from __future__ import annotations

from .dispatcher import HookDispatcher
from .registry import HookRegistry

__all__ = ["HookDispatcher", "HookRegistry"]
