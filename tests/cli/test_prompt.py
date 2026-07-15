"""Tests for ``modi_harness.cli.prompt``.

Validates ``ApprovalPrompt.ask`` for the three keypress paths (approve,
reject, details-then-loop) by patching ``rich.prompt.Prompt.ask`` with a
scripted ``Mock(side_effect=[...])``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

from rich.console import Console

from modi_harness.cli.prompt import (
    ApprovalPrompt,
    InteractionPrompt,
    NodeReviewPrompt,
    PlanReviewPrompt,
    UserInputPrompt,
)


def _prompt() -> tuple[ApprovalPrompt, Console]:
    console = Console(record=True, width=200, force_terminal=False)
    return ApprovalPrompt(console), console


def _approval(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "approval_id": "ap1",
        "tool_call_id": "tc1",
        "summary": "search(query='cats')",
        "risk_level": "medium",
        "decision_kind": "require_approval",
    }
    base.update(overrides)
    return base


def test_approve_returns_approved_none() -> None:
    prompt_obj, _console = _prompt()
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "approved"
    assert reason is None
    assert mock.call_count == 1


def test_reject_returns_with_reason() -> None:
    prompt_obj, _console = _prompt()
    mock = Mock(side_effect=["r", "too risky"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "rejected"
    assert reason == "too risky"
    assert mock.call_count == 2


def test_details_then_approve() -> None:
    prompt_obj, console = _prompt()
    mock = Mock(side_effect=["d", "a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "approved"
    assert reason is None
    assert mock.call_count == 2
    text = console.export_text(styles=False)
    # The detail panel should have been printed at least once.
    assert "require_approval" in text
    # The summary text should be present (full, untruncated).
    assert "search(query='cats')" in text


def test_details_then_reject() -> None:
    prompt_obj, _console = _prompt()
    mock = Mock(side_effect=["d", "r", "reason text"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "rejected"
    assert reason == "reason text"
    assert mock.call_count == 3


def test_panel_contains_tool_info() -> None:
    prompt_obj, console = _prompt()
    approval = _approval(summary="search(query='X')")
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(approval)

    text = console.export_text(styles=False)
    # Panel labels and key fields show up in the rendered output.
    assert "Tool:" in text
    assert "Args:" in text
    assert "Risk:" in text
    assert "Reason:" in text
    assert "search" in text
    assert "query='X'" in text
    assert "medium" in text
    assert "require_approval" in text


def test_panel_truncates_long_args() -> None:
    prompt_obj, console = _prompt()
    long_args = "x" * 500
    approval = _approval(summary=f"big_tool(payload='{long_args}')")
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(approval)

    text = console.export_text(styles=False)
    assert "..." in text
    # The full 500-char payload must not appear in the rendered output;
    # truncation kicks in well before that.
    assert "x" * 200 not in text
    args_lines = [line for line in text.splitlines() if "Args:" in line]
    assert args_lines


def test_panel_falls_back_to_summary_when_no_tool_call() -> None:
    prompt_obj, console = _prompt()
    approval = _approval(tool_call_id="", summary="raw_summary_no_parens")
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(approval)

    text = console.export_text(styles=False)
    # Without a parsable name(args) summary we still render something useful
    # for the Tool/Args lines.
    assert "raw_summary_no_parens" in text


def test_details_panel_includes_agent_safety_constraints() -> None:
    prompt_obj, console = _prompt()
    agent = {
        "name": "researcher",
        "safety_constraints": ["no_destructive_io", "review_external_calls"],
    }
    mock = Mock(side_effect=["d", "a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        prompt_obj.ask(_approval(), agent=agent)

    text = console.export_text(styles=False)
    assert "researcher" in text
    assert "no_destructive_io" in text
    assert "review_external_calls" in text


def test_details_loops_until_decision() -> None:
    prompt_obj, _console = _prompt()
    # Three details prints in a row, then approve.
    mock = Mock(side_effect=["d", "d", "d", "a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        decision, reason = prompt_obj.ask(_approval())

    assert decision == "approved"
    assert reason is None
    assert mock.call_count == 4


def test_url_list_prompt_collects_until_empty(monkeypatch) -> None:
    console = Console(record=True, force_terminal=False)
    answers = iter(["bad", "https://one.example", "https://two.example", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    prompt = UserInputPrompt(console)

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": "Enter URLs",
            "payload": {"input_type": "url_list", "required": True},
        }
    )

    assert decision == "submitted"
    assert value == ["https://one.example", "https://two.example"]
    assert "must start" in console.export_text(styles=False)


def test_multiline_prompt_accepts_default_on_empty_input(monkeypatch) -> None:
    console = Console(record=True, width=200, force_terminal=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    prompt = UserInputPrompt(console)
    default = "\n".join(
        [
            "url: http://192.168.7.171:5173/home",
            "caseType: 殴打他人",
            "task: 完成殴打他人案取证",
            "dataDir: agents/modi-webagent/data/oudataren/files",
        ]
    )

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": "请提供智证采集参数, 可直接回车使用默认值。",
            "payload": {
                "input_type": "multiline",
                "required": True,
                "default": default,
            },
        }
    )

    assert (decision, value) == ("submitted", default)
    text = console.export_text(styles=False)
    assert "默认: url: http://192.168.7.171:5173/home" in text
    assert "回车=默认" in text


def test_interaction_prompt_dispatches_user_input(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "hello")
    prompt = InteractionPrompt(Console(record=True, force_terminal=False))

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": "Say something",
            "payload": {"input_type": "text", "required": True},
        }
    )

    assert (decision, value) == ("submitted", "hello")


def test_node_review_only_collects_the_decision(monkeypatch) -> None:
    console = Console(record=True, width=200, force_terminal=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "go")
    prompt = NodeReviewPrompt(console)

    decision, feedback = prompt.ask(
        {
            "kind": "node_review",
            "payload": {
                "draft": {
                    "subject": "中控技术",
                    "research_question": "竞争壁垒和风险是什么?",
                    "task_plan": {
                        "items": [
                            {"id": "barriers", "title": "产品和市场竞争壁垒"},
                            {"id": "risks", "title": "经营和行业风险"},
                        ]
                    },
                }
            },
        }
    )

    assert (decision, feedback) == ("approved", None)
    text = console.export_text(styles=False)
    assert "Press Enter or type go to start" in text
    assert "Research scope" not in text


def test_user_input_prompt_does_not_special_case_webagent(monkeypatch) -> None:
    console = Console(record=True, width=200, force_terminal=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "警情")
    prompt = UserInputPrompt(console)

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": "你好! 我是 Modi Webagent, 我可以帮你完成以下网页业务流程...",
            "payload": {"input_type": "text", "field": "task_request", "required": True},
        },
        agent={"name": "webagent"},
    )

    assert (decision, value) == ("submitted", "警情")
    text = console.export_text(styles=False)
    assert "你好! 我是 Modi Webagent" in text
    assert "选择应用" not in text


def test_user_input_prompt_renders_markdown_and_maps_numbered_choices(monkeypatch) -> None:
    console = Console(record=True, width=200, force_terminal=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "3")
    prompt = UserInputPrompt(console)

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": (
                "请选择应用。\n\n"
                "1. **police-intake** - 警情录入\n"
                "2. **zhizheng** - 智证探索\n"
                "3. **zhizheng-replay** - 智证回放\n\n"
                "请输入序号 1/2/3 或应用名称。"
            ),
            "payload": {
                "input_type": "text",
                "field": "task_request",
                "required": True,
                "default": "zhizheng-replay",
                "choices": ["police-intake", "zhizheng", "zhizheng-replay"],
            },
        }
    )

    assert (decision, value) == ("submitted", "zhizheng-replay")
    text = console.export_text(styles=False)
    assert "**" not in text
    assert "police-intake - 警情录入" in text
    assert "可输入序号或完整选项:" in text
    assert "3. zhizheng-replay" in text
    assert "默认: zhizheng-replay" in text


def test_confirm_prompt_displays_and_accepts_suggested_value(monkeypatch) -> None:
    console = Console(record=True, width=200, force_terminal=False)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    prompt = UserInputPrompt(console)

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": "Suggested research question",
            "payload": {
                "input_type": "confirm",
                "required": True,
                "default": "Which providers are strong in latency and availability?",
            },
        }
    )

    assert (decision, value) == (
        "submitted",
        "Which providers are strong in latency and availability?",
    )
    text = console.export_text(styles=False)
    assert "Suggested research question" in text
    assert "默认: Which providers are strong in latency and availability?" in text
    assert "回车/go=使用默认" in text


def test_confirm_prompt_treats_go_as_accepting_default(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "GO")
    prompt = UserInputPrompt(Console(record=True, force_terminal=False))

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": "Suggested research question",
            "payload": {
                "input_type": "confirm",
                "required": True,
                "default": "Source-scoped question",
            },
        }
    )

    assert (decision, value) == ("submitted", "Source-scoped question")


def test_confirm_prompt_accepts_go_default_when_choices_are_present(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "go")
    prompt = UserInputPrompt(Console(record=True, force_terminal=False))

    decision, value = prompt.ask(
        {
            "kind": "user_input",
            "prompt": "请选择要进入的警情记录:",
            "payload": {
                "input_type": "confirm",
                "required": True,
                "default": "J202606300001",
                "choices": ["J202606300001", "J202606290044"],
            },
        }
    )

    assert (decision, value) == ("submitted", "J202606300001")


def test_plan_review_treats_go_as_approval_without_colon_prompt(monkeypatch) -> None:
    seen_prompts: list[str] = []

    def answer(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "go"

    monkeypatch.setattr("builtins.input", answer)
    prompt = PlanReviewPrompt(Console(record=True, force_terminal=False))

    decision, feedback = prompt.ask({"kind": "plan_review"})

    assert (decision, feedback) == ("approved", None)
    assert seen_prompts == ["> "]


# --- JudgmentPrompt (plan N6.4) ---------------------------------------------


def _judgment(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "judgment_id": "j1",
        "approval_id": "j1",
        "tool_call_id": "tc1",
        "summary": "send(to='x')",
        "risk_level": "L3",
        "allowed_kinds": ["approve", "reject", "revise", "constrain"],
        "prompt": "Judge action: send(to='x')",
    }
    base.update(overrides)
    return base


def _jprompt():
    from modi_harness.cli.prompt import JudgmentPrompt

    console = Console(record=True, width=200, force_terminal=False)
    return JudgmentPrompt(console), console


def test_judgment_approve() -> None:
    prompt_obj, _c = _jprompt()
    mock = Mock(side_effect=["a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        kind, rationale, updates = prompt_obj.ask(_judgment())
    assert kind == "approve"
    assert rationale is None
    assert updates == {}


def test_judgment_reject_with_reason() -> None:
    prompt_obj, _c = _jprompt()
    mock = Mock(side_effect=["r", "too risky"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        kind, rationale, updates = prompt_obj.ask(_judgment())
    assert kind == "reject"
    assert rationale == "too risky"
    assert updates == {}


def test_judgment_revise_sets_goal() -> None:
    prompt_obj, _c = _jprompt()
    # choose revise; then provide new-goal text
    mock = Mock(side_effect=["v", "send to the right person"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        kind, _rationale, updates = prompt_obj.ask(_judgment())
    assert kind == "revise"
    assert updates["goal"] == "send to the right person"


def test_judgment_constrain_adds_boundary() -> None:
    prompt_obj, _c = _jprompt()
    mock = Mock(side_effect=["c", "never send to external domains"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        kind, _rationale, updates = prompt_obj.ask(_judgment())
    assert kind == "constrain"
    bs = updates["add_boundaries"]
    assert bs and bs[0]["statement"] == "never send to external domains"
    assert bs[0]["severity"] == "hard"
    assert bs[0]["escalation"] == "deny"


def test_judgment_details_then_approve() -> None:
    prompt_obj, console = _jprompt()
    mock = Mock(side_effect=["d", "a"])
    with patch("modi_harness.cli.prompt.Prompt.ask", mock):
        kind, _r, _u = prompt_obj.ask(_judgment())
    assert kind == "approve"
    text = console.export_text(styles=False)
    assert "send(to='x')" in text
