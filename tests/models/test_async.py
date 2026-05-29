"""Async tests for ModelAdapter.acall and astream."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from modi_harness.models import ModelAdapter


def _pack() -> dict:
    return {
        "system_instruction": "sys",
        "agent_instruction": "agent",
        "skill_instructions": [],
        "memory_blocks": [],
        "references": [],
        "state_summary": "",
        "tool_descriptions": [],
        "workspace_index": [],
        "recent_messages": [],
        "output_requirement": None,
        "trust_annotations": [],
        "context_hash": "abc",
    }


@pytest.mark.asyncio
async def test_acall_returns_model_result() -> None:
    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=AIMessage(content="hi"))
    # bind_tools not needed when tool_descriptions is empty — adapter returns model as-is
    adapter = ModelAdapter(chat_model=mock_model)
    result = await adapter.acall(_pack())
    assert result["message"]["role"] == "assistant"
    assert result["message"]["content"] == "hi"
    mock_model.ainvoke.assert_awaited_once()
