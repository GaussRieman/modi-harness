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


def _step(
    index: int,
    *,
    target: str | None = None,
    arguments: dict[str, Any] | None = None,
    state_delta: dict[str, Any] | None = None,
) -> Any:
    operation = None
    if target is not None:
        operation = {
            "kind": "tool",
            "summary": f"call {target}",
            "target": target,
            "arguments": arguments or {},
            "expected_outcome": "result",
        }
    return {
        "index": index,
        "decision": {"operation": operation},
        "state_delta": state_delta or {},
    }


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


def test_model_planner_wraps_flat_complete_node_arguments() -> None:
    flat_result = {
        "research_question": "灵西机器人",
        "source_urls": ["https://example.test/linx"],
    }
    model = _ModelAdapter(
        {
            "tool_calls": [
                {
                    "tool_name": "complete_node",
                    "arguments": flat_result,
                }
            ]
        }
    )
    planner = ModelStructuredPlanner(
        model=cast(Any, model),
        instruction="",
        tool_catalog={},
    )

    decision = planner.plan_structured_step(_context())

    assert decision["operation"] is not None
    assert decision["operation"]["target"] == "complete_node"
    assert decision["operation"]["arguments"] == {"result": flat_result}


def test_model_planner_recovers_empty_complete_node_arguments_from_content() -> None:
    model = _ModelAdapter(
        {
            "message": {
                "content": (
                    '{"research_question":"灵西机器人",'
                    '"source_urls":["https://example.test/linx"]}'
                )
            },
            "tool_calls": [{"tool_name": "complete_node", "arguments": {}}],
        }
    )
    planner = ModelStructuredPlanner(
        model=cast(Any, model),
        instruction="",
        tool_catalog={},
    )

    decision = planner.plan_structured_step(_context())

    assert decision["operation"] is not None
    assert decision["operation"]["arguments"] == {
        "result": {
            "research_question": "灵西机器人",
            "source_urls": ["https://example.test/linx"],
        }
    }


def test_model_planner_hides_tool_after_per_node_input_round_budget() -> None:
    model = _ModelAdapter(
        {
            "tool_calls": [
                {"tool_name": "complete_node", "arguments": {"result": {}}}
            ]
        }
    )
    planner = ModelStructuredPlanner(
        model=cast(Any, model),
        instruction="",
        tool_catalog={
            "search": {
                "name": "search",
                "description": "Search the Web",
                "input_schema": {"type": "object"},
                "max_calls_per_node": 4,
            }
        },
    )
    context = _context()
    context["available_capabilities"] = {"tools": ["search"]}
    context["recent_steps"] = [
        _step(index, target="search", arguments={"query": str(index)})
        for index in range(1, 5)
    ]

    planner.plan_structured_step(context)

    assert model.pack is not None
    assert [item["name"] for item in model.pack["tool_descriptions"]] == [
        "request_user_input",
        "complete_node",
    ]
    payload = model.pack["recent_messages"][0]["content"]
    assert '"exhausted_tools": ["search"]' in payload


def test_model_planner_resets_tool_budget_after_human_input() -> None:
    model = _ModelAdapter(
        {
            "tool_calls": [
                {"tool_name": "search", "arguments": {"query": "new company name"}}
            ]
        }
    )
    planner = ModelStructuredPlanner(
        model=cast(Any, model),
        instruction="",
        tool_catalog={
            "search": {
                "name": "search",
                "description": "Search the Web",
                "input_schema": {"type": "object"},
                "max_calls_per_node": 4,
            }
        },
    )
    context = _context()
    context["available_capabilities"] = {"tools": ["search"]}
    context["recent_steps"] = [
        *[
            _step(index, target="search", arguments={"query": str(index)})
            for index in range(1, 5)
        ],
        _step(5, state_delta={"human_input": "杭州拉格朗日"}),
    ]

    decision = planner.plan_structured_step(context)

    assert decision["operation"] is not None
    assert decision["operation"]["target"] == "search"


def test_model_planner_prefers_untried_proposal_fingerprint() -> None:
    model = _ModelAdapter(
        {
            "tool_calls": [
                {"tool_name": "search", "arguments": {"query": "first"}},
                {"tool_name": "search", "arguments": {"query": "second"}},
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
    context["recent_steps"] = [
        _step(1, target="search", arguments={"query": "first"})
    ]

    decision = planner.plan_structured_step(context)

    assert decision["operation"] is not None
    assert decision["operation"]["arguments"] == {"query": "second"}
