"""Tests for ``modi_harness.cli.renderer``.

Validates dispatch of stream events into rich console output and the side
return values used by the future REPL (approval payload, terminal response).
"""

from __future__ import annotations

from io import StringIO
from types import MappingProxyType
from typing import Any

import pytest
from rich.console import Console

from modi_harness.cli.renderer import (
    StreamRenderer,
    TaskProgressRenderer,
    _format_terminal_output,
    _truncate,
)


def _renderer() -> tuple[StreamRenderer, Console]:
    console = Console(record=True, width=200, force_terminal=False)
    return StreamRenderer(console), console


def test_model_delta_inline() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "model_delta",
        "run_id": "r",
        "sequence": 1,
        "payload": {"delta": "hello"},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    text = console.export_text(styles=False)
    assert "hello" in text
    # No trailing newline appended by the renderer itself.
    assert not text.endswith("\n\n")
    # Single delta should not introduce a leading newline.
    assert text.startswith("hello")


def test_model_delta_falls_back_to_content() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "model_delta",
        "run_id": "r",
        "sequence": 1,
        "payload": {"content": "world"},
        "terminal_response": None,
    }

    renderer.render_event(event)

    assert "world" in console.export_text(styles=False)


def test_tool_call_proposal_marker() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "tool_call_proposal",
        "run_id": "r",
        "sequence": 2,
        "payload": {"tool_name": "fs.read", "arguments": {"path": "/tmp/x.txt"}},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    text = console.export_text(styles=False)
    assert "▸" in text
    assert "fs.read" in text
    assert "path" in text
    assert text.endswith("\n")


def test_tool_call_proposal_truncates_arguments() -> None:
    renderer, console = _renderer()
    long_args = {"payload": "x" * 500}
    event = {
        "event_type": "tool_call_proposal",
        "run_id": "r",
        "sequence": 2,
        "payload": {"tool_name": "fs.write", "arguments": long_args},
        "terminal_response": None,
    }

    renderer.render_event(event)

    text = console.export_text(styles=False)
    assert "..." in text
    # Sanity: the line must remain bounded.
    assert len(text.splitlines()[0]) < 200


def test_tool_call_result_marker() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "tool_call_result",
        "run_id": "r",
        "sequence": 3,
        "payload": {"tool_call_id": "tc1", "content": "file contents here"},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    text = console.export_text(styles=False)
    assert "←" in text
    assert "file contents here" in text


def test_tool_call_result_truncates_long_content() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "tool_call_result",
        "run_id": "r",
        "sequence": 3,
        "payload": {"content": "a" * 500},
        "terminal_response": None,
    }

    renderer.render_event(event)

    text = console.export_text(styles=False)
    assert "..." in text


def test_workflow_progress_events_are_visible() -> None:
    renderer, console = _renderer()

    renderer.render_event({"event_type": "node_started", "payload": {"node_id": "research"}})
    renderer.render_event(
        {
            "event_type": "operation_started",
            "payload": {"adapter_id": "public_web_research"},
        }
    )
    renderer.render_event(
        {
            "event_type": "operation_completed",
            "payload": {"adapter_id": "public_web_research"},
        }
    )
    renderer.render_event(
        {
            "event_type": "completion_rejected",
            "payload": {"feedback": "evidence[0].source_url must match a declared source"},
        }
    )

    text = console.export_text(styles=False)
    assert "… research" in text
    assert "▸ public_web_research" in text
    assert "← public_web_research done" in text
    assert "↻ evidence[0].source_url must match a declared source" in text


def test_repairable_missing_complete_result_is_hidden() -> None:
    renderer, console = _renderer()

    renderer.render_event(
        {
            "event_type": "completion_rejected",
            "payload": {"feedback": "complete_node requires result"},
        }
    )

    assert console.export_text(styles=False) == ""


def test_workflow_selection_is_visible_with_route_summary() -> None:
    renderer, console = _renderer()

    renderer.render_event(
        {
            "event_type": "workflow_selected",
            "payload": {
                "workflow_id": "deep_research",
                "strategy": "model",
                "summary": "评估公司的技术实力和风险",
            },
        }
    )

    text = console.export_text(styles=False)
    assert "◆ deep research" in text
    assert "评估公司的技术实力和风险" in text


def test_deep_research_starts_with_semantic_exploration_progress() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console)

    renderer.render_event(
        {
            "event_type": "workflow_selected",
            "payload": {"workflow_id": "deep_research", "summary": "杭州 AI 就业"},
        }
    )
    renderer.render_event(
        {
            "event_type": "model_delta",
            "payload": {"delta": "以下是我制定的范围草案, 是否确认执行?"},
        }
    )

    text = console.export_text(styles=False)
    assert "◆ deep research" not in text
    assert "◆ 正在探索" in text
    assert "以下是我制定的范围草案" not in text


def test_deep_research_shows_only_one_progress_view_and_final_output() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {"event_type": "workflow_selected", "payload": {"workflow_id": "deep_research"}}
    )
    renderer.render_event({"event_type": "node_started", "payload": {"node_id": "investigate"}})
    renderer.render_event(
        {
            "event_type": "operation_started",
            "payload": {"adapter_id": "public_web_search"},
        }
    )
    renderer.render_event(
        {
            "event_type": "completion_rejected",
            "payload": {"feedback": "internal repair"},
        }
    )
    renderer.render_event(
        {
            "event_type": "task_plan_created",
            "payload": {
                "task_plan": {
                    "items": [
                        {
                            "id": "one",
                            "title": "研究公司背景",
                            "status": "pending",
                            "summary": None,
                        },
                        {"id": "two", "title": "研究融资", "status": "pending", "summary": None},
                    ],
                    "current_action": None,
                }
            },
        }
    )

    text = console.export_text(styles=False)
    assert "Research Task Graph · 0/2" in text
    assert "○ 研究公司背景" in text
    assert "○ 研究融资" in text
    assert "investigate" not in text
    assert "public_web_search" not in text
    assert "internal repair" not in text


def test_scope_review_and_task_progress_share_one_live_panel() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {"event_type": "workflow_selected", "payload": {"workflow_id": "deep_research"}}
    )
    interaction = renderer.render_event(
        {
            "event_type": "interaction_requested",
            "payload": {
                "kind": "node_review",
                "payload": {
                    "draft": {
                        "subject": "威灿科技 vs 高新兴",
                        "research_question": "两家公司有什么差异?",
                        "task_plan": {
                            "items": [
                                {"id": "business", "title": "业务对比"},
                                {"id": "finance", "title": "财务对比"},
                            ]
                        },
                    }
                },
            },
        }
    )
    renderer.prepare_for_prompt()
    renderer.render_event(
        {
            "event_type": "task_started",
            "payload": {
                "task_plan": {
                    "items": [
                        {
                            "id": "business",
                            "title": "业务对比",
                            "status": "in_progress",
                            "summary": None,
                        },
                        {
                            "id": "finance",
                            "title": "财务对比",
                            "status": "pending",
                            "summary": None,
                        },
                    ]
                }
            },
        }
    )

    assert interaction is not None
    text = console.export_text(styles=False)
    assert "Research scope" in text
    assert "主体: 威灿科技 vs 高新兴" in text
    assert "Research Task Graph · 0/2" in text
    assert "● 业务对比" in text


def test_scope_prompt_renders_once_before_starting_one_progress_live_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_instances: list[Any] = []

    class FakeLive:
        def __init__(self, renderable: Any, **kwargs: Any) -> None:
            self.renderable = renderable
            self.kwargs = kwargs
            self.started = 0
            self.stopped = 0
            self.updates: list[tuple[Any, bool]] = []
            live_instances.append(self)

        def start(self) -> None:
            self.started += 1

        def update(self, renderable: Any, *, refresh: bool = False) -> None:
            self.updates.append((renderable, refresh))

        def stop(self) -> None:
            self.stopped += 1

    monkeypatch.setattr("modi_harness.cli.renderer.Live", FakeLive)
    console = Console(record=True, width=120, force_terminal=True)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {"event_type": "workflow_selected", "payload": {"workflow_id": "deep_research"}}
    )
    interaction = {
        "kind": "node_review",
        "payload": {
            "draft": {
                "subject": "威灿科技 vs 高新兴",
                "research_question": "两家公司有什么差异?",
                "task_plan": {"items": [{"id": "business", "title": "业务对比"}]},
            }
        },
    }

    renderer.render_event({"event_type": "interaction_requested", "payload": interaction})

    assert len(live_instances) == 0
    assert console.export_text(styles=False).count("Research scope") == 1
    renderer.prepare_for_prompt()

    renderer.resume_after_prompt(interaction, "approved")
    assert len(live_instances) == 0

    renderer.render_event(
        {
            "event_type": "task_started",
            "payload": {
                "task_plan": {
                    "items": [
                        {
                            "id": "business",
                            "title": "业务对比",
                            "status": "in_progress",
                            "summary": None,
                        }
                    ]
                }
            },
        }
    )

    assert len(live_instances) == 1
    assert live_instances[0].kwargs["refresh_per_second"] == 4
    assert live_instances[0].started == 1


def test_scope_review_does_not_write_manual_cursor_restore_sequences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLive:
        def __init__(self, renderable: Any, **kwargs: Any) -> None:
            del renderable, kwargs

        def start(self) -> None:
            pass

        def update(self, renderable: Any, *, refresh: bool = False) -> None:
            del renderable, refresh

        def stop(self) -> None:
            pass

    monkeypatch.setattr("modi_harness.cli.renderer.Live", FakeLive)
    output = StringIO()
    console = Console(file=output, width=120, force_terminal=True)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {"event_type": "workflow_selected", "payload": {"workflow_id": "deep_research"}}
    )
    interaction = {
        "kind": "node_review",
        "payload": {
            "draft": {
                "subject": "Tesla Model Y vs 小米YU7",
                "research_question": "两款车有什么差异?",
                "task_plan": {"items": [{"id": "specs", "title": "规格对比"}]},
            }
        },
    }

    renderer.render_event({"event_type": "interaction_requested", "payload": interaction})
    renderer.prepare_for_prompt()
    renderer.resume_after_prompt(interaction, "approved")

    assert "\x1b7" not in output.getvalue()
    assert "\x1b8" not in output.getvalue()


def test_deep_research_spinner_is_part_of_progress_title_without_query_text() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {"event_type": "workflow_selected", "payload": {"workflow_id": "deep_research"}}
    )
    renderer.render_event(
        {
            "event_type": "task_started",
            "payload": {
                "task_plan": {
                    "items": [
                        {
                            "id": "company",
                            "title": "研究公司背景",
                            "status": "in_progress",
                            "summary": None,
                        }
                    ],
                    "current_action": "拉格朗日具身智能 公司注册 创始人",
                }
            },
        }
    )

    text = console.export_text(styles=False)
    assert "Research Task Graph · 0/1" in text
    assert "公司注册 创始人" not in text


def test_protocol_tools_are_not_rendered_as_regular_tool_activity() -> None:
    renderer, console = _renderer()
    renderer.render_event(
        {
            "event_type": "tool_call_proposal",
            "run_id": "r",
            "sequence": 1,
            "payload": {
                "tool_call_id": "ask-1",
                "tool_name": "request_user_input",
                "arguments": {"prompt": "Enter a URL"},
            },
            "terminal_response": None,
        }
    )
    renderer.render_event(
        {
            "event_type": "tool_call_result",
            "run_id": "r",
            "sequence": 2,
            "payload": {"tool_call_id": "ask-1", "content": "submitted"},
            "terminal_response": None,
        }
    )

    assert console.export_text(styles=False) == ""


def test_research_finding_operation_is_represented_by_task_progress() -> None:
    renderer, console = _renderer()

    renderer.render_event(
        {
            "event_type": "operation_started",
            "payload": {"adapter_id": "record_research_finding"},
        }
    )
    renderer.render_event(
        {
            "event_type": "operation_completed",
            "payload": {"adapter_id": "record_research_finding"},
        }
    )

    assert console.export_text(styles=False) == ""


def test_submit_output_is_not_rendered_as_tool_activity() -> None:
    renderer, console = _renderer()
    renderer.render_event(
        {
            "event_type": "tool_call_proposal",
            "run_id": "r",
            "sequence": 1,
            "payload": {
                "tool_call_id": "submit-1",
                "tool_name": "submit_output",
                "arguments": {"answer": "done"},
            },
            "terminal_response": None,
        }
    )
    renderer.render_event(
        {
            "event_type": "tool_call_result",
            "run_id": "r",
            "sequence": 2,
            "payload": {"tool_call_id": "submit-1", "content": "submitted"},
            "terminal_response": None,
        }
    )

    assert console.export_text(styles=False) == ""


def test_approval_request_returns_payload() -> None:
    renderer, _console = _renderer()
    payload: dict[str, Any] = {
        "approval_id": "ap1",
        "tool_call_id": "tc1",
        "summary": "delete file",
        "risk_level": "high",
        "decision_kind": "require_approval",
    }
    event = {
        "event_type": "approval_request",
        "run_id": "r",
        "sequence": 4,
        "payload": payload,
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result == payload


def test_approval_request_does_not_print() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "approval_request",
        "run_id": "r",
        "sequence": 4,
        "payload": {"approval_id": "ap", "summary": "x"},
        "terminal_response": None,
    }

    renderer.render_event(event)

    assert console.export_text(styles=False) == ""


def test_terminal_completed_green() -> None:
    renderer, console = _renderer()
    response = {
        "run_id": "r",
        "thread_id": "t",
        "status": "completed",
        "output": {"text": "done"},
        "pending_approval": None,
        "error": None,
        "elapsed": 1.234,
    }
    event = {
        "event_type": "terminal",
        "run_id": "r",
        "sequence": 5,
        "payload": {"response": response},
        "terminal_response": response,
    }

    result = renderer.render_event(event)

    assert result == response
    text = console.export_text(styles=False)
    assert "✓" in text
    assert "completed" in text
    assert "1.2" in text
    assert "done" in text


def test_terminal_completed_renders_structured_output_summary() -> None:
    renderer, console = _renderer()
    response = {
        "run_id": "r",
        "thread_id": "t",
        "status": "completed",
        "output": {
            "direct_answer": "核心结论已经形成。",
            "key_findings": [
                {
                    "question": "界定来源覆盖",
                    "conclusion": "来源覆盖 PDF 在线处理工具。",
                    "implications": "可以据此比较公开产品能力。",
                    "confidence": "medium",
                    "evidence": [
                        {
                            "claim": "公开页面列出了 PDF 处理能力。",
                            "source_url": "https://example.test/source",
                            "source_type": "primary",
                            "as_of": "2026-07",
                        }
                    ],
                },
            ],
            "recommendations": [],
            "limitations": ["两家公司的价格资料不可用。"],
            "citations": ["https://example.test/source"],
        },
        "pending_approval": None,
        "error": None,
    }
    event = {
        "event_type": "terminal",
        "run_id": "r",
        "sequence": 5,
        "payload": {"response": response},
        "terminal_response": response,
    }

    renderer.render_event(event)

    text = console.export_text(styles=False)
    assert "核心结论已经形成" in text
    assert "界定来源覆盖" in text
    assert "可以据此比较公开产品能力" not in text
    assert "公开页面列出了 PDF 处理能力" not in text
    assert "来源: [1]" in text
    assert "置信度" not in text
    assert "限制:" in text
    assert "两家公司的价格资料不可用" in text
    assert "来源:" in text
    assert "[1] https://example.test/source" in text


def test_terminal_output_renders_frozen_research_shape() -> None:
    output = MappingProxyType(
        {
            "direct_answer": "Only committed findings are published.",
            "key_findings": (
                MappingProxyType(
                    {
                        "question": "Was the primary source available?",
                        "conclusion": "The available source was secondary.",
                        "status": "limited",
                        "confidence": "low",
                        "evidence": (
                            MappingProxyType(
                                {
                                    "claim": "A reference work discusses the claim.",
                                    "source_url": "https://example.test/reference",
                                    "source_type": "secondary",
                                    "as_of": "2026-07-19",
                                }
                            ),
                        ),
                    }
                ),
            ),
            "recommendations": ("Obtain the primary text before relying on the claim.",),
            "limitations": ("The required primary source was unavailable.",),
            "citations": ("https://example.test/reference",),
        }
    )

    text = _format_terminal_output(output)

    assert "Was the primary source available?" in text
    assert "- Was the primary source available?" in text
    assert "[未核实] The available source was secondary" not in text
    assert "- Was the primary source available?: The available source was secondary." not in text
    assert "A reference work discusses the claim" not in text
    assert "来源: [1]" in text
    assert "置信度" not in text
    assert "Obtain the primary text" in text
    assert "The required primary source was unavailable." in text
    assert text.index("\n限制:") < text.index("\n来源:\n")
    assert "[1] https://example.test/reference" in text


def test_terminal_output_renders_frozen_task_results() -> None:
    output = MappingProxyType(
        {
            "task_results": (
                MappingProxyType(
                    {
                        "question": "Legacy research question",
                        "result": "Legacy result remains readable.",
                    }
                ),
            ),
        }
    )

    assert _format_terminal_output(output) == (
        "- Legacy research question: Legacy result remains readable."
    )


def test_terminal_failed_red() -> None:
    renderer, console = _renderer()
    response = {
        "run_id": "r",
        "thread_id": "t",
        "status": "failed",
        "output": None,
        "pending_approval": None,
        "error": {"code": "boom", "message": "fail"},
    }
    event = {
        "event_type": "terminal",
        "run_id": "r",
        "sequence": 5,
        "payload": {"response": response},
        "terminal_response": response,
    }

    result = renderer.render_event(event)

    assert result == response
    text = console.export_text(styles=False)
    assert "✗" in text
    assert "failed" in text
    assert "fail" in text


def test_terminal_interrupted_yellow() -> None:
    renderer, console = _renderer()
    response = {
        "run_id": "r",
        "thread_id": "t",
        "status": "interrupted",
        "output": None,
        "pending_approval": None,
        "error": None,
    }
    event = {
        "event_type": "terminal",
        "run_id": "r",
        "sequence": 5,
        "payload": {"response": response},
        "terminal_response": response,
    }

    renderer.render_event(event)

    text = console.export_text(styles=False)
    assert "⏸" in text
    assert "interrupted" in text


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("hello", 10, "hello"),
        ("hello", 5, "hello"),
        ("hello world", 5, "hello..."),
        ("", 10, ""),
    ],
)
def test_truncate_helper(text: str, limit: int, expected: str) -> None:
    assert _truncate(text, limit) == expected


def test_unknown_event_type_returns_none() -> None:
    renderer, console = _renderer()
    event = {
        "event_type": "policy_decision",
        "run_id": "r",
        "sequence": 9,
        "payload": {"foo": "bar"},
        "terminal_response": None,
    }

    result = renderer.render_event(event)

    assert result is None
    # Unknown events are silently ignored at this stage.
    assert console.export_text(styles=False) == ""


def test_task_progress_renderer_uses_canonical_task_events() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console, title="Research tasks")
    renderer.render_event(
        {
            "event_type": "task_plan_created",
            "payload": {
                "task_plan": {
                    "items": [
                        {"id": "one", "title": "Read source", "status": "pending", "summary": None},
                        {"id": "two", "title": "Write brief", "status": "pending", "summary": None},
                    ],
                    "current_action": None,
                    "last_activity": None,
                }
            },
        }
    )
    renderer.render_event(
        {
            "event_type": "task_started",
            "payload": {
                "task_plan": {
                    "items": [
                        {
                            "id": "one",
                            "title": "Read source",
                            "status": "in_progress",
                            "summary": None,
                        },
                        {"id": "two", "title": "Write brief", "status": "pending", "summary": None},
                    ],
                    "current_action": "Fetching pricing page",
                    "last_activity": None,
                }
            },
        }
    )

    text = console.export_text(styles=False)
    assert "Research tasks · 0/2" in text
    assert "○ Read source" in text
    assert "● Read source" in text
    assert "Fetching pricing page" in text


def test_task_progress_keeps_blocked_and_later_completed_history() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console, title="Research tasks")
    base = {
        "version": 1,
        "current_task_id": None,
        "current_action": None,
        "last_activity": "Source unavailable",
        "items": [
            {
                "id": "source",
                "title": "Read source",
                "status": "blocked",
                "summary": "Source unavailable",
            }
        ],
    }
    renderer.render_event({"event_type": "task_blocked", "payload": {"task_plan": base}})
    completed = {
        **base,
        "last_activity": "Replacement source read",
        "items": [
            {
                "id": "source",
                "title": "Read source",
                "status": "completed",
                "summary": "Replacement source read",
            }
        ],
    }
    renderer.render_event({"event_type": "task_completed", "payload": {"task_plan": completed}})

    text = console.export_text(styles=False)
    assert "! Read source  Source unavailable" in text
    assert "✓ Read source  Replacement source read" in text
    assert text.count("Source unavailable") == 1
    assert text.count("Replacement source read") == 1


def test_task_progress_renders_finalization_and_repair_activity() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console, title="Research tasks")
    renderer.render_event(
        {
            "event_type": "task_plan_created",
            "payload": {
                "task_plan": {
                    "items": [
                        {"id": "one", "title": "Research", "status": "completed", "summary": "Done"}
                    ],
                    "current_action": None,
                    "last_activity": "Done",
                }
            },
        }
    )
    renderer.render_event({"event_type": "finalization_started", "payload": {}})
    renderer.render_event({"event_type": "output_repair_started", "payload": {}})

    text = console.export_text(styles=False)
    assert "正在生成最终结果" in text
    assert "正在修复输出格式" in text


def test_deep_research_renderer_distinguishes_received_and_applied_steering() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {
            "event_type": "workflow_selected",
            "payload": {"workflow_id": "deep_research"},
        }
    )
    renderer.render_event({"event_type": "user_steering_received", "payload": {}})
    renderer.render_event({"event_type": "user_steering_applied", "payload": {}})

    text = console.export_text(styles=False)
    assert "反馈已收到" in text
    assert "方向已应用" in text
