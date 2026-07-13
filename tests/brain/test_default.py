"""Tests for the single Brain implementation."""

from __future__ import annotations

import pytest

from modi_harness.brain import (
    BrainPlanningError,
    DefaultBrain,
    StaticStructuredPlanner,
)
from modi_harness.loop import initialize_loop_state
from modi_harness.loop.types import StepContext


def _context() -> StepContext:
    return StepContext(
        step_id="step-1",
        loop=initialize_loop_state(
            workflow_run_id="run-1",
            workflow_id="research",
            node_id="investigate",
            node_attempt=1,
            agent_name="researcher",
            intent_version=1,
            max_auto_steps=5,
        ),
    )


def _decision() -> dict:
    return {
        "id": "planner-id-is-overridden",
        "step_kind": "plan",
        "reason": "plan the investigation",
        "intent_patch": None,
        "ask": None,
        "operation": None,
        "expected_state_change": None,
        "postcheck": None,
        "continuation": "continue",
        "human_judgment": {
            "required": False,
            "reason": "within scope",
            "trigger": "none",
        },
        "continuation_basis": {
            "source": "planner",
            "reference": None,
            "reason": "continue planning",
        },
    }


def test_default_brain_returns_one_validated_decision() -> None:
    decision = DefaultBrain(StaticStructuredPlanner(_decision())).plan_step(_context())

    assert decision["id"] == "step-1"
    assert set(decision) == {
        "id",
        "step_kind",
        "reason",
        "intent_patch",
        "ask",
        "operation",
        "expected_state_change",
        "postcheck",
        "continuation",
        "human_judgment",
        "continuation_basis",
    }


class _RaisingPlanner:
    def plan_structured_step(self, _context: StepContext) -> dict:
        raise RuntimeError("provider unavailable")


def test_provider_failure_raises_brain_planning_error() -> None:
    with pytest.raises(BrainPlanningError, match="provider failed"):
        DefaultBrain(_RaisingPlanner()).plan_step(_context())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("legacy_mode", "removed"),
        ("legacy_rule", "removed"),
        ("continuation", "stop"),
        ("step_kind", "finish"),
    ],
)
def test_removed_decision_vocabulary_is_rejected(field: str, value: str) -> None:
    candidate = _decision()
    candidate[field] = value

    with pytest.raises(BrainPlanningError, match="invalid StepDecision"):
        DefaultBrain(StaticStructuredPlanner(candidate)).plan_step(_context())


def test_planner_rejects_unknown_operation_kinds() -> None:
    for kind in ("graph_control", "finalize_directly"):
        candidate = _decision()
        candidate["operation"] = {
            "kind": kind,
            "summary": "obsolete",
            "target": kind,
            "arguments": {},
            "expected_outcome": None,
        }
        with pytest.raises(BrainPlanningError, match="RuntimeOperation kind"):
            DefaultBrain(StaticStructuredPlanner(candidate)).plan_step(_context())
