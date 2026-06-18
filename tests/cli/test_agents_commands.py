"""Agent discovery and positional-run CLI tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from modi_harness.__main__ import main


def _write_project(tmp_path: Path) -> Path:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "demo.md").write_text(
        "---\nname: demo\ndescription: Discovered demo\n---\nReply.\n",
        encoding="utf-8",
    )
    (tmp_path / "modi.toml").write_text(
        "[agents]\ninclude_plugins = false\ninclude_user = false\n",
        encoding="utf-8",
    )
    return agents


def test_agents_list_shows_name_and_provenance(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = main(["agents", "list"])

    assert code == 0
    output = capsys.readouterr().out
    assert "demo" in output
    assert "project:demo" in output


def test_agents_show_includes_path_and_description(tmp_path: Path, monkeypatch, capsys) -> None:
    agents = _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = main(["agents", "show", "demo"])

    assert code == 0
    output = capsys.readouterr().out
    assert "description: Discovered demo" in output
    assert str((agents / "demo.md").resolve()) in output
    assert "factory: no" in output


def test_agents_which_accepts_qualified_name(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = main(["agents", "which", "project:demo"])

    assert code == 0
    assert capsys.readouterr().out.splitlines()[0] == "project:demo"


def test_agents_which_missing_returns_actionable_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = main(["agents", "which", "missing"])

    assert code == 2
    error = capsys.readouterr().err
    assert "missing" in error
    assert "available: demo" in error


def test_positional_run_syntax_passes_discovered_agent_to_runner(
    tmp_path: Path, monkeypatch
) -> None:
    agents = _write_project(tmp_path)
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"goal": "say hi"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    async def fake_runner(*args, **kwargs):
        assert kwargs["agent"] == "demo"
        assert kwargs["input"] == {"goal": "say hi"}
        return 0

    with patch("modi_harness.__main__._build_session", return_value=object()), patch(
        "modi_harness.__main__.run_streaming", side_effect=fake_runner
    ):
        code = main([
            "run",
            "demo",
            "--agents-dir",
            str(agents),
            "--task",
            str(task),
            "--stream",
        ])

    assert code == 0


def test_positional_and_legacy_agent_flags_are_mutually_exclusive(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_project(tmp_path)
    task = tmp_path / "task.json"
    task.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = main(["run", "demo", "--agent", "other", "--task", str(task)])

    assert code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_non_tty_run_without_task_fails_instead_of_waiting(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    with patch("modi_harness.__main__._build_session") as build:
        code = main(["run", "demo"])

    assert code == 2
    build.assert_not_called()
    assert "--task is required" in capsys.readouterr().err


def test_dynamic_agent_command_passes_trailing_message(
    tmp_path: Path, monkeypatch
) -> None:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    async def fake_runner(*args, **kwargs):
        assert kwargs["agent"] == "demo"
        assert kwargs["input"] == {"prompt": "hello there"}
        return 0

    with patch("modi_harness.__main__._build_session", return_value=object()), patch(
        "modi_harness.__main__.run_streaming", side_effect=fake_runner
    ):
        code = main(["demo", "hello", "there", "--stream-format", "plain"])

    assert code == 0


def test_dynamic_interactive_agent_starts_without_generic_task_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    agents = _write_project(tmp_path)
    (agents / "demo.md").write_text(
        """---
name: demo
description: Interactive demo
interaction_protocol:
  startup: agent
---
Ask for input first.
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    async def fake_runner(*args, **kwargs):
        assert kwargs["input"] == {"interactive_startup": True}
        return 0

    with patch("modi_harness.__main__._build_session", return_value=object()), patch(
        "modi_harness.__main__.run_streaming", side_effect=fake_runner
    ):
        code = main(["demo"])

    assert code == 0


def test_dynamic_interactive_agent_requires_tty_when_empty(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    agents = _write_project(tmp_path)
    (agents / "demo.md").write_text(
        """---
name: demo
description: Interactive demo
interaction_protocol:
  startup: agent
---
Ask for input first.
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    with patch("modi_harness.__main__._build_session") as build:
        code = main(["demo"])

    assert code == 2
    build.assert_not_called()
    assert "requires a TTY" in capsys.readouterr().err
