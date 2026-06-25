"""Tests for ``modi_harness.cli.renderer``.

Validates dispatch of stream events into rich console output and the side
return values used by the future REPL (approval payload, terminal response).
"""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console

from modi_harness.cli.renderer import (
    StreamRenderer,
    TaskProgressRenderer,
    WebagentWorkflowRenderer,
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


def test_protocol_tools_are_not_rendered_as_regular_tool_activity() -> None:
    renderer, console = _renderer()
    renderer.render_event({
        "event_type": "tool_call_proposal",
        "run_id": "r",
        "sequence": 1,
        "payload": {
            "tool_call_id": "ask-1",
            "tool_name": "request_user_input",
            "arguments": {"prompt": "Enter a URL"},
        },
        "terminal_response": None,
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "run_id": "r",
        "sequence": 2,
        "payload": {"tool_call_id": "ask-1", "content": "submitted"},
        "terminal_response": None,
    })

    assert console.export_text(styles=False) == ""


def test_submit_output_is_not_rendered_as_tool_activity() -> None:
    renderer, console = _renderer()
    renderer.render_event({
        "event_type": "tool_call_proposal",
        "run_id": "r",
        "sequence": 1,
        "payload": {
            "tool_call_id": "submit-1",
            "tool_name": "submit_output",
            "arguments": {"answer": "done"},
        },
        "terminal_response": None,
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "run_id": "r",
        "sequence": 2,
        "payload": {"tool_call_id": "submit-1", "content": "submitted"},
        "terminal_response": None,
    })

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
    renderer.render_event({
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
    })
    renderer.render_event({
        "event_type": "task_started",
        "payload": {
            "task_plan": {
                "items": [
                    {"id": "one", "title": "Read source", "status": "in_progress", "summary": None},
                    {"id": "two", "title": "Write brief", "status": "pending", "summary": None},
                ],
                "current_action": "Fetching pricing page",
                "last_activity": None,
            }
        },
    })

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
        "items": [{
            "id": "source",
            "title": "Read source",
            "status": "blocked",
            "summary": "Source unavailable",
        }],
    }
    renderer.render_event({"event_type": "task_blocked", "payload": {"task_plan": base}})
    completed = {
        **base,
        "last_activity": "Replacement source read",
        "items": [{
            "id": "source",
            "title": "Read source",
            "status": "completed",
            "summary": "Replacement source read",
        }],
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
    renderer.render_event({
        "event_type": "task_plan_created",
        "payload": {
            "task_plan": {
                "items": [{"id": "one", "title": "Research", "status": "completed", "summary": "Done"}],
                "current_action": None,
                "last_activity": "Done",
            }
        },
    })
    renderer.render_event({"event_type": "finalization_started", "payload": {}})
    renderer.render_event({"event_type": "output_repair_started", "payload": {}})

    text = console.export_text(styles=False)
    assert "正在生成最终结果" in text
    assert "正在修复输出格式" in text


def test_webagent_renderer_shows_workflow_over_raw_model_text() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = WebagentWorkflowRenderer(console)

    renderer.render_run_start("webagent")
    renderer.render_event({
        "event_type": "model_delta",
        "payload": {"delta": "这段模型碎碎念不应该出现"},
    })
    renderer.render_event({
        "event_type": "tool_call_proposal",
        "payload": {
            "tool_call_id": "parse",
            "tool_name": "parse_police_intake",
            "arguments": {"intake_path": "agents/modi-webagent/data/injection/intro.md"},
        },
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "payload": {
            "tool_call_id": "parse",
            "content": {
                "ok": True,
                "intake_path": "/repo/agents/modi-webagent/data/injection/intro.md",
                "url": "http://192.168.24.220:30101/",
                "fields": {
                    "报警人姓名": "李江",
                    "报警人联系电话": "18199987774",
                    "处警人员": "赵武,钱柳",
                    "警情地址": "诚高大厦6楼",
                    "报警内容描述": "我被我的同事周枫打了",
                    "警情类别": "行政(治安)类警情",
                    "警情类型": "侵犯人身权利",
                },
            },
        },
    })

    text = console.export_text(styles=False)
    assert "[webagent] 网页自动化" in text
    assert "应用" in text
    assert "警情录入" in text
    assert "流程" in text
    # checklist markers
    assert "✓ 读取警情文件" in text
    assert "○ 确认草稿" in text
    assert "○ 提交网页表单" in text
    assert "○ 保存证据" in text
    assert "草稿" in text
    assert "报警人: 李江" in text
    assert "go / 回车 / 确认: 提交录入" in text
    assert "模型碎碎念" not in text


def test_webagent_renderer_reprints_updated_draft_once() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = WebagentWorkflowRenderer(console)
    draft = {
        "intake_path": "/repo/intro.md",
        "url": "http://example.test/",
        "fields": {
            "报警人姓名": "李江",
            "报警人联系电话": "18199987774",
            "处警人员": "赵武,钱柳",
            "警情地址": "诚高大厦6楼",
            "报警内容描述": "我被我的同事周枫打了",
            "警情类别": "行政(治安)类警情",
            "警情类型": "侵犯人身权利",
        },
    }
    renderer.render_event({
        "event_type": "tool_call_proposal",
        "payload": {
            "tool_call_id": "parse",
            "tool_name": "parse_police_intake",
            "arguments": {"intake_path": "/repo/intro.md"},
        },
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "payload": {
            "tool_call_id": "parse",
            "content": {"ok": True, **draft},
        },
    })
    renderer.render_event({
        "event_type": "interaction_requested",
        "payload": {
            "interaction_id": "same-draft",
            "kind": "user_input",
            "payload": {"field": "draft_confirmation", "draft": draft},
        },
    })
    updated = {
        **draft,
        "fields": {
            **draft["fields"],
            "报警内容描述": "我被同事周枫打了,我要报警",
        },
    }
    renderer.render_event({
        "event_type": "interaction_requested",
        "payload": {
            "interaction_id": "updated-draft",
            "kind": "user_input",
            "payload": {"field": "draft_confirmation", "draft": updated},
        },
    })

    text = console.export_text(styles=False)
    assert text.count("草稿已更新") == 1
    assert "内容: 我被同事周枫打了,我要报警" in text
    # checklist: parse completed, confirm_draft stays in_progress (not yet confirmed)
    assert "✓ 读取警情文件" in text
    assert "● 确认草稿" in text


def test_webagent_renderer_summarizes_run_result() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = WebagentWorkflowRenderer(console)
    renderer.render_event({
        "event_type": "tool_call_proposal",
        "payload": {
            "tool_call_id": "run",
            "tool_name": "run_police_intake",
            "arguments": {"intake_path": "agents/modi-webagent/data/injection/intro.md"},
        },
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "payload": {
            "tool_call_id": "run",
            "content": (
                "{'ok': True, 'submitted': True, 'record_id': '', "
                "'evidence_dir': '/repo/runs/webagent-1', "
                "'trace_path': '/repo/runs/webagent-1/trace.json'}"
            ),
        },
    })

    text = console.export_text(styles=False)
    assert "✓ 提交网页表单" in text
    assert "提交网页表单: 已提交" in text
    assert "结果" in text
    assert "证据目录: /repo/runs/webagent-1" in text
    assert "Trace: /repo/runs/webagent-1/trace.json" in text
    # evidence present → save_evidence completed
    assert "✓ 保存证据" in text


def test_webagent_renderer_surfaces_repair_failure_details() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = WebagentWorkflowRenderer(console)

    renderer.render_event({
        "event_type": "output_repair_started",
        "payload": {
            "issues": [{
                "message": "could not parse output as JSON: Expecting value",
            }]
        },
    })
    renderer.render_event({
        "event_type": "error",
        "payload": {"code": "repair_budget_exhausted"},
    })
    renderer.render_event({
        "event_type": "terminal",
        "payload": {
            "response": {
                "status": "failed",
                "output": None,
                "error": None,
            }
        },
        "terminal_response": {
            "status": "failed",
            "output": None,
            "error": None,
        },
    })

    text = console.export_text(styles=False)
    assert "错误" in text
    assert "could not parse output as JSON" in text
    assert "repair_budget_exhausted" in text


# ------------------------------------------------------------------
# new live-checklist tests
# ------------------------------------------------------------------

def test_webagent_checklist_full_progression() -> None:
    """4 steps complete: parse → confirm → run (with evidence)."""
    console = Console(record=True, width=200, force_terminal=False)
    renderer = WebagentWorkflowRenderer(console)
    renderer.render_run_start("webagent")

    # --- parse ---
    renderer.render_event({
        "event_type": "tool_call_proposal",
        "payload": {"tool_call_id": "p", "tool_name": "parse_police_intake",
                    "arguments": {"intake_path": "intro.md"}},
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "payload": {"tool_call_id": "p",
                    "content": {"ok": True, "intake_path": "/repo/intro.md",
                                "url": "http://x/",
                                "fields": {"报警人姓名": "李江"}}},
    })

    # --- confirm draft ---
    renderer.render_event({
        "event_type": "interaction_requested",
        "payload": {"interaction_id": "i1", "kind": "user_input",
                    "payload": {"field": "draft_confirmation",
                                "draft": {"intake_path": "/repo/intro.md",
                                          "url": "http://x/", "fields": {}}}},
    })

    # --- run ---
    renderer.render_event({
        "event_type": "tool_call_proposal",
        "payload": {"tool_call_id": "r", "tool_name": "run_police_intake",
                    "arguments": {"fields": {}}},
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "payload": {"tool_call_id": "r",
                    "content": {"ok": True, "submitted": True,
                                "evidence_dir": "/runs/1",
                                "trace_path": "/runs/1/trace.json"}},
    })

    text = console.export_text(styles=False)
    assert "✓ 读取警情文件" in text
    assert "✓ 确认草稿" in text
    assert "✓ 提交网页表单" in text
    assert "✓ 保存证据" in text


def test_webagent_checklist_parse_failure() -> None:
    """Parse failure blocks remaining steps."""
    console = Console(record=True, width=200, force_terminal=False)
    renderer = WebagentWorkflowRenderer(console)
    renderer.render_run_start("webagent")

    renderer.render_event({
        "event_type": "tool_call_proposal",
        "payload": {"tool_call_id": "p", "tool_name": "parse_police_intake",
                    "arguments": {"intake_path": "bad.md"}},
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "payload": {"tool_call_id": "p",
                    "content": {"ok": False, "error": "file not found"}},
    })

    text = console.export_text(styles=False)
    assert "✗ 读取警情文件" in text
    assert "读取警情文件失败" in text
    # remaining steps are skipped
    assert "- 提交网页表单" in text
    assert "- 保存证据" in text


def test_webagent_checklist_no_evidence() -> None:
    """No evidence in run result → save_evidence skipped (dim)."""
    console = Console(record=True, width=200, force_terminal=False)
    renderer = WebagentWorkflowRenderer(console)

    # set up state directly — confirm completed, run about to start
    renderer._steps["parse_police_intake"] = "completed"
    renderer._steps["confirm_draft"] = "completed"

    renderer.render_event({
        "event_type": "tool_call_proposal",
        "payload": {"tool_call_id": "r", "tool_name": "run_police_intake",
                    "arguments": {"fields": {}}},
    })
    renderer.render_event({
        "event_type": "tool_call_result",
        "payload": {"tool_call_id": "r",
                    "content": {"ok": True, "submitted": True}},
    })

    text = console.export_text(styles=False)
    assert "✓ 提交网页表单" in text
    assert "- 保存证据" in text
