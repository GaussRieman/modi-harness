"""Memory Store exceptions."""

from __future__ import annotations


class MemoryError(Exception):  # noqa: N818
    """Base for memory errors."""


class MemoryNotFoundError(MemoryError):
    pass


class MemoryIdInvalidError(MemoryError):
    pass


class MemoryBodyTooLargeError(MemoryError):
    pass
