"""Architecture marker test: tool calls normalize into ActionProposal.

Fails until plan N4 introduces action normalization. The model must not send an
opaque tool call straight into policy; the runtime normalizes each proposed
action and attaches an alignment-relevant impact first.
"""
from __future__ import annotations


def test_tool_call_normalizes_to_action_proposal() -> None:
    from modi_harness.actions import ActionProposal  # noqa: F401
    from typing import get_type_hints

    hints = get_type_hints(ActionProposal)
    required = {
        "id",
        "kind",
        "summary",
        "tool_name",
        "arguments",
        "intent_version",
        "stage_id",
        "impact",
    }
    assert required <= set(hints), f"missing proposal fields: {required - set(hints)}"
