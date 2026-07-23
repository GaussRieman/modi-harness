"""Explicit and model-assisted Agent-local Workflow selection."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .definition import WorkflowInstanceError, validate_instance, workflow_to_dict
from .types import Workflow


class WorkflowRoutingError(ValueError):
    """A caller's Workflow control selection is invalid."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        available_workflow_ids: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.available_workflow_ids = available_workflow_ids


@dataclass(frozen=True, slots=True)
class WorkflowRoute:
    workflow: Workflow
    workflow_input: dict[str, Any]
    strategy: Literal["explicit", "sole", "model"]


def select_workflow(
    workflows: Sequence[Workflow],
    workflow_id: str | None = None,
) -> Workflow:
    """Select explicitly or default the sole mandatory Workflow."""

    by_id: dict[str, Workflow] = {}
    for workflow in workflows:
        if workflow.id in by_id:
            available = tuple(sorted((*by_id, workflow.id)))
            raise WorkflowRoutingError(
                "workflow_duplicate",
                f"duplicate Workflow id {workflow.id!r}",
                available_workflow_ids=available,
            )
        by_id[workflow.id] = workflow
    available = tuple(sorted(by_id))

    if workflow_id is not None:
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise WorkflowRoutingError(
                "workflow_required",
                "workflow_id must be a non-empty string",
                available_workflow_ids=available,
            )
        selected_id = workflow_id.strip()
        try:
            return by_id[selected_id]
        except KeyError as exc:
            raise WorkflowRoutingError(
                "workflow_not_found",
                f"Workflow {selected_id!r} is not registered for this Agent",
                available_workflow_ids=available,
            ) from exc

    if not by_id:
        raise WorkflowRoutingError(
            "workflow_required",
            "Agent must declare at least one Workflow",
            available_workflow_ids=available,
        )
    if len(by_id) == 1:
        return next(iter(by_id.values()))
    raise WorkflowRoutingError(
        "workflow_required",
        "workflow_id is required when an Agent declares multiple Workflows",
        available_workflow_ids=available,
    )


def route_workflow(
    workflows: Sequence[Workflow],
    workflow_input: Mapping[str, Any],
    *,
    workflow_id: str | None,
    model: Any,
    agent_instruction: str,
) -> WorkflowRoute:
    """Select a Workflow and produce its validated input."""

    if workflow_id is not None:
        return WorkflowRoute(
            workflow=select_workflow(workflows, workflow_id),
            workflow_input=dict(workflow_input),
            strategy="explicit",
        )
    if len(workflows) <= 1:
        return WorkflowRoute(
            workflow=select_workflow(workflows),
            workflow_input=dict(workflow_input),
            strategy="sole",
        )

    by_tool = {f"route__{workflow.id}": workflow for workflow in workflows}
    descriptions: list[dict[str, Any]] = [
        {
            "name": tool_name,
            "description": workflow.description,
            "input_schema": workflow_to_dict(workflow)["input_schema"],
            "risk_level": "L0",
            "side_effect": False,
        }
        for tool_name, workflow in sorted(by_tool.items())
    ]
    request: dict[str, Any] = {
        "role": "user",
        "content": (
            "Choose exactly one declared Workflow for this Agent request. Follow each "
            "Workflow description and fill the selected Workflow input directly from "
            "the request. Preserve explicit requested depth: words such as thorough, "
            "deep, comprehensive, careful search, or due diligence must not be "
            "downgraded to a narrow lookup merely because there is one entity. Do not "
            "answer the request.\n\n"
            + json.dumps(dict(workflow_input), ensure_ascii=False, default=str)
        ),
        "tool_call_id": None,
        "metadata": {},
    }
    pack = {
        "system_instruction": (
            "You are the Agent Workflow Router. Select one declared Workflow; do not "
            "answer the user or call domain tools."
        ),
        "agent_instruction": agent_instruction,
        "skill_instructions": [],
        "memory_blocks": [],
        "references": [],
        "state_summary": "",
        "tool_descriptions": descriptions,
        "workspace_index": [],
        "recent_messages": [request],
        "output_requirement": None,
        "trust_annotations": [],
        "context_hash": "",
    }
    result = model.call(pack)
    calls = [
        call
        for call in result.get("tool_calls") or []
        if str(call.get("tool_name") or "") in by_tool
    ]
    if len(calls) != 1:
        raise WorkflowRoutingError(
            "workflow_route_invalid",
            "Agent Router must select exactly one declared Workflow",
            available_workflow_ids=tuple(sorted(workflow.id for workflow in workflows)),
        )
    call = calls[0]
    if call.get("malformed"):
        raise WorkflowRoutingError(
            "workflow_route_invalid",
            "Agent Router produced a malformed Workflow selection",
            available_workflow_ids=tuple(sorted(workflow.id for workflow in workflows)),
        )
    tool_name = str(call["tool_name"])
    selected = by_tool[tool_name]
    raw_arguments = call.get("arguments")
    if not isinstance(raw_arguments, Mapping):
        raise WorkflowRoutingError(
            "workflow_route_input_invalid",
            "Agent Router Workflow input must be an object",
            available_workflow_ids=tuple(sorted(workflow.id for workflow in workflows)),
        )
    routed_input = dict(raw_arguments)
    if selected.id == "quick_lookup" and _explicit_deep_research_requested(workflow_input):
        deep = next((item for item in workflows if item.id == "deep_research"), None)
        prompt = str(workflow_input.get("prompt") or "").strip()
        if deep is not None and prompt:
            properties = deep.input_schema.get("properties")
            allowed = set(properties) if isinstance(properties, Mapping) else {"request"}
            corrected_request = str(
                raw_arguments.get("question") or raw_arguments.get("subject") or prompt
            ).strip()
            routed_input = {
                "request": corrected_request,
                **{
                    key: value
                    for key, value in raw_arguments.items()
                    if key in allowed and key != "request"
                },
            }
            selected = deep
    try:
        validate_instance(
            selected.input_schema,
            routed_input,
            context=f"Workflow Router input for {selected.id!r}",
        )
    except WorkflowInstanceError as exc:
        raise WorkflowRoutingError(
            "workflow_route_input_invalid",
            str(exc),
            available_workflow_ids=tuple(sorted(workflow.id for workflow in workflows)),
        ) from exc
    return WorkflowRoute(
        workflow=selected,
        workflow_input=routed_input,
        strategy="model",
    )


def _explicit_deep_research_requested(workflow_input: Mapping[str, Any]) -> bool:
    prompt = str(workflow_input.get("prompt") or "").casefold()
    return bool(
        re.search(r"仔细(?:搜寻|搜索)|深入|全面|多方查证|尽调", prompt)
        or re.search(
            r"\b(?:thorough|comprehensive|in-depth|deep research|due diligence|careful search)\b",
            prompt,
        )
    )


__all__ = ["WorkflowRoute", "WorkflowRoutingError", "route_workflow", "select_workflow"]
