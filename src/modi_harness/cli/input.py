"""Unicode-aware terminal input helpers for the CLI."""

from __future__ import annotations

import asyncio
import sys


def read_cli_input(prompt: str = "> ") -> str:
    """Read one line of user input, preferring prompt_toolkit in real terminals.

    Python's stdlib ``input`` delegates line editing to readline/libedit on many
    platforms. macOS libedit can mis-handle East Asian wide characters while
    deleting or moving the cursor, so interactive TTY sessions use
    prompt_toolkit when available. Non-TTY tests and scripted usage keep the
    stdlib path so monkeypatching ``builtins.input`` continues to work.
    """

    if sys.stdin.isatty() and sys.stdout.isatty() and not _event_loop_is_running():
        try:
            from prompt_toolkit import prompt as toolkit_prompt
        except ImportError:
            pass
        else:
            return toolkit_prompt(prompt)
    return input(prompt)


def _event_loop_is_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
