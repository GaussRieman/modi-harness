"""Tool Gateway: only execution path for model-requested tools."""

from __future__ import annotations

from .builtin import BUILTIN_TOOL_NAMES, get_builtin_specs
from .errors import ToolError, ToolSchemaError, ToolUnknownError
from .gateway import ToolDispatchResult, ToolGateway
from .registry import ToolRegistry

__all__ = [
    "BUILTIN_TOOL_NAMES",
    "ToolDispatchResult",
    "ToolError",
    "ToolGateway",
    "ToolRegistry",
    "ToolSchemaError",
    "ToolUnknownError",
    "get_builtin_specs",
]
