"""Tests for retry logic, prompt cache marking, and cache_write_tokens extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from modi_harness.models import ModelAdapter
from modi_harness.models.adapter import _extract_usage
from modi_harness.models.errors import ModelError


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


# -----------------------------------------------------------------------
# T1: Retry on transient errors
# -----------------------------------------------------------------------


class TestRetryOnTimeout:
    def test_retry_on_timeout(self) -> None:
        """Mock model raises TimeoutError on first invoke, returns AIMessage on second."""
        mock_model = MagicMock()
        mock_model.invoke = MagicMock(
            side_effect=[TimeoutError("timed out"), AIMessage(content="ok")]
        )

        adapter = ModelAdapter(chat_model=mock_model, retry_attempts=2, retry_backoff=0.01)
        with patch("modi_harness.models.adapter.time.sleep"):
            result = adapter.call(_pack())

        assert result["message"]["content"] == "ok"
        assert mock_model.invoke.call_count == 2

    def test_no_retry_on_auth_error(self) -> None:
        """PermissionError should not be retried."""
        mock_model = MagicMock()
        mock_model.invoke = MagicMock(side_effect=PermissionError("auth failed"))

        adapter = ModelAdapter(chat_model=mock_model, retry_attempts=2, retry_backoff=0.01)
        with pytest.raises(PermissionError, match="auth failed"):
            adapter.call(_pack())

        assert mock_model.invoke.call_count == 1

    def test_retry_exhausted(self) -> None:
        """After retry_attempts + 1 total calls, raises ModelError wrapping the last exception."""
        mock_model = MagicMock()
        mock_model.invoke = MagicMock(side_effect=TimeoutError("always fails"))

        adapter = ModelAdapter(chat_model=mock_model, retry_attempts=2, retry_backoff=0.01)
        with patch("modi_harness.models.adapter.time.sleep"):
            with pytest.raises(ModelError) as exc_info:
                adapter.call(_pack())

        assert exc_info.value.code.value == "timeout"
        # 1 initial + 2 retries = 3 total
        assert mock_model.invoke.call_count == 3


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_retry_on_timeout_async(self) -> None:
        """Async retry on TimeoutError."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(
            side_effect=[TimeoutError("timed out"), AIMessage(content="ok")]
        )

        adapter = ModelAdapter(chat_model=mock_model, retry_attempts=2, retry_backoff=0.01)
        with patch("modi_harness.models.adapter.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter.acall(_pack())

        assert result["message"]["content"] == "ok"
        assert mock_model.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_connection_error_async(self) -> None:
        """Async retry on ConnectionError."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(
            side_effect=[ConnectionError("reset"), AIMessage(content="recovered")]
        )

        adapter = ModelAdapter(chat_model=mock_model, retry_attempts=2, retry_backoff=0.01)
        with patch("modi_harness.models.adapter.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter.acall(_pack())

        assert result["message"]["content"] == "recovered"

    @pytest.mark.asyncio
    async def test_retry_on_429_error_async(self) -> None:
        """Async retry on rate-limit (429) error."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(
            side_effect=[Exception("HTTP 429 Too Many Requests"), AIMessage(content="ok")]
        )

        adapter = ModelAdapter(chat_model=mock_model, retry_attempts=2, retry_backoff=0.01)
        with patch("modi_harness.models.adapter.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter.acall(_pack())

        assert result["message"]["content"] == "ok"

    @pytest.mark.asyncio
    async def test_retry_on_500_error_async(self) -> None:
        """Async retry on server error (5xx)."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(
            side_effect=[Exception("HTTP 502 Bad Gateway"), AIMessage(content="ok")]
        )

        adapter = ModelAdapter(chat_model=mock_model, retry_attempts=2, retry_backoff=0.01)
        with patch("modi_harness.models.adapter.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter.acall(_pack())

        assert result["message"]["content"] == "ok"


# -----------------------------------------------------------------------
# T2: Prompt cache marking
# -----------------------------------------------------------------------


class TestCacheControl:
    def test_cache_control_on_system_message(self) -> None:
        """First SystemMessage should have cache_control in additional_kwargs."""
        adapter = ModelAdapter()
        messages = adapter.to_langchain_messages(_pack())
        first_system = messages[0]
        assert first_system.additional_kwargs.get("cache_control") == {"type": "ephemeral"}

    def test_no_cache_control_on_output_requirement(self) -> None:
        """The output_requirement SystemMessage should NOT have cache_control."""
        pack = _pack()
        pack["output_requirement"] = {"type": "object"}
        adapter = ModelAdapter()
        messages = adapter.to_langchain_messages(pack)
        # Last message is the output_requirement SystemMessage
        last_msg = messages[-1]
        assert "cache_control" not in last_msg.additional_kwargs


# -----------------------------------------------------------------------
# T3: Extract cache_write_tokens
# -----------------------------------------------------------------------


class TestCacheWriteTokens:
    def test_cache_write_tokens_extracted(self) -> None:
        """_extract_usage reads cache_creation from input_token_details."""
        msg = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "input_token_details": {"cache_creation": 42, "cache_read": 10},
            },
        )
        usage = _extract_usage(msg)
        assert usage["cache_write_tokens"] == 42
        assert usage["cache_read_tokens"] == 10

    def test_cache_write_tokens_zero_when_missing(self) -> None:
        """cache_write_tokens defaults to 0 when input_token_details is absent."""
        msg = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        )
        usage = _extract_usage(msg)
        assert usage["cache_write_tokens"] == 0
        assert usage["cache_read_tokens"] == 0
