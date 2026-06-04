"""Sample plugin fixture for the V0.5 discovery flow.

Returns a self-contained manifest: a list of ModiAgent objects + a list of
ToolBinding kernel tools. modi never scans the plugin's filesystem.
"""

from __future__ import annotations

from typing import Any

from modi_harness import ModiAgent
from modi_harness.types import ToolBinding

SAMPLE_TOOL_SPEC: dict[str, Any] = {
    "name": "sample_tool",
    "description": "Sample tool from plugin",
    "input_schema": {"type": "object", "properties": {}},
    "risk_level": "L1",
}


def sample_tool_handler(**_: Any) -> dict[str, str]:
    return {"result": "ok"}


def get_plugin() -> dict[str, Any]:
    return {
        "name": "sample-plugin",
        "agents": [
            ModiAgent(
                name="sample-agent",
                description="Sample agent from plugin",
                instruction="reply",
            )
        ],
        "kernel_tools": [ToolBinding(spec=SAMPLE_TOOL_SPEC, handler=sample_tool_handler)],
    }
