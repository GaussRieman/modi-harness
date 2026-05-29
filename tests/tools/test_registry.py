"""ToolRegistry default behavior."""

from __future__ import annotations

from modi_harness.tools import ToolRegistry


def _spec(**overrides):
    base = {
        "name": "search",
        "description": "",
        "input_schema": {},
        "risk_level": "L1",
        "side_effect": False,
    }
    base.update(overrides)
    return base


def test_register_tool_defaults_kind_to_regular() -> None:
    reg = ToolRegistry()
    reg.register_tool(_spec(), lambda **_: {"hits": []})
    spec = reg.get("search")
    assert spec["kind"] == "regular"
    assert spec["subagent_target"] is None


def test_register_tool_subagent_kind_preserved() -> None:
    reg = ToolRegistry()
    reg.register_tool(
        _spec(
            name="delegate_to_research",
            risk_level="L2",
            side_effect=True,
            kind="subagent",
            subagent_target="research",
        ),
        lambda **_: None,
    )
    spec = reg.get("delegate_to_research")
    assert spec["kind"] == "subagent"
    assert spec["subagent_target"] == "research"


def test_register_tool_dry_run_flips_dry_run_supported() -> None:
    reg = ToolRegistry()
    reg.register_tool(_spec(), lambda **_: None, dry_run=lambda **_: None)
    spec = reg.get("search")
    assert spec["dry_run_supported"] is True
