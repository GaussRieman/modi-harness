"""Context Manager: build deterministic ContextPack for each model step."""

from __future__ import annotations

from .manager import ContextManager, UNTRUSTED_SYSTEM_NOTE

__all__ = ["ContextManager", "UNTRUSTED_SYSTEM_NOTE"]
