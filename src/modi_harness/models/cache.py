"""Per-agent model adapter cache.

Allows individual agents to override the global provider/model settings via
their frontmatter ``model:`` block. Cached by ``(provider, name, base_url)``
so repeated agent invocations reuse the same ``ModelAdapter`` instance.
"""

from __future__ import annotations

from typing import Any

from ..config.settings import ModelSettings
from .adapter import ModelAdapter
from .factory import create_chat_model


class ModelAdapterCache:
    def __init__(
        self,
        global_settings: ModelSettings,
        *,
        default_adapter: ModelAdapter | None = None,
    ) -> None:
        self._global = global_settings
        self._cache: dict[tuple[str, str, str], ModelAdapter] = {}
        self._default_adapter: ModelAdapter | None = default_adapter

    def get_or_create(self, agent_model_config: dict[str, Any] | None) -> ModelAdapter:
        """Return a ``ModelAdapter`` for the given per-agent config.

        Falls back to the global default adapter when the per-agent config is
        absent or empty. Identical configs return the same adapter instance.
        """
        if not agent_model_config:
            return self._get_default()

        merged = self._merge(agent_model_config)
        key = (merged["provider"], merged["name"], merged["base_url"])
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        chat_model = create_chat_model(
            provider=merged["provider"],
            name=merged["name"],
            api_key=merged["api_key"],
            base_url=merged["base_url"],
            timeout=self._global.timeout,
        )
        adapter = ModelAdapter(
            chat_model=chat_model,
            retry_attempts=self._global.retry_attempts,
            retry_backoff=self._global.retry_backoff,
            fallback_config=merged["fallback_config"],
            provider=merged["provider"],
            name=merged["name"],
        )
        self._cache[key] = adapter
        return adapter

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _get_default(self) -> ModelAdapter:
        if self._default_adapter is not None:
            return self._default_adapter
        g = self._global
        chat_model = create_chat_model(
            provider=g.provider,
            name=g.name,
            api_key=g.api_key,
            base_url=g.base_url,
            timeout=g.timeout,
        )
        adapter = ModelAdapter(
            chat_model=chat_model,
            retry_attempts=g.retry_attempts,
            retry_backoff=g.retry_backoff,
            fallback_config=self._global_fallback_config(),
            provider=g.provider,
            name=g.name,
        )
        self._default_adapter = adapter
        return adapter

    def _global_fallback_config(self) -> dict[str, Any] | None:
        g = self._global
        if not g.fallback_provider:
            return None
        return {
            "provider": g.fallback_provider,
            "name": g.fallback_name,
            "api_key": g.fallback_api_key,
            "base_url": g.fallback_base_url,
            "timeout": g.timeout,
        }

    def _merge(self, agent_cfg: dict[str, Any]) -> dict[str, Any]:
        """Merge agent config over global defaults; agent values override
        when they are non-empty.
        """
        g = self._global
        provider = (agent_cfg.get("provider") or g.provider) or ""
        name = (agent_cfg.get("name") or g.name) or ""
        api_key = (agent_cfg.get("api_key") or g.api_key) or ""
        base_url = agent_cfg.get("base_url")
        if base_url is None or base_url == "":
            base_url = g.base_url

        # Fallback: per-agent fallback dict overrides global fallback when present.
        agent_fb = agent_cfg.get("fallback")
        if isinstance(agent_fb, dict) and agent_fb.get("provider"):
            fallback_config: dict[str, Any] | None = {
                "provider": agent_fb.get("provider", ""),
                "name": agent_fb.get("name", ""),
                "api_key": agent_fb.get("api_key", ""),
                "base_url": agent_fb.get("base_url", ""),
                "timeout": g.timeout,
            }
        else:
            fallback_config = self._global_fallback_config()

        return {
            "provider": provider,
            "name": name,
            "api_key": api_key,
            "base_url": base_url,
            "fallback_config": fallback_config,
        }
