"""Tests for the plugin discovery module (V0.4c)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from modi_harness import plugins as plugins_module
from modi_harness.plugins import (
    PluginLoadError,
    _validate_plugin_dict,
    discover_plugins,
)

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


# ---------------------------------------------------------------------------
# discover_plugins
# ---------------------------------------------------------------------------


def _make_ep(
    name: str,
    loader: Any,
    *,
    value: str = "pkg.mod:get_plugin",
    dist_name: str | None = "test-dist",
    dist_version: str = "1.0.0",
) -> SimpleNamespace:
    """Build a fake EntryPoint-like object that mimics importlib.metadata.

    ``loader`` is a zero-arg callable invoked by ``ep.load()``. Pass a
    function that ``return``s the plugin callable to model a successful load,
    or one that ``raise``s to model an import error.
    """
    dist: SimpleNamespace | None = None
    if dist_name is not None:
        dist = SimpleNamespace(name=dist_name, version=dist_version)
    return SimpleNamespace(name=name, value=value, dist=dist, load=loader)


def _patch_entry_points(
    monkeypatch: pytest.MonkeyPatch, eps: list[SimpleNamespace]
) -> None:
    monkeypatch.setattr(
        plugins_module,
        "entry_points",
        lambda group: list(eps) if group == "modi_harness.plugins" else [],
    )


def test_discover_no_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    assert discover_plugins() == []


def test_discover_one_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agents_dir = tmp_path / "agents"
    skills_dir = tmp_path / "skills"
    agents_dir.mkdir()
    skills_dir.mkdir()

    spec = {
        "name": "do_thing",
        "description": "d",
        "input_schema": {"type": "object"},
        "risk_level": "L1",
    }
    handler = lambda **_: None  # noqa: E731

    def get_plugin() -> dict[str, Any]:
        return {
            "name": "good",
            "agents_dir": agents_dir,
            "skills_dir": skills_dir,
            "tools": [(spec, handler)],
        }

    ep = _make_ep("good", lambda: get_plugin, dist_name="good-dist", dist_version="2.3.4")
    _patch_entry_points(monkeypatch, [ep])

    plugins = discover_plugins()

    assert len(plugins) == 1
    info = plugins[0]
    assert info["name"] == "good"
    assert info["agents_dir"] == agents_dir
    assert info["skills_dir"] == skills_dir
    assert info["tools"] == [(spec, handler)]
    assert info["source"] == "entry_point:good-dist v2.3.4"


def test_discover_iteration_order(monkeypatch: pytest.MonkeyPatch) -> None:
    def make_payload(n: str) -> Any:
        return lambda: {"name": n}

    eps = [
        _make_ep("a", lambda: make_payload("a"), dist_name="a-dist"),
        _make_ep("b", lambda: make_payload("b"), dist_name="b-dist"),
        _make_ep("c", lambda: make_payload("c"), dist_name="c-dist"),
    ]
    _patch_entry_points(monkeypatch, eps)

    names = [p["name"] for p in discover_plugins()]
    assert names == ["a", "b", "c"]


def test_discover_source_falls_back_to_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``ep.dist`` is missing, source falls back to ``ep.value``."""
    ep = _make_ep(
        "x",
        lambda: (lambda: {"name": "x"}),
        value="some_pkg:get_plugin",
        dist_name=None,
    )
    _patch_entry_points(monkeypatch, [ep])

    info = discover_plugins()[0]
    assert info["source"] == "entry_point:some_pkg:get_plugin"


def test_discover_import_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad_load() -> Any:
        raise ImportError("module not found")

    ep = _make_ep("broken", bad_load, dist_name="broken-dist")
    _patch_entry_points(monkeypatch, [ep])

    with pytest.raises(PluginLoadError) as exc:
        discover_plugins()
    assert exc.value.plugin_name == "broken"
    assert "broken-dist" in exc.value.source
    assert isinstance(exc.value.__cause__, ImportError)


def test_discover_module_not_found_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bad_load() -> Any:
        raise ModuleNotFoundError("nope")

    ep = _make_ep("missing", bad_load)
    _patch_entry_points(monkeypatch, [ep])

    with pytest.raises(PluginLoadError) as exc:
        discover_plugins()
    assert exc.value.plugin_name == "missing"


def test_discover_attribute_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bad_load() -> Any:
        raise AttributeError("no such attr")

    ep = _make_ep("missing-attr", bad_load)
    _patch_entry_points(monkeypatch, [ep])

    with pytest.raises(PluginLoadError) as exc:
        discover_plugins()
    assert exc.value.plugin_name == "missing-attr"
    assert isinstance(exc.value.__cause__, AttributeError)


def test_discover_call_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_plugin() -> dict[str, Any]:
        raise RuntimeError("boom")

    ep = _make_ep("crash", lambda: get_plugin)
    _patch_entry_points(monkeypatch, [ep])

    with pytest.raises(PluginLoadError) as exc:
        discover_plugins()
    assert exc.value.plugin_name == "crash"
    assert isinstance(exc.value.__cause__, RuntimeError)


def test_discover_invalid_return(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_plugin() -> dict[str, Any]:
        return {"agents_dir": None}  # missing 'name'

    ep = _make_ep("nameless", lambda: get_plugin)
    _patch_entry_points(monkeypatch, [ep])

    with pytest.raises(PluginLoadError) as exc:
        discover_plugins()
    assert "name" in exc.value.message
