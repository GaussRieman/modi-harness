"""Tests for Hook System."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modi_harness.hooks import HookDispatcher, HookRegistry


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _settings(tmp_path: Path, hooks: list[dict]) -> Path:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": hooks}))
    return p


def _make_dispatcher(tmp_path: Path, hooks: list[dict]) -> HookDispatcher:
    settings_path = _settings(tmp_path, hooks)
    registry = HookRegistry.from_files(user_settings=None, project_settings=settings_path)
    return HookDispatcher(registry=registry, project_root=tmp_path, pass_env=["PATH"])


# ----------------------------------------------------------------------
# Python hooks (in-process; deterministic; no subprocess)
# ----------------------------------------------------------------------


def hook_proceed(payload: dict) -> dict:
    return {"decision": "proceed", "feedback": None}


def hook_block(payload: dict) -> dict:
    return {"decision": "block", "feedback": "blocked by test"}


def hook_redirect(payload: dict) -> dict:
    return {"decision": "redirect", "redirect": {"new_arg": payload.get("tool_name", "x")}}


def hook_raises(payload: dict) -> dict:
    raise RuntimeError("hook crash")


def test_python_hook_proceed(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_proceed",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert len(results) == 1
    assert results[0]["decision"] == "proceed"


def test_python_hook_block(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_block",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert results[0]["decision"] == "block"
    assert results[0]["feedback"] == "blocked by test"


def test_python_hook_redirect(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "user_prompt_submit",
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_redirect",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("user_prompt_submit", {"tool_name": "x"})
    assert results[0]["decision"] == "redirect"
    assert results[0]["redirect"] == {"new_arg": "x"}


def test_redirect_only_allowed_on_specific_events(tmp_path: Path) -> None:
    """A redirect on pre_tool_use is downgraded to proceed (only specific events allow redirect)."""
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",  # not in the redirect-allowed set
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_redirect",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert results[0]["decision"] != "redirect"


def test_first_block_short_circuits(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_block",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            },
            {
                "event": "pre_tool_use",
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_proceed",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            },
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert len(results) == 1
    assert results[0]["decision"] == "block"


def test_matcher_and_combined(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "matcher": {"tool": "git_push", "risk_level": "L4"},
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_block",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    matched = disp.dispatch("pre_tool_use", {"tool_name": "git_push", "risk_level": "L4"})
    not_matched = disp.dispatch("pre_tool_use", {"tool_name": "git_push", "risk_level": "L1"})
    assert matched[0]["decision"] == "block"
    assert not_matched == []


def test_on_failure_block_when_hook_raises(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_raises",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "block",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert results[0]["decision"] == "block"


def test_on_failure_warn_passes_through(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": "python:modi_harness._test_fixtures.hook_inproc.hook_raises",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert results[0]["decision"] == "proceed"


# ----------------------------------------------------------------------
# Shell hooks
# ----------------------------------------------------------------------


def test_shell_hook_non_json_stdout_becomes_feedback(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": "echo plain text",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert results[0]["decision"] == "proceed"
    assert "plain text" in (results[0]["feedback"] or "")


def test_shell_hook_json_stdout_parsed(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": 'echo \'{"decision":"block","feedback":"shell-blocked"}\'',
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "warn",
                "timeout_seconds": 5,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    assert results[0]["decision"] == "block"
    assert results[0]["feedback"] == "shell-blocked"


def test_shell_hook_timeout(tmp_path: Path) -> None:
    disp = _make_dispatcher(
        tmp_path,
        [
            {
                "event": "pre_tool_use",
                "command": "sleep 10",
                "blocking": True,
                "pass_payload": "stdin",
                "capture": "stdout",
                "on_failure": "block",
                "timeout_seconds": 1,
            }
        ],
    )
    results = disp.dispatch("pre_tool_use", {"tool_name": "x"})
    # timeout -> on_failure handling kicks in
    assert results[0]["decision"] == "block"


def test_no_matching_hook_returns_empty(tmp_path: Path) -> None:
    disp = _make_dispatcher(tmp_path, [])
    assert disp.dispatch("pre_tool_use", {}) == []
