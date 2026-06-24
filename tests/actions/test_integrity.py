"""Action integrity: a reviewed action must equal its resume (plan N5)."""
from __future__ import annotations

from typing import Any

from modi_harness.actions import (
    from_tool_call,
    hash_action,
    hash_tool_call,
    verify_resumed_action,
)


def _spec(name: str = "fetch_url", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "description": "",
        "input_schema": {"type": "object"},
        "output_schema": None,
        "risk_level": "L1",
        "side_effect": False,
        "permission_scope": "",
        "allowed_agents": [],
        "allowed_skills": [],
        "timeout_seconds": 30,
        "retry": None,
        "idempotent": False,
        "dry_run_supported": False,
        "tags": [],
        "kind": "regular",
        "subagent_target": None,
    }
    base.update(over)
    return base


def _tc(name: str = "fetch_url", args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "tool_call_id": "tc-1",
        "tool_name": name,
        "arguments": args if args is not None else {"url": "https://x"},
        "malformed": False,
        "parse_error": None,
    }


def _action(name: str = "fetch_url", args: dict[str, Any] | None = None):
    return from_tool_call(
        _tc(name, args), spec=_spec(name), intent_version=2, stage_id="stage-explore"
    )


def test_hash_is_stable_for_same_tool_and_args() -> None:
    a = _action(args={"url": "https://x"})
    b = _action(args={"url": "https://x"})
    assert hash_action(a) == hash_action(b)


def test_hash_changes_when_arguments_change() -> None:
    a = _action(args={"url": "https://x"})
    b = _action(args={"url": "https://EVIL"})
    assert hash_action(a) != hash_action(b)


def test_hash_changes_when_tool_changes() -> None:
    a = _action("fetch_url", {"url": "https://x"})
    b = _action("send_email", {"url": "https://x"})
    assert hash_action(a) != hash_action(b)


def test_hash_action_agrees_with_hash_tool_call() -> None:
    """The raw-dict hash and the normalized-proposal hash must agree."""
    tc = _tc(args={"url": "https://x"})
    action = from_tool_call(tc, spec=_spec(), intent_version=2, stage_id="s")
    assert hash_tool_call(tc) == hash_action(action)


def test_argument_key_order_does_not_matter() -> None:
    a = _action(args={"a": 1, "b": 2})
    b = _action(args={"b": 2, "a": 1})
    assert hash_action(a) == hash_action(b)


def test_verify_resumed_action_true_on_match() -> None:
    reviewed = _action(args={"url": "https://x"})
    resumed = _action(args={"url": "https://x"})
    assert verify_resumed_action(hash_action(reviewed), resumed) is True


def test_verify_resumed_action_false_on_tamper() -> None:
    reviewed = _action(args={"url": "https://x"})
    resumed = _action(args={"url": "https://EVIL"})
    assert verify_resumed_action(hash_action(reviewed), resumed) is False
