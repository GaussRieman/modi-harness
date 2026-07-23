"""Multi-provider chat model factory.

Lazily imports provider packages so the harness only requires the package
for the provider actually in use.
"""

from __future__ import annotations

import os
import re
from typing import Any

from langchain_core.language_models import BaseChatModel

from .errors import ModelConfigError

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars(value: str) -> str:
    """Replace ``${VAR_NAME}`` patterns with the corresponding env value.

    Missing variables expand to the empty string.
    """
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)


def create_chat_model(
    *,
    provider: str,
    name: str,
    api_key: str,
    base_url: str = "",
    timeout: float = 30.0,
) -> BaseChatModel:
    """Construct a LangChain chat model for the given provider.

    Raises:
        ModelConfigError: If *provider* is unknown or *api_key* is empty.
    """
    if not api_key:
        raise ModelConfigError("api_key must not be empty")
    if timeout <= 0:
        raise ModelConfigError("timeout must be positive")

    if provider == "openai":
        return _make_openai(
            name=name,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    if provider == "anthropic":
        return _make_anthropic(
            name=name,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    raise ModelConfigError(f"Unknown provider: {provider!r}")


def _make_openai(
    *,
    name: str,
    api_key: str,
    base_url: str,
    timeout: float,
) -> BaseChatModel:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ModelConfigError("langchain-openai package is not installed") from exc

    kwargs: dict[str, Any] = {
        "model": name,
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def _make_anthropic(
    *,
    name: str,
    api_key: str,
    base_url: str,
    timeout: float,
) -> BaseChatModel:
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ModelConfigError("langchain-anthropic package is not installed") from exc

    kwargs: dict[str, Any] = {
        "model": name,
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": 0,
        "streaming": True,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatAnthropic(**kwargs)
