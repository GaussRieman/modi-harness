"""Tests for per-agent provider override (N2)."""

from __future__ import annotations

import os

import pytest

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
