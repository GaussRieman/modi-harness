from __future__ import annotations

from typing import Any

from modi_harness.graph.interaction_protocol import (
    execute_interaction_protocol,
    interaction_protocol_specs,
    validate_user_input_response,
)


def _profile(startup: str = "agent") -> dict[str, Any]:
    return {"metadata": {"interaction_protocol": {"startup": startup}}}


def _state() -> dict[str, Any]:
    return {
        "run_id": "run",
        "root_run_id": "run",
        "parent_run_id": None,
        "thread_id": "thread",
    }


def test_user_input_tool_is_opt_in() -> None:
    assert interaction_protocol_specs(_profile("prompt")) == {}  # type: ignore[arg-type]
    assert set(interaction_protocol_specs(_profile())) == {"request_user_input"}  # type: ignore[arg-type]


def test_user_input_call_creates_checkpoint_interaction() -> None:
    update = execute_interaction_protocol(  # type: ignore[arg-type]
        _state(),
        {
            "tool_call_id": "ask-1",
            "tool_name": "request_user_input",
            "arguments": {
                "prompt": "Enter URLs",
                "input_type": "url_list",
            },
        },
    )

    interaction = update["pending_interaction"]
    assert interaction["kind"] == "user_input"
    assert interaction["payload"]["field"] == "source_urls"
    assert interaction["payload"]["input_type"] == "url_list"
    assert "messages" not in update


def test_user_input_response_validation() -> None:
    interaction = {
        "payload": {"input_type": "url_list", "required": True},
    }
    assert validate_user_input_response(interaction, ["https://example.com"]) is None
    assert "list" in (validate_user_input_response(interaction, "bad") or "")
    assert "required" in (validate_user_input_response(interaction, []) or "")


def test_confirm_response_go_accepts_default_before_choice_validation() -> None:
    interaction = {
        "payload": {
            "input_type": "confirm",
            "required": True,
            "default": "J202606300001",
            "choices": ["J202606300001", "J202606290044"],
        },
    }

    assert validate_user_input_response(interaction, "go") is None
    assert validate_user_input_response(interaction, "") is None
    assert "declared choices" in (validate_user_input_response(interaction, "J000") or "")
