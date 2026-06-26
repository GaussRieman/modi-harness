from __future__ import annotations

import asyncio
import sys
import types

from modi_harness.cli.input import read_cli_input


def test_read_cli_input_uses_builtin_input_when_not_tty(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt: f"builtin:{prompt}")

    assert read_cli_input("> ") == "builtin:> "


def test_read_cli_input_uses_prompt_toolkit_for_tty(monkeypatch) -> None:
    fake = types.ModuleType("prompt_toolkit")
    fake.prompt = lambda prompt: f"toolkit:{prompt}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "prompt_toolkit", fake)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert read_cli_input("> ") == "toolkit:> "


def test_read_cli_input_falls_back_when_prompt_toolkit_missing(monkeypatch) -> None:
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "prompt_toolkit":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: f"fallback:{prompt}")

    assert read_cli_input("> ") == "fallback:> "


def test_read_cli_input_uses_builtin_input_inside_running_event_loop(monkeypatch) -> None:
    fake = types.ModuleType("prompt_toolkit")

    def fail_if_called(prompt):
        raise AssertionError(f"prompt_toolkit should not be called: {prompt}")

    fake.prompt = fail_if_called  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "prompt_toolkit", fake)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: f"async-safe:{prompt}")

    async def run() -> str:
        return read_cli_input("> ")

    assert asyncio.run(run()) == "async-safe:> "
