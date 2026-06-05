"""Tests for task_input_to_text — the run_task input → user-text contract."""

from __future__ import annotations

from modi_harness._utils import task_input_to_text


def test_messages_last_user_wins() -> None:
    payload = {
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
    }
    assert task_input_to_text(payload) == "second"


def test_prompt_key_recognized() -> None:
    assert task_input_to_text({"prompt": "hello"}) == "hello"


def test_customer_message_key() -> None:
    assert task_input_to_text({"customer_message": "charged twice"}) == "charged twice"


def test_question_key() -> None:
    assert task_input_to_text({"question": "why?"}) == "why?"


def test_goal_key() -> None:
    assert task_input_to_text({"goal": "resolve it"}) == "resolve it"


def test_precedence_full_chain() -> None:
    # messages (user) beats everything
    payload = {
        "messages": [{"role": "user", "content": "M"}],
        "prompt": "P",
        "customer_message": "C",
        "question": "Q",
        "goal": "G",
    }
    assert task_input_to_text(payload) == "M"
    # remove messages -> prompt
    del payload["messages"]
    assert task_input_to_text(payload) == "P"
    # remove prompt -> customer_message
    del payload["prompt"]
    assert task_input_to_text(payload) == "C"
    # remove customer_message -> question
    del payload["customer_message"]
    assert task_input_to_text(payload) == "Q"
    # remove question -> goal
    del payload["question"]
    assert task_input_to_text(payload) == "G"


def test_messages_without_user_falls_through() -> None:
    # messages present but no role=="user" → continue to next key (prompt)
    payload = {
        "messages": [{"role": "assistant", "content": "only assistant"}],
        "goal": "fallback goal",
    }
    assert task_input_to_text(payload) == "fallback goal"


def test_empty_payload_falls_back_to_str() -> None:
    assert task_input_to_text({}) == "{}"


def test_unrecognized_only_falls_back_to_str() -> None:
    payload = {"unknown_key": "x"}
    assert task_input_to_text(payload) == str(payload)


def test_messages_user_content_none_returns_empty() -> None:
    payload = {"messages": [{"role": "user", "content": None}]}
    assert task_input_to_text(payload) == ""


def test_messages_user_no_content_key_returns_empty() -> None:
    payload = {"messages": [{"role": "user"}]}
    assert task_input_to_text(payload) == ""
