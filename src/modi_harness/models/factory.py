"""Multi-provider chat model factory.

Lazily imports provider packages so the harness only requires the package
for the provider actually in use.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from .errors import ModelConfigError


def create_chat_model(
    *,
    provider: str,
    name: str,
    api_key: str,
    base_url: str = "",
) -> BaseChatModel:
    """Construct a LangChain chat model for the given provider.

    Raises:
        ModelConfigError: If *provider* is unknown or *api_key* is empty.
    """
    if not api_key:
        raise ModelConfigError("api_key must not be empty")

    if provider == "openai":
        return _make_openai(name=name, api_key=api_key, base_url=base_url)
    if provider == "anthropic":
        return _make_anthropic(name=name, api_key=api_key, base_url=base_url)

    raise ModelConfigError(f"Unknown provider: {provider!r}")


def _make_openai(*, name: str, api_key: str, base_url: str) -> BaseChatModel:
    try:
        from langchain_openai import ChatOpenAI  # noqa: WPS433
    except ImportError as exc:
        raise ModelConfigError("langchain-openai package is not installed") from exc

    kwargs: dict = {"model": name, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def _make_anthropic(*, name: str, api_key: str, base_url: str) -> BaseChatModel:
    try:
        from langchain_anthropic import ChatAnthropic  # noqa: WPS433
    except ImportError as exc:
        raise ModelConfigError("langchain-anthropic package is not installed") from exc

    kwargs: dict = {"model": name, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatAnthropic(**kwargs)
