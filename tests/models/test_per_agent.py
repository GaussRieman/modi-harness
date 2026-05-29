"""Tests for per-agent provider override (N2)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from modi_harness.agents import AgentLoader
from modi_harness.config.settings import ModelSettings
from modi_harness.models.factory import expand_env_vars


class TestExpandEnvVars:
    def test_expand_env_vars_replaces_known_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TEST_KEY", "secret123")
        result = expand_env_vars("Bearer ${MY_TEST_KEY}")
        assert result == "Bearer secret123"

    def test_expand_env_vars_missing_var_becomes_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        result = expand_env_vars("prefix-${NONEXISTENT_VAR_XYZ}-suffix")
        assert result == "prefix--suffix"

    def test_expand_env_vars_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A_VAR", "hello")
        monkeypatch.setenv("B_VAR", "world")
        result = expand_env_vars("${A_VAR} ${B_VAR}")
        assert result == "hello world"

    def test_expand_env_vars_no_pattern_unchanged(self) -> None:
        result = expand_env_vars("no variables here")
        assert result == "no variables here"


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


class TestAgentModelBlockParsed:
    def test_agent_model_block_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_API_KEY", "sk-agent-123")
        _write(
            tmp_path / "agents" / "coder.md",
            """\
---
name: coder
description: A coding agent
model:
  provider: anthropic
  name: claude-sonnet-4-20250514
  api_key: "${AGENT_API_KEY}"
  base_url: ""
  fallback:
    provider: openai
    name: gpt-4o
    api_key: "${AGENT_API_KEY}"
---
You are a coder.
""",
        )
        loader = AgentLoader(project_dir=tmp_path / "agents")
        profile = loader.load_agent("coder")
        model_cfg = profile["metadata"]["model"]
        assert model_cfg["provider"] == "anthropic"
        assert model_cfg["api_key"] == "sk-agent-123"
        assert model_cfg["fallback"]["api_key"] == "sk-agent-123"

    def test_model_block_not_in_metadata_as_raw(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """model key should not appear twice in metadata (once processed is enough)."""
        monkeypatch.setenv("K", "v")
        _write(
            tmp_path / "agents" / "x.md",
            "---\nname: x\ndescription: d\nmodel:\n  provider: openai\n  name: gpt-4o\n  api_key: '${K}'\n---\nbody",
        )
        loader = AgentLoader(project_dir=tmp_path / "agents")
        profile = loader.load_agent("x")
        # model is in metadata (processed), not duplicated as raw unknown key
        assert "model" in profile["metadata"]
        assert profile["metadata"]["model"]["api_key"] == "v"


class TestModelAdapterCache:
    def _global(self) -> ModelSettings:
        return ModelSettings(
            provider="openai",
            name="gpt-4o",
            api_key="sk-global",
            base_url="",
        )

    def test_cache_returns_default_when_no_agent_config(self) -> None:
        from modi_harness.models.cache import ModelAdapterCache

        cache = ModelAdapterCache(self._global())
        with patch("modi_harness.models.cache.create_chat_model") as create_mock:
            create_mock.return_value = object()
            a = cache.get_or_create(None)
            b = cache.get_or_create({})
            c = cache.get_or_create(None)
        assert a is b is c
        # Default constructed exactly once.
        assert create_mock.call_count == 1

    def test_cache_creates_new_for_different_config(self) -> None:
        from modi_harness.models.cache import ModelAdapterCache

        cache = ModelAdapterCache(self._global())
        cfg_a = {"provider": "anthropic", "name": "claude-sonnet-4-20250514", "api_key": "k"}
        cfg_b = {"provider": "openai", "name": "gpt-4o-mini", "api_key": "k2"}
        with patch("modi_harness.models.cache.create_chat_model") as create_mock:
            create_mock.side_effect = lambda **kw: object()
            a = cache.get_or_create(cfg_a)
            b = cache.get_or_create(cfg_b)
        assert a is not b
        assert create_mock.call_count == 2

    def test_cache_reuses_adapter_for_same_config(self) -> None:
        from modi_harness.models.cache import ModelAdapterCache

        cache = ModelAdapterCache(self._global())
        cfg = {"provider": "anthropic", "name": "claude-sonnet-4-20250514", "api_key": "k"}
        with patch("modi_harness.models.cache.create_chat_model") as create_mock:
            create_mock.return_value = object()
            a = cache.get_or_create(cfg)
            b = cache.get_or_create(dict(cfg))  # same content, different dict
        assert a is b
        assert create_mock.call_count == 1


class TestTwoAgentsDifferentProviders:
    def test_two_agents_different_providers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Build a harness with two agents pointing at different providers
        and verify the cache materializes a distinct adapter for each."""
        monkeypatch.setenv("OAI_KEY", "sk-oai")
        monkeypatch.setenv("ANT_KEY", "sk-ant")
        # Isolate from local .env so MODI_MODEL_* vars don't bleed into Settings()
        # constructed inside ModiHarness.__init__.
        from modi_harness.config import settings as settings_module

        original_init = settings_module.Settings.__init__

        def _isolated_init(self, _env_file=None, **overrides):  # type: ignore[no-untyped-def]
            original_init(self, _env_file=None, **overrides)

        monkeypatch.setattr(settings_module.Settings, "__init__", _isolated_init)
        for key in list(os.environ):
            if key.startswith("MODI_"):
                monkeypatch.delenv(key, raising=False)

        # Two agents, two different providers in their model: blocks.
        agents_dir = tmp_path / "agents"
        _write(
            agents_dir / "alpha.md",
            """\
---
name: alpha
description: openai agent
model:
  provider: openai
  name: gpt-4o
  api_key: "${OAI_KEY}"
---
alpha body
""",
        )
        _write(
            agents_dir / "beta.md",
            """\
---
name: beta
description: anthropic agent
model:
  provider: anthropic
  name: claude-sonnet-4-20250514
  api_key: "${ANT_KEY}"
---
beta body
""",
        )

        from modi_harness import ModiHarness

        h = ModiHarness(
            agents_dir=agents_dir,
            workspace_root=tmp_path / "ws",
            memory_root=tmp_path / "mem",
        )
        cache = h._model_cache  # type: ignore[attr-defined]
        assert cache is not None

        # Drive the cache directly using each agent's loaded model config.
        loader = AgentLoader(project_dir=agents_dir)
        cfg_alpha = loader.load_agent("alpha")["metadata"]["model"]
        cfg_beta = loader.load_agent("beta")["metadata"]["model"]

        with patch("modi_harness.models.cache.create_chat_model") as create_mock:
            create_mock.side_effect = lambda **kw: object()
            adapter_alpha = cache.get_or_create(cfg_alpha)
            adapter_beta = cache.get_or_create(cfg_beta)

        assert adapter_alpha is not adapter_beta
        # Two different (provider, name, base_url) keys → cache has both.
        keys = list(cache._cache.keys())  # type: ignore[attr-defined]
        assert len(keys) == 2
        assert ("openai", "gpt-4o", "") in keys
        assert ("anthropic", "claude-sonnet-4-20250514", "") in keys
        assert create_mock.call_count == 2
