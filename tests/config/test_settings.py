"""Tests for Settings: env loading, defaults, path expansion, structured groups."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from modi_harness.config import Settings


def _clear_modi_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in list(os.environ):
        if var.startswith("MODI_"):
            monkeypatch.delenv(var, raising=False)


def test_defaults_when_no_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.runtime.permission_mode == "ask"
    assert s.runtime.max_steps == 20
    assert s.runtime.repair_budget == 3
    assert s.policy.rule_packs == ["core"]


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_PERMISSION_MODE", "auto")
    monkeypatch.setenv("MODI_MAX_STEPS", "42")
    monkeypatch.setenv("MODI_POLICY_RULE_PACKS", "core,coding,messaging")
    s = Settings(_env_file=None)
    assert s.runtime.permission_mode == "auto"
    assert s.runtime.max_steps == 42
    assert s.policy.rule_packs == ["core", "coding", "messaging"]


def test_path_expansion_user_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_AGENT_USER_DIR", "~/.modi/agents")
    s = Settings(_env_file=None)
    assert s.loaders.agent_user_dir.is_absolute()
    assert "~" not in str(s.loaders.agent_user_dir)


def test_workspace_root_path_object(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_WORKSPACE_ROOT", str(tmp_path / "ws"))
    s = Settings(_env_file=None)
    assert isinstance(s.storage.workspace_root, Path)
    assert s.storage.workspace_root == tmp_path / "ws"


def test_model_settings_missing_does_not_raise_at_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Model config absence must not blow up Settings load — Model Adapter validates lazily."""
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.model.api_key == ""
    assert s.model.provider == "openai"  # default


def test_trace_redact_keys_split(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_TRACE_REDACT_KEYS", "api_key,authorization,custom_secret")
    s = Settings(_env_file=None)
    assert "custom_secret" in s.storage.trace_redact_keys
    assert "api_key" in s.storage.trace_redact_keys


def test_settings_is_immutable_after_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    with pytest.raises(Exception):
        s.runtime.max_steps = 99  # type: ignore[misc]


def test_dotenv_file_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("MODI_MAX_STEPS=7\nMODI_PERMISSION_MODE=plan\n")
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=str(env_file))
    assert s.runtime.max_steps == 7
    assert s.runtime.permission_mode == "plan"


def test_real_env_overrides_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Real env vars take precedence over .env."""
    _clear_modi_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("MODI_MAX_STEPS=7\n")
    monkeypatch.setenv("MODI_MAX_STEPS", "99")
    s = Settings(_env_file=str(env_file))
    assert s.runtime.max_steps == 99


def test_hook_pass_env_split(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_HOOK_PASS_ENV", "PATH,LANG,LC_ALL")
    s = Settings(_env_file=None)
    assert s.hooks.pass_env == ["PATH", "LANG", "LC_ALL"]


def test_rule_packs_implicit_core(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_modi_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODI_POLICY_RULE_PACKS", "coding")  # core not listed
    s = Settings(_env_file=None)
    assert s.policy.rule_packs == ["core", "coding"]
