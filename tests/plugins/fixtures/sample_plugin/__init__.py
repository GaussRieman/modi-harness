"""Sample plugin used as a test fixture for the V0.4c discovery flow.

Mimics the layout an external plugin package would ship: a top-level
``get_plugin`` callable returning the plugin manifest dict, plus an
``agents/`` directory and a ``skills/`` directory with a single sample item
in each. Tests inject this package via a fake entry point to exercise
``discover_plugins`` end-to-end without publishing a real distribution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).parent

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
        "agents_dir": _PKG_ROOT / "agents",
        "skills_dir": _PKG_ROOT / "skills",
        "tools": [(SAMPLE_TOOL_SPEC, sample_tool_handler)],
    }
