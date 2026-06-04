"""Tests for the plugin discovery module (V0.5)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from modi_harness import ModiAgent
from modi_harness import plugins as plugins_module
from modi_harness.plugins import (
    PluginLoadError,
    _validate_plugin_dict,
    discover_plugins,
)
from modi_harness.types import ToolBinding

# ---------------------------------------------------------------------------
# _validate_plugin_dict
# ---------------------------------------------------------------------------


def _agent(name: str = "do-thing") -> ModiAgent:
    return ModiAgent(name=name, description="does a thing", instruction="reply")


def _tool_spec(name: str = "do_thing") -> dict[str, Any]:
    return {
        "name": name,
        "description": "does a thing",
        "input_schema": {"type": "object", "properties": {}},
        "risk_level": "L1",
    }


def test_validate_full_dict() -> None:
    agent = _agent()
    spec = _tool_spec()
    handler = lambda **_: None  # noqa: E731

    info = _validate_plugin_dict(
        {
            "name": "good-plugin",
            "agents": [agent],
            "kernel_tools": [(spec, handler)],
        },
        source="explicit",
    )

    assert info["name"] == "good-plugin"
    assert info["agents"] == [agent]
    assert len(info["kernel_tools"]) == 1
    assert info["kernel_tools"][0] == ToolBinding(spec=spec, handler=handler)
    assert info["source"] == "explicit"


def test_validate_minimal_dict() -> None:
    info = _validate_plugin_dict({"name": "tiny"}, source="explicit")

    assert info["name"] == "tiny"
    assert info["agents"] == []
    assert info["kernel_tools"] == []
    assert info["source"] == "explicit"


def test_validate_accepts_toolbinding_directly() -> None:
    binding = ToolBinding(spec=_tool_spec(), handler=lambda **_: None)
    info = _validate_plugin_dict(
        {"name": "p", "kernel_tools": [binding]},
        source="explicit",
    )
    assert info["kernel_tools"] == [binding]


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


def test_validate_agents_not_a_list_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict({"name": "p", "agents": "nope"}, source="explicit")
    assert "agents" in exc.value.message
    assert exc.value.plugin_name == "p"


def test_validate_agent_not_a_modiagent_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "agents": ["not-an-agent"]},
            source="explicit",
        )
    assert "agents[0]" in exc.value.message


def test_validate_kernel_tools_not_a_list_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "kernel_tools": "not a list"}, source="explicit"
        )
    assert "kernel_tools" in exc.value.message


def test_validate_kernel_tool_entry_invalid_raises() -> None:
    with pytest.raises(PluginLoadError) as exc:
        _validate_plugin_dict(
            {"name": "p", "kernel_tools": [12345]},
            source="explicit",
        )
    assert "kernel_tools[0]" in exc.value.message


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


def test_discover_one_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent("good-agent")
    spec = _tool_spec()
    handler = lambda **_: None  # noqa: E731

    def get_plugin() -> dict[str, Any]:
        return {
            "name": "good",
            "agents": [agent],
            "kernel_tools": [(spec, handler)],
        }

    ep = _make_ep("good", lambda: get_plugin, dist_name="good-dist", dist_version="2.3.4")
    _patch_entry_points(monkeypatch, [ep])

    plugins = discover_plugins()

    assert len(plugins) == 1
    info = plugins[0]
    assert info["name"] == "good"
    assert info["agents"] == [agent]
    assert info["kernel_tools"][0].spec["name"] == "do_thing"
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
        return {"agents": []}  # missing 'name'

    ep = _make_ep("nameless", lambda: get_plugin)
    _patch_entry_points(monkeypatch, [ep])

    with pytest.raises(PluginLoadError) as exc:
        discover_plugins()
    assert "name" in exc.value.message


# ---------------------------------------------------------------------------
# sample_plugin fixture
# ---------------------------------------------------------------------------


_SAMPLE_PLUGIN_DIR = Path(__file__).parent / "fixtures" / "sample_plugin"


def _load_sample_plugin_module() -> Any:
    """Load the sample_plugin fixture package by file path.

    The ``tests/`` directory is not a Python package (no ``__init__.py``),
    so we cannot ``import tests.plugins.fixtures.sample_plugin``. Loading
    via importlib's spec API keeps the fixture self-contained and avoids
    polluting sys.path.
    """
    import importlib.util
    import sys

    if "modi_test_sample_plugin" in sys.modules:
        return sys.modules["modi_test_sample_plugin"]

    spec = importlib.util.spec_from_file_location(
        "modi_test_sample_plugin",
        _SAMPLE_PLUGIN_DIR / "__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["modi_test_sample_plugin"] = module
    spec.loader.exec_module(module)
    return module


def test_sample_plugin_fixture_validates_cleanly() -> None:
    module = _load_sample_plugin_module()
    payload = module.get_plugin()
    info = _validate_plugin_dict(payload, source="explicit")

    assert info["name"] == "sample-plugin"
    assert len(info["agents"]) == 1
    assert info["agents"][0].name == "sample-agent"
    assert len(info["kernel_tools"]) == 1
    binding = info["kernel_tools"][0]
    assert binding.spec["name"] == "sample_tool"
    assert callable(binding.handler)
    assert binding.handler() == {"result": "ok"}


def test_sample_plugin_fixture_via_discover_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sample_plugin_module()

    ep = _make_ep(
        "sample-plugin",
        lambda: module.get_plugin,
        value="tests.plugins.fixtures.sample_plugin:get_plugin",
        dist_name="sample-dist",
        dist_version="0.0.1",
    )
    _patch_entry_points(monkeypatch, [ep])

    plugins = discover_plugins()
    assert len(plugins) == 1
    info = plugins[0]
    assert info["name"] == "sample-plugin"
    assert info["source"] == "entry_point:sample-dist v0.0.1"
