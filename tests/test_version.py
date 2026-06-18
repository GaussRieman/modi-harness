from __future__ import annotations

import tomllib
from pathlib import Path

import modi_harness


def test_runtime_version_matches_project_metadata() -> None:
    project = Path(__file__).parents[1] / "pyproject.toml"
    with project.open("rb") as handle:
        metadata = tomllib.load(handle)
    assert modi_harness.__version__ == metadata["project"]["version"] == "0.7.1"
