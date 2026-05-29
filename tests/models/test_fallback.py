"""Tests for model fallback logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from modi_harness.models import ModelAdapter
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


FALLBACK_CONFIG = {
    "provider": "openai",
    "name": "gpt-4o-mini",
    "api_key": "sk-fallback",
    "base_url": "",
}


class TestFallbackSync:
    def test_fallback_succeeds_after_primary_fails(self) -> None:
        """Primary raises TimeoutError on all retries, fallback returns ok."""
        mock_model = MagicMock()
        mock_model.invoke = MagicMock(side_effect=TimeoutError("timed out"))

        fallback_model = MagicMock()
        fallback_model.invoke = MagicMock(return_value=AIMessage(content="fallback ok"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=FALLBACK_CONFIG,
        )

        with (
            patch("modi_harness.models.adapter.time.sleep"),
            patch("modi_harness.models.adapter.create_chat_model", return_value=fallback_model),
        ):
            result = adapter.call(_pack())

        assert result["fallback_used"] is True
        assert result["message"]["content"] == "fallback ok"
        # Primary should have been called retry_attempts + 1 times
        assert mock_model.invoke.call_count == 3
        # Fallback called once
        assert fallback_model.invoke.call_count == 1

    def test_fallback_also_fails(self) -> None:
        """Both primary and fallback raise TimeoutError. Assert ModelError raised."""
        mock_model = MagicMock()
        mock_model.invoke = MagicMock(side_effect=TimeoutError("primary timeout"))

        fallback_model = MagicMock()
        fallback_model.invoke = MagicMock(side_effect=TimeoutError("fallback timeout"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=FALLBACK_CONFIG,
        )

        with (
            patch("modi_harness.models.adapter.time.sleep"),
            patch("modi_harness.models.adapter.create_chat_model", return_value=fallback_model),
        ):
            with pytest.raises(ModelError) as exc_info:
                adapter.call(_pack())

        assert exc_info.value.code.value == "timeout"

    def test_no_fallback_configured(self) -> None:
        """Primary fails, no fallback config. Assert ModelError raised."""
        mock_model = MagicMock()
        mock_model.invoke = MagicMock(side_effect=TimeoutError("timed out"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=None,
        )

        with patch("modi_harness.models.adapter.time.sleep"):
            with pytest.raises(ModelError) as exc_info:
                adapter.call(_pack())

        assert exc_info.value.code.value == "timeout"

    def test_non_transient_skips_fallback(self) -> None:
        """Primary raises auth error. Assert ModelError raised immediately (no fallback)."""
        mock_model = MagicMock()
        mock_model.invoke = MagicMock(side_effect=PermissionError("401 auth failed"))

        fallback_model = MagicMock()
        fallback_model.invoke = MagicMock(return_value=AIMessage(content="fallback ok"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=FALLBACK_CONFIG,
        )

        with patch("modi_harness.models.adapter.create_chat_model", return_value=fallback_model):
            with pytest.raises(PermissionError, match="401 auth failed"):
                adapter.call(_pack())

        # Fallback should never be called for non-transient errors
        assert fallback_model.invoke.call_count == 0


class TestFallbackAsync:
    @pytest.mark.asyncio
    async def test_fallback_succeeds_after_primary_fails_async(self) -> None:
        """Async: primary raises TimeoutError on all retries, fallback returns ok."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=TimeoutError("timed out"))

        fallback_model = MagicMock()
        fallback_model.ainvoke = AsyncMock(return_value=AIMessage(content="fallback ok"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=FALLBACK_CONFIG,
        )

        with (
            patch("modi_harness.models.adapter.asyncio.sleep", new_callable=AsyncMock),
            patch("modi_harness.models.adapter.create_chat_model", return_value=fallback_model),
        ):
            result = await adapter.acall(_pack())

        assert result["fallback_used"] is True
        assert result["message"]["content"] == "fallback ok"

    @pytest.mark.asyncio
    async def test_fallback_also_fails_async(self) -> None:
        """Async: both primary and fallback fail."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=TimeoutError("primary timeout"))

        fallback_model = MagicMock()
        fallback_model.ainvoke = AsyncMock(side_effect=TimeoutError("fallback timeout"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=FALLBACK_CONFIG,
        )

        with (
            patch("modi_harness.models.adapter.asyncio.sleep", new_callable=AsyncMock),
            patch("modi_harness.models.adapter.create_chat_model", return_value=fallback_model),
        ):
            with pytest.raises(ModelError) as exc_info:
                await adapter.acall(_pack())

        assert exc_info.value.code.value == "timeout"

    @pytest.mark.asyncio
    async def test_no_fallback_configured_async(self) -> None:
        """Async: primary fails, no fallback config."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=TimeoutError("timed out"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=None,
        )

        with patch("modi_harness.models.adapter.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ModelError) as exc_info:
                await adapter.acall(_pack())

        assert exc_info.value.code.value == "timeout"

    @pytest.mark.asyncio
    async def test_non_transient_skips_fallback_async(self) -> None:
        """Async: auth error skips fallback."""
        mock_model = MagicMock()
        mock_model.ainvoke = AsyncMock(side_effect=PermissionError("401 auth failed"))

        fallback_model = MagicMock()
        fallback_model.ainvoke = AsyncMock(return_value=AIMessage(content="fallback ok"))

        adapter = ModelAdapter(
            chat_model=mock_model,
            retry_attempts=2,
            retry_backoff=0.01,
            fallback_config=FALLBACK_CONFIG,
        )

        with patch("modi_harness.models.adapter.create_chat_model", return_value=fallback_model):
            with pytest.raises(PermissionError, match="401 auth failed"):
                await adapter.acall(_pack())

        assert fallback_model.ainvoke.call_count == 0
