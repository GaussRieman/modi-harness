"""Tool Gateway exceptions."""

from __future__ import annotations


class ToolError(Exception):
    """Base for tool gateway errors."""


class ToolUnknownError(ToolError):
    pass


class ToolSchemaError(ToolError):
    pass
