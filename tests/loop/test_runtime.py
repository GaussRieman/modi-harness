"""Tests for AgentLoop scoped to an autonomous Workflow Node."""

from __future__ import annotations

import pytest

from modi_harness.brain import DefaultBrain, StaticStructuredPlanner
from modi_harness.loop import (
    AgentLoop,
    initialize_loop_state,
    planner_step_decision,
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


def test_loop_state_has_required_workflow_scope_and_no_stage() -> None:
    state = _state()

    assert state["workflow_run_id"] == "run-1"
    assert state["node_id"] == "investigate"
    assert "stage_id" not in state
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


def test_closed_validation_rejects_obsolete_fields_and_controls() -> None:
    decision = planner_step_decision(step_id="step-1")
    decision["reasoning_mode"] = "slow"  # type: ignore[typeddict-unknown-key]
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
