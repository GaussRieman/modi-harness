"""Tests for AgentLoop scoped to an autonomous Workflow Node."""

from __future__ import annotations

import pytest

from modi_harness.brain import DefaultBrain, StaticStructuredPlanner
from modi_harness.loop import (
    AgentLoop,
    initialize_loop_state,
    planner_step_decision,
    project_recent_steps_for_brain,
    validate_step_decision,
)
from modi_harness.loop.types import AutonomousNodeContext, StepDecision, StepValidationError


def _state(*, max_auto_steps: int = 5):
    return initialize_loop_state(
        workflow_run_id="run-1",
        workflow_id="research",
        node_id="investigate",
        node_attempt=1,
        agent_name="researcher",
        intent_version=1,
        max_auto_steps=max_auto_steps,
    )


def _node() -> AutonomousNodeContext:
    return AutonomousNodeContext(
        goal="Find the root cause",
        inputs={"complaint_id": "c-1"},
        completion={"validator": "valid_investigation"},
    )


def test_loop_state_has_required_workflow_scope() -> None:
    state = _state()

    assert state["workflow_run_id"] == "run-1"
    assert state["node_id"] == "investigate"
    assert "run_id" not in state


def test_loop_construction_requires_complete_node_scope() -> None:
    state = _state()
    state["node_id"] = ""
    brain = DefaultBrain(StaticStructuredPlanner(planner_step_decision(step_id="x")))

    with pytest.raises(ValueError, match="node_id"):
        AgentLoop(state=state, brain=brain)


def test_prepare_step_builds_node_scoped_context_and_record() -> None:
    brain = DefaultBrain(StaticStructuredPlanner(planner_step_decision(step_id="ignored")))
    loop = AgentLoop(state=_state(), brain=brain)

    prepared = loop.prepare_step(
        step_id="step-1",
        node=_node(),
        event=None,
        intent={},
        intent_clarity={},
        autonomy_scope={},
        agent_profile={"name": "researcher", "instruction": "Investigate carefully."},
        recent_steps=[],
        available_capabilities={"tools": ["search"]},
        task_plan={"items": []},
    )

    assert prepared["context"]["node"]["goal"] == "Find the root cause"
    assert prepared["record"]["workflow_id"] == "research"
    assert prepared["record"]["node_attempt"] == 1


def test_project_recent_steps_bounds_outputs_preserves_control_and_urls() -> None:
    long_excerpt = "evidence " * 2_000
    steps = [
        {
            "step_id": "step-1",
            "loop_id": "loop-1",
            "workflow_run_id": "run-1",
            "workflow_id": "research",
            "node_id": "investigate",
            "node_attempt": 1,
            "index": 2,
            "step_kind": "act",
            "status": "completed",
            "intent_version": 1,
            "input_event_id": None,
            "decision": {
                "operation": {
                    "target": "public_web_search",
                    "arguments": {"task_id": "dim-ke", "searches": ["x"]},
                }
            },
            "operation_ref": "op-1",
            "operation_result_ref": "blob-1",
            "state_delta": {
                "human_input": "reset-marker",
                "operation_output": {
                    "search_ids": ["search-1", "search-2"],
                    "sources": [
                        {
                            "url": f"https://example.test/{index}",
                            "content": long_excerpt,
                        }
                        for index in range(30)
                    ],
                },
            },
            "postcheck_result": None,
            "started_at": "now",
            "finished_at": "later",
        }
    ]

    result = project_recent_steps_for_brain(steps)[0]

    assert result["index"] == 2
    assert result["decision"]["operation"]["arguments"]["task_id"] == "dim-ke"
    assert result["state_delta"]["human_input"] == "reset-marker"
    output = result["state_delta"]["operation_output"]
    assert output["search_ids"] == ["search-1", "search-2"]
    assert [item["url"] for item in output["sources"]] == [
        f"https://example.test/{index}" for index in range(30)
    ]
    assert output["sources"][0]["content"]["original_fingerprint"]


def test_complete_node_proposal_returns_control_to_workflow_runtime() -> None:
    decision = planner_step_decision(step_id="step-1")
    decision["step_kind"] = "verify"
    decision["operation"] = {
        "kind": "workflow_control",
        "summary": "complete current Node",
        "target": "complete_node",
        "arguments": {"result": {"root_cause": "x"}},
        "expected_outcome": "Harness validates completion",
    }
    decision["continuation"] = "wait"
    decision["continuation_basis"] = None
    loop = AgentLoop(
        state=_state(),
        brain=DefaultBrain(StaticStructuredPlanner(decision)),
    )
    prepared = loop.prepare_step(
        step_id="step-1",
        node=_node(),
        event=None,
        intent={},
        intent_clarity={},
        autonomy_scope={},
        agent_profile={"name": "researcher"},
        recent_steps=[],
        available_capabilities={},
    )

    completed = loop.complete_step(prepared["record"])

    assert completed["continuation"]["outcome"] == "node_completion_proposed"
    assert completed["loop"]["status"] == "active"


def test_step_failure_and_budget_exhaustion_fail_node_loop() -> None:
    decision = planner_step_decision(step_id="step-1")
    brain = DefaultBrain(StaticStructuredPlanner(decision))
    loop = AgentLoop(state=_state(max_auto_steps=1), brain=brain)
    prepared = loop.prepare_step(
        step_id="step-1",
        node=_node(),
        event=None,
        intent={},
        intent_clarity={},
        autonomy_scope={},
        agent_profile={"name": "researcher"},
        recent_steps=[],
        available_capabilities={},
    )

    completed = loop.complete_step(prepared["record"])

    assert completed["continuation"]["outcome"] == "fail"
    assert completed["loop"]["status"] == "failed"


def test_recoverable_step_failure_returns_control_to_brain() -> None:
    decision = planner_step_decision(step_id="step-1")
    loop = AgentLoop(
        state=_state(max_auto_steps=3),
        brain=DefaultBrain(StaticStructuredPlanner(decision)),
    )
    prepared = loop.prepare_step(
        step_id="step-1",
        node=_node(),
        event=None,
        intent={},
        intent_clarity={},
        autonomy_scope={},
        agent_profile={"name": "researcher"},
        recent_steps=[],
        available_capabilities={},
    )

    completed = loop.complete_step(
        prepared["record"],
        status="failed",
        state_delta={"operation_error": "invalid URL"},
    )

    assert completed["continuation"]["outcome"] == "continue"
    assert completed["continuation"]["blockers"] == ["step_failed"]
    assert completed["loop"]["status"] == "active"


def test_closed_validation_rejects_obsolete_fields_and_controls() -> None:
    decision = planner_step_decision(step_id="step-1")
    decision["legacy_mode"] = "removed"  # type: ignore[typeddict-unknown-key]
    with pytest.raises(StepValidationError, match="unsupported field"):
        validate_step_decision(decision)

    invalid = StepDecision(**planner_step_decision(step_id="step-2"))
    invalid["operation"] = {
        "kind": "workflow_control",
        "summary": "jump",
        "target": "jump_workflow",
        "arguments": {},
        "expected_outcome": None,
    }
    with pytest.raises(StepValidationError, match="complete_node"):
        validate_step_decision(invalid)
