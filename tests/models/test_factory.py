"""Tests for create_chat_model factory."""

from __future__ import annotations

import pytest

from modi_harness.models.errors import ModelConfigError
from modi_harness.models.factory import create_chat_model


def test_openai_constructs_ok() -> None:
    model = create_chat_model(provider="openai", name="gpt-4o", api_key="sk-test")
    from langchain_openai import ChatOpenAI

    assert isinstance(model, ChatOpenAI)


def test_anthropic_constructs_ok() -> None:
    model = create_chat_model(
        provider="anthropic", name="claude-sonnet-4-20250514", api_key="sk-ant-test"
    )
    from langchain_anthropic import ChatAnthropic

    assert isinstance(model, ChatAnthropic)


def test_unknown_provider_raises_model_config_error() -> None:
    with pytest.raises(ModelConfigError, match="Unknown provider"):
        create_chat_model(provider="cohere", name="command", api_key="key")


def test_empty_api_key_raises_model_config_error() -> None:
    with pytest.raises(ModelConfigError, match="api_key"):
        create_chat_model(provider="openai", name="gpt-4o", api_key="")
