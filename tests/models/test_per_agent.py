"""Tests for per-agent provider override (N2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from modi_harness.agents import AgentLoader
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
