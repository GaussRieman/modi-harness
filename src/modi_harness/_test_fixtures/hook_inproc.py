"""In-process hook callables used by hooks dispatcher tests.

Lives under src so that ``python:modi_harness._test_fixtures.hook_inproc.X``
targets resolve through the regular package path.
"""

from __future__ import annotations

from typing import Any


def hook_proceed(payload: dict[str, Any]) -> dict[str, Any]:
    return {"decision": "proceed", "feedback": None}


def hook_block(payload: dict[str, Any]) -> dict[str, Any]:
    return {"decision": "block", "feedback": "blocked by test"}


def hook_redirect(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": "redirect",
        "redirect": {"new_arg": payload.get("tool_name", "x")},
    }


def hook_raises(payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("hook crash")
