"""Tests for create_chat_model factory."""

from __future__ import annotations

import pytest

from modi_harness.models.errors import ModelConfigError
from modi_harness.models.factory import create_chat_model


def test_openai_constructs_ok() -> None:
    model = create_chat_model(provider="openai", name="gpt-4o", api_key="sk-test")
    from langchain_openai import ChatOpenAI

    assert isinstance(model, ChatOpenAI)
    assert model.request_timeout == 30.0
    assert model.max_retries == 0


def test_anthropic_constructs_ok() -> None:
    model = create_chat_model(
        provider="anthropic", name="claude-sonnet-4-20250514", api_key="sk-ant-test"
    )
    from langchain_anthropic import ChatAnthropic

    assert isinstance(model, ChatAnthropic)
    assert model.default_request_timeout == 30.0
    assert model.max_retries == 0
    assert model.streaming is True


def test_provider_timeout_is_configurable() -> None:
    model = create_chat_model(
        provider="openai",
        name="gpt-4o",
        api_key="sk-test",
        timeout=12.5,
    )

    assert model.request_timeout == 12.5


def test_unknown_provider_raises_model_config_error() -> None:
    with pytest.raises(ModelConfigError, match="Unknown provider"):
        create_chat_model(provider="cohere", name="command", api_key="key")


def test_empty_api_key_raises_model_config_error() -> None:
    with pytest.raises(ModelConfigError, match="api_key"):
        create_chat_model(provider="openai", name="gpt-4o", api_key="")


def test_non_positive_timeout_raises_model_config_error() -> None:
    with pytest.raises(ModelConfigError, match="timeout"):
        create_chat_model(
            provider="openai",
            name="gpt-4o",
            api_key="sk-test",
            timeout=0,
        )
