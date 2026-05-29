"""Tests for the plugin discovery module (V0.4c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from modi_harness.plugins import PluginLoadError, _validate_plugin_dict

# ---------------------------------------------------------------------------
# _validate_plugin_dict
# ---------------------------------------------------------------------------


def test_validate_full_dict(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    skills_dir = tmp_path / "skills"
    agents_dir.mkdir()
    skills_dir.mkdir()
    spec = {
        "name": "do_thing",
        "description": "does a thing",
        "input_schema": {"type": "object", "properties": {}},
        "risk_level": "L1",
    }
    handler = lambda **_: None  # noqa: E731

    info = _validate_plugin_dict(
        {
            "name": "good-plugin",
            "agents_dir": agents_dir,
            "skills_dir": skills_dir,
            "tools": [(spec, handler)],
        },
        source="explicit",
    )

    assert info["name"] == "good-plugin"
    assert info["agents_dir"] == agents_dir
    assert info["skills_dir"] == skills_dir
    assert info["tools"] == [(spec, handler)]
    assert info["source"] == "explicit"


def test_validate_minimal_dict() -> None:
    info = _validate_plugin_dict({"name": "tiny"}, source="explicit")

    assert info["name"] == "tiny"
    assert info["agents_dir"] is None
    assert info["skills_dir"] is None
    assert info["tools"] == []
    assert info["source"] == "explicit"


def test_validate_string_path_is_normalized(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    info = _validate_plugin_dict(
        {"name": "p", "agents_dir": str(agents_dir)},
        source="explicit",
    )

    assert isinstance(info["agents_dir"], Path)
    assert info["agents_dir"] == agents_dir


def test_validate_missing_name_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict({}, source="explicit")
    assert exc.value.plugin_name == "<unknown>"
    assert exc.value.source == "explicit"
    assert "name" in exc.value.message


def test_validate_empty_name_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict({"name": ""}, source="explicit")
    assert "name" in exc.value.message


def test_validate_non_string_name_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict({"name": 123}, source="explicit")
    assert "name" in exc.value.message


def test_validate_missing_agents_dir_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "does-not-exist"
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "agents_dir": bogus},
            source="explicit",
        )
    assert "agents_dir" in exc.value.message
    assert exc.value.plugin_name == "p"


def test_validate_agents_dir_is_file_raises(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("hello")
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "agents_dir": f},
            source="explicit",
        )
    assert "agents_dir" in exc.value.message


def test_validate_missing_skills_dir_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "missing"
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "skills_dir": bogus},
            source="explicit",
        )
    assert "skills_dir" in exc.value.message


def test_validate_tools_not_a_list_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict({"name": "p", "tools": "not a list"}, source="explicit")
    assert "tools" in exc.value.message


def test_validate_tool_entry_not_a_tuple_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "tools": ["not a tuple"]},
            source="explicit",
        )
    assert "tools" in exc.value.message


def test_validate_tool_handler_not_callable_raises() -> None:
    spec = {
        "name": "t",
        "description": "d",
        "input_schema": {},
        "risk_level": "L0",
    }
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "tools": [(spec, "not callable")]},
            source="explicit",
        )
    assert "tools" in exc.value.message


def test_validate_tool_spec_missing_field_raises() -> None:
    bad_spec = {
        "name": "t",
        "description": "missing input_schema and risk_level",
    }
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "tools": [(bad_spec, lambda **_: None)]},
            source="explicit",
        )
    msg = exc.value.message
    assert "input_schema" in msg or "risk_level" in msg
