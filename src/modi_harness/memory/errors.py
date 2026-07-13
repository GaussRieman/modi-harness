"""Memory Store exceptions."""

from __future__ import annotations


class MemoryError(Exception):
    """Base for memory errors."""


class MemoryNotFoundError(MemoryError):
    pass


class MemoryIdInvalidError(MemoryError):
    pass


class MemoryBodyTooLargeError(MemoryError):
    pass
