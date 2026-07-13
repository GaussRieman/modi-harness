"""Model-backed Brain planner control decisions."""

from __future__ import annotations

from typing import Any, cast

from modi_harness.brain.model import ModelStructuredPlanner
from modi_harness.loop import validate_step_decision
from modi_harness.loop.types import StepContext


class _ModelAdapter:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.pack: dict[str, Any] | None = None

    def call(self, pack: dict[str, Any]) -> dict[str, Any]:
        self.pack = pack
        return self.result


def _context() -> StepContext:
    return StepContext(
        step_id="step-1",
        node={
            "goal": "Understand the research request",
            "inputs": {"request": {"prompt": "hi"}},
            "completion": {
                "output_schema": {
                    "type": "object",
                    "required": ["research_question", "source_urls"],
                }
            },
        },
        available_capabilities={"tools": []},
    )


def test_model_planner_maps_request_user_input_to_structured_ask() -> None:
    model = _ModelAdapter(
        {
            "tool_calls": [
                {
                    "tool_name": "request_user_input",
                    "arguments": {
                        "prompt": "请提供研究问题和至少一个来源 URL。",
                        "field": "research_request",
                        "input_type": "multiline",
                        "required": True,
                    },
                }
            ]
        }
    )
    planner = ModelStructuredPlanner(
        model=cast(Any, model),
        instruction="Do not invent missing input.",
        tool_catalog={},
    )

    decision = planner.plan_structured_step(_context())

    validate_step_decision(decision)
    assert decision["step_kind"] == "clarify"
    assert decision["operation"] is None
    assert decision["continuation"] == "wait"
    assert decision["ask"] == {
        "prompt": "请提供研究问题和至少一个来源 URL。",
        "field": "research_request",
        "input_type": "multiline",
        "required": True,
    }
    assert model.pack is not None
    assert [item["name"] for item in model.pack["tool_descriptions"]] == [
        "request_user_input",
        "complete_node",
    ]


def test_model_planner_rejects_malformed_input_request() -> None:
    model = _ModelAdapter(
        {
            "tool_calls": [
                {
                    "tool_name": "request_user_input",
                    "arguments": {
                        "prompt": "Need input",
                        "field": "details",
                        "input_type": "file",
                    },
                }
            ]
        }
    )
    planner = ModelStructuredPlanner(
        model=cast(Any, model),
        instruction="",
        tool_catalog={},
    )

    import pytest

    with pytest.raises(ValueError, match="unsupported input_type"):
        planner.plan_structured_step(_context())


def test_model_planner_serializes_multiple_operation_proposals() -> None:
    model = _ModelAdapter(
        {
            "tool_calls": [
                {
                    "tool_name": "search",
                    "arguments": {"query": "first"},
                },
                {
                    "tool_name": "search",
                    "arguments": {"query": "second"},
                },
            ]
        }
    )
    planner = ModelStructuredPlanner(
        model=cast(Any, model),
        instruction="",
        tool_catalog={
            "search": {
                "name": "search",
                "description": "Search once",
                "input_schema": {"type": "object"},
            }
        },
    )
    context = _context()
    context["available_capabilities"] = {"tools": ["search"]}

    decision = planner.plan_structured_step(context)

    validate_step_decision(decision)
    assert decision["operation"] is not None
    assert decision["operation"]["target"] == "search"
    assert decision["operation"]["arguments"] == {"query": "first"}
    assert "deferred 1 additional proposal" in decision["reason"]
