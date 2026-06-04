"""Tests for ``modi_harness.__main__`` TTY-aware ``run`` dispatch (V0.4b N3).

Validates that ``_cmd_run`` selects the streaming runner vs the JSON-dump
fallback based on ``--stream`` / ``--no-stream`` flags and the
``sys.stdout.isatty()`` heuristic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness.__main__ import _cmd_run
from modi_harness._test_fixtures import make_session


class _Script(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "main_script"


def _write_task(tmp_path: Path) -> Path:
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"goal": "say hi"}))
    return task_path


def _make_session(tmp_path: Path):
    """Build a ModiSession wired to a scripted chat model for offline tests."""
    return make_session(
        tmp_path,
        chat_model=_Script(script=[AIMessage(content="hello back")]),
        agent_files={
            "demo": """---
name: demo
description: main test
tools: []
---
Reply directly.
"""
        },
    )


def _parsed_namespace(
    *,
    task_path: Path,
    agents_dir: Path,
    stream: bool | None = None,
    no_stream: bool | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        cmd="run",
        agent="demo",
        agents_dir=str(agents_dir),
        task=str(task_path),
        thread_id=None,
        permission_mode=None,
        stream=stream,
        no_stream=no_stream,
    )


def test_pipe_outputs_json(tmp_path: Path, capsys) -> None:
    """When stdout is not a TTY (piped) and no flags are set, emit JSON."""
    session = _make_session(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(task_path=task_path, agents_dir=tmp_path / "agents")

    with patch("modi_harness.__main__._build_session", return_value=session), patch(
        "sys.stdout.isatty", return_value=False
    ), patch("modi_harness.__main__.run_streaming") as mock_stream:
        rc = _cmd_run(parsed)

    assert rc == 0
    mock_stream.assert_not_called()
    out = capsys.readouterr().out
    parsed_out = json.loads(out)
    assert parsed_out["status"] == "completed"


def test_no_stream_flag_outputs_json(tmp_path: Path, capsys) -> None:
    """``--no-stream`` forces the JSON path even if stdout is a TTY."""
    session = _make_session(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(
        task_path=task_path, agents_dir=tmp_path / "agents", no_stream=True
    )

    with patch("modi_harness.__main__._build_session", return_value=session), patch(
        "sys.stdout.isatty", return_value=True
    ), patch("modi_harness.__main__.run_streaming") as mock_stream:
        rc = _cmd_run(parsed)

    assert rc == 0
    mock_stream.assert_not_called()
    out = capsys.readouterr().out
    parsed_out = json.loads(out)
    assert parsed_out["status"] == "completed"


def test_stream_flag_forces_streaming(tmp_path: Path) -> None:
    """``--stream`` forces the streaming runner regardless of TTY state."""
    session = _make_session(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(
        task_path=task_path, agents_dir=tmp_path / "agents", stream=True
    )

    async def _fake_runner(*args, **kwargs):
        return 0

    with patch("modi_harness.__main__._build_session", return_value=session), patch(
        "sys.stdout.isatty", return_value=False
    ), patch(
        "modi_harness.__main__.run_streaming", side_effect=_fake_runner
    ) as mock_stream:
        rc = _cmd_run(parsed)

    assert rc == 0
    assert mock_stream.call_count == 1
    call_kwargs = mock_stream.call_args.kwargs
    assert call_kwargs["agent"] == "demo"
    assert call_kwargs["input"] == {"goal": "say hi"}


def test_tty_default_streams(tmp_path: Path) -> None:
    """When stdout is a TTY and no flag is set, default to streaming."""
    session = _make_session(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(task_path=task_path, agents_dir=tmp_path / "agents")

    async def _fake_runner(*args, **kwargs):
        return 0

    with patch("modi_harness.__main__._build_session", return_value=session), patch(
        "sys.stdout.isatty", return_value=True
    ), patch(
        "modi_harness.__main__.run_streaming", side_effect=_fake_runner
    ) as mock_stream:
        rc = _cmd_run(parsed)

    assert rc == 0
    assert mock_stream.call_count == 1
