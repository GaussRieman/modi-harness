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

from modi_harness import ModiHarness
from modi_harness.__main__ import _cmd_run


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


def _write_agent(root: Path, name: str) -> None:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"""---
name: {name}
description: main test
tools: []
---
Reply directly.
"""
    )


def _write_task(tmp_path: Path) -> Path:
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"goal": "say hi"}))
    return task_path


def _make_harness(tmp_path: Path) -> ModiHarness:
    """Build a ModiHarness wired to a scripted chat model for offline tests."""
    _write_agent(tmp_path / "agents", "demo")
    return ModiHarness(
        agents_dir=tmp_path / "agents",
        skills_dir=None,
        workspace_root=tmp_path / "ws",
        memory_root=tmp_path / "mem",
        chat_model=_Script(script=[AIMessage(content="hello back")]),
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
    harness = _make_harness(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(task_path=task_path, agents_dir=tmp_path / "agents")

    with patch("modi_harness.ModiHarness", return_value=harness), patch(
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
    harness = _make_harness(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(
        task_path=task_path, agents_dir=tmp_path / "agents", no_stream=True
    )

    with patch("modi_harness.ModiHarness", return_value=harness), patch(
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
    harness = _make_harness(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(
        task_path=task_path, agents_dir=tmp_path / "agents", stream=True
    )

    async def _fake_runner(*args, **kwargs):
        return 0

    with patch("modi_harness.ModiHarness", return_value=harness), patch(
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
    harness = _make_harness(tmp_path)
    task_path = _write_task(tmp_path)
    parsed = _parsed_namespace(task_path=task_path, agents_dir=tmp_path / "agents")

    async def _fake_runner(*args, **kwargs):
        return 0

    with patch("modi_harness.ModiHarness", return_value=harness), patch(
        "sys.stdout.isatty", return_value=True
    ), patch(
        "modi_harness.__main__.run_streaming", side_effect=_fake_runner
    ) as mock_stream:
        rc = _cmd_run(parsed)

    assert rc == 0
    assert mock_stream.call_count == 1
