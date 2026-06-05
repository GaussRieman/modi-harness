"""Derive the agent's first user message from a run_task input payload.

This is the single source of truth for the ``ModiSession.run_task`` /
``stream`` / ``astream`` input contract. See
docs/architecture/08-harness-api.md for the documented precedence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Scalar keys checked in order after the ``messages`` list. The first key
# present in the payload wins. ``prompt`` was added when the contract was
# formalized; the rest predate it.
_TEXT_KEYS = ("prompt", "customer_message", "question", "goal")


def task_input_to_text(payload: Mapping[str, Any]) -> str:
    """Return the text used as the agent's first user message.

    Precedence: ``messages`` (content of the last ``role == "user"`` item)
    > ``prompt`` > ``customer_message`` > ``question`` > ``goal`` >
    ``str(payload)`` fallback. If ``messages`` is present but contains no
    user item, evaluation continues to the scalar keys. A user item whose
    ``content`` is missing or ``None`` resolves to an empty string.
    """
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                return "" if content is None else str(content)
    for key in _TEXT_KEYS:
        if key in payload:
            return str(payload[key])
    return str(payload)
