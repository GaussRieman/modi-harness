"""Smoke: package importable, CLI smoke entry runs."""

from __future__ import annotations

import subprocess
import sys


def test_package_imports() -> None:
    import modi_harness

    assert modi_harness.__version__


def test_cli_smoke_entry_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "modi_harness", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip()
