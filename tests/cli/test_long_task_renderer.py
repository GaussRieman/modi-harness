"""Task Graph-specific CLI progress projection tests."""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from rich.console import Console
from rich.panel import Panel

from modi_harness.cli.renderer import TaskProgressRenderer


def _plan(
    items: list[dict[str, Any]],
    *,
    graph_status: str = "active",
    human_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "task_graph",
        "graph_id": "graph-1",
        "version": 7,
        "graph_status": graph_status,
        "items": items,
        "current_task_id": None,
        "current_action": None,
        "last_activity": "task_graph_updated",
        "current_human_request": human_request,
    }


def _event(event_type: str, plan: dict[str, Any]) -> dict[str, Any]:
    return {"event_type": event_type, "payload": {"task_plan": plan}}


def _render_text(renderable: Any) -> str:
    output = StringIO()
    Console(file=output, width=160, force_terminal=False).print(renderable)
    return output.getvalue()


def test_task_graph_updates_one_stable_live_panel(
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
            self.renderable = renderable
            self.updates.append((renderable, refresh))

        def stop(self) -> None:
            self.stopped += 1

    monkeypatch.setattr("modi_harness.cli.renderer.Live", FakeLive)
    renderer = TaskProgressRenderer(Console(force_terminal=True, width=160))
    initial = _plan(
        [
            {"id": "research", "title": "Research", "status": "pending"},
            {"id": "write", "title": "Write", "status": "pending"},
        ]
    )
    running = _plan(
        [
            {"id": "research", "title": "Research", "status": "in_progress"},
            {"id": "write", "title": "Write", "status": "pending"},
        ]
    )

    renderer.render_event(_event("task_plan_created", initial))
    renderer.render_event(_event("task_started", running))
    renderer.render_event(_event("task_started", running))

    assert len(live_instances) == 1
    live = live_instances[0]
    assert live.started == 1
    assert len(live.updates) == 2
    assert isinstance(live.renderable, Panel)
    text = _render_text(live.renderable)
    assert "Task Graph · 0/2" in text
    assert "● Research" in text
    assert text.count("Research") == 1


def test_scope_review_and_task_graph_progress_are_not_rendered_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_instances: list[Any] = []

    class FakeLive:
        def __init__(self, renderable: Any, **kwargs: Any) -> None:
            self.renderable = renderable
            self.updates: list[Any] = []
            live_instances.append(self)

        def start(self) -> None:
            return None

        def update(self, renderable: Any, *, refresh: bool = False) -> None:
            del refresh
            self.renderable = renderable
            self.updates.append(renderable)

        def stop(self) -> None:
            return None

    monkeypatch.setattr("modi_harness.cli.renderer.Live", FakeLive)
    console = Console(record=True, force_terminal=True, width=160)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {"event_type": "workflow_selected", "payload": {"workflow_id": "deep_research"}}
    )
    interaction = {
        "event_type": "interaction_requested",
        "payload": {
            "interaction_id": "scope-1",
            "kind": "node_review",
            "payload": {
                "draft": {
                    "subject": "Tesla Model Y vs 小米 YU7",
                    "research_question": "Compare the two vehicles",
                    "task_plan": {
                        "items": [
                            {"id": "specs", "title": "Specifications"},
                            {"id": "price", "title": "Pricing"},
                        ]
                    },
                }
            },
        },
    }

    renderer.render_event(interaction)
    renderer.render_event(interaction)
    renderer.resume_after_prompt(interaction["payload"], "approved")
    progress = _plan(
        [
            {"id": "specs", "title": "Specifications", "status": "in_progress"},
            {"id": "price", "title": "Pricing", "status": "pending"},
        ]
    )
    renderer.render_event(_event("task_plan_created", progress))
    renderer.render_event(_event("task_started", progress))

    text = console.export_text(styles=False)
    assert text.count("Research scope") == 1
    assert text.count("主体: Tesla Model Y vs 小米 YU7") == 1
    assert len(live_instances) == 1
    assert len(live_instances[0].updates) == 1


def test_scope_review_renders_intent_candidate_dimensions() -> None:
    console = Console(record=True, force_terminal=False, width=160)
    renderer = TaskProgressRenderer(console)
    renderer.render_event(
        {"event_type": "workflow_selected", "payload": {"workflow_id": "deep_research"}}
    )

    renderer.render_event(
        {
            "event_type": "interaction_requested",
            "payload": {
                "interaction_id": "intent-review-1",
                "kind": "node_review",
                "payload": {
                    "draft": {
                        "goal": "Compare Tesla Model Y and Xiaomi YU7",
                        "constraints": [
                            "Use current public sources",
                            "Do not infer unavailable specifications",
                        ],
                        "planning_context": {
                            "subject": "Tesla Model Y vs 小米 YU7",
                            "research_question": "Which vehicle better fits the user?",
                            "candidate_dimensions": [
                                {
                                    "id": "dimensions",
                                    "title": "Dimensions",
                                    "verification_method": "official_primary_required",
                                    "authority_bindings": [
                                        {
                                            "host": "tesla.com",
                                            "source_type": "official",
                                            "include_subdomains": True,
                                        }
                                    ],
                                },
                                {
                                    "id": "price",
                                    "question": "Price and configuration",
                                    "verification_method": "dual_independent_required",
                                    "authority_bindings": [],
                                },
                            ],
                        },
                    }
                },
            },
        }
    )

    text = console.export_text(styles=False)
    assert text.count("Research scope") == 1
    assert "Tesla Model Y vs 小米 YU7" in text
    assert "Dimensions" in text
    assert "Price and configuration" in text
    assert text.count("Use current public sources") == 1
    assert text.count("Do not infer unavailable specifications") == 1
    assert text.count("official_primary_required") == 1
    assert text.count("dual_independent_required") == 1
    assert text.count("official: tesla.com (含子域名)") == 1


def test_task_graph_decodes_legacy_research_titles_without_exposing_json() -> None:
    console = Console(record=True, width=200, force_terminal=False)
    renderer = TaskProgressRenderer(console)
    research_task = {
        "schema_version": "research-task-goal-v1",
        "id": "dimensions",
        "title": "Compare vehicle dimensions",
        "question": "How do the dimensions differ?",
        "dimension": "dimensions",
    }
    legacy_goal = json.dumps(research_task, separators=(",", ":"))
    legacy_manifest = json.dumps(
        {"extensions": {"research_task": {**research_task, "title": "Compare pricing"}}},
        separators=(",", ":"),
    )

    renderer.render_event(
        _event(
            "task_started",
            _plan(
                [
                    {
                        "id": "dimensions",
                        "title": legacy_goal,
                        "status": "in_progress",
                        "child": {"run_id": "child-1", "status": "running", "revision": 2},
                    },
                    {"id": "pricing", "goal": legacy_manifest, "status": "pending"},
                ]
            ),
        )
    )

    text = console.export_text(styles=False)
    assert "Compare vehicle dimensions" in text
    assert "Compare pricing" in text
    assert "child-1 · running · r2" in text
    assert "research-task-goal-v1" not in text
    assert '"extensions"' not in text


def test_human_task_status_and_current_request_are_visible() -> None:
    console = Console(record=True, width=160, force_terminal=False)
    renderer = TaskProgressRenderer(console)
    waiting = _plan(
        [
            {
                "id": "approval",
                "title": "Publish result",
                "status": "waiting_human",
                "executor_mode": "human",
                "summary": "Waiting for human decision",
            }
        ],
        graph_status="waiting",
        human_request={
            "request_id": "judgment-1",
            "kind": "judgment",
            "prompt": "Approve production publish",
        },
    )

    renderer.render_event(_event("task_started", waiting))

    text = console.export_text(styles=False)
    assert "Task Graph" in text
    assert "Waiting for human decision" in text
    assert "Approve production publish" in text
    assert "judgment-1" in text


def test_cancelled_and_retiring_states_remain_distinct() -> None:
    console = Console(record=True, width=160, force_terminal=False)
    renderer = TaskProgressRenderer(console)
    cancelling = _plan(
        [
            {
                "id": "cancelled",
                "title": "Cancelled Task",
                "status": "cancelled",
                "summary": "Cancelled by user",
            },
            {
                "id": "retiring",
                "title": "Retiring worker",
                "status": "in_progress",
                "attempt_status": "cancelled",
                "retiring": True,
                "summary": "Cancellation requested; executor retiring",
                "child": {
                    "run_id": "child-7",
                    "status": "reconciliation_required",
                    "revision": 11,
                },
            },
        ],
        graph_status="cancelled",
    )

    renderer.render_event(_event("task_blocked", cancelling))

    text = console.export_text(styles=False).casefold()
    assert "cancelled by user" in text
    assert "executor retiring" in text
    assert "↻ retiring worker" in text
    assert "child-7 · reconciliation required · r11" in text
    assert "cancelled" in text
    assert "retiring" in text
