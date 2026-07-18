"""Canonical Workflow definition records.

These frozen records are the validated runtime projection of author-provided
YAML. They intentionally keep only Workflow and Node as addressable definition
objects; completion, capabilities, and limits are flattened Node fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

WorkflowExecution = Literal["operation", "autonomous", "task_graph"]
CompletionReview = Literal["none", "required"]

WORKFLOW_COMPLETE = "$complete"
WORKFLOW_FAIL = "$fail"
WORKFLOW_WAIT = "$wait"
WORKFLOW_TERMINALS = frozenset({WORKFLOW_COMPLETE, WORKFLOW_FAIL})


@dataclass(frozen=True, slots=True)
class TaskGraphLimits:
    """Hard deterministic limits for one Task Graph Node."""

    max_tasks: int
    max_graph_depth: int
    max_replans: int
    max_concurrency: int
    max_child_runs: int
    template_concurrency_limits: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class TaskGraphNodeConfig:
    """Immutable registry bindings for one Task Graph Node."""

    planner: str
    graph_policy: str
    context_builder: str
    task_validators: tuple[str, ...]
    group_validators: tuple[str, ...]
    criterion_validators: tuple[str, ...]
    goal_verifier: str
    operation_adapters: tuple[str, ...]
    parent_inline_components: tuple[str, ...]
    human_task_contracts: tuple[str, ...]
    child_templates: tuple[str, ...]
    limits: TaskGraphLimits


@dataclass(frozen=True, slots=True)
class Node:
    """One validated Workflow Node."""

    id: str
    execution: WorkflowExecution
    inputs: Mapping[str, Any]
    completion_output_schema: Mapping[str, Any] | None
    completion_validator: str | None
    completion_required: tuple[str, ...]
    completion_review: CompletionReview
    transitions: Mapping[str, str]
    operation: str | None = None
    goal: str | None = None
    capability_tools: tuple[str, ...] | None = None
    max_steps: int | None = None
    completion_output_schema_id: str | None = None
    completion_output_schema_version: str | None = None
    completion_output_schema_fingerprint: str | None = None
    task_graph: TaskGraphNodeConfig | None = None


@dataclass(frozen=True, slots=True)
class Workflow:
    """One validated Agent-local Workflow definition."""

    id: str
    description: str
    input_schema: Mapping[str, Any]
    start_node: str
    nodes: tuple[Node, ...]
    definition_fingerprint: str

    def node(self, node_id: str) -> Node:
        """Return a Node by ID, raising ``KeyError`` when it is absent."""

        for item in self.nodes:
            if item.id == node_id:
                return item
        raise KeyError(node_id)


__all__ = [
    "WORKFLOW_COMPLETE",
    "WORKFLOW_FAIL",
    "WORKFLOW_TERMINALS",
    "WORKFLOW_WAIT",
    "CompletionReview",
    "Node",
    "TaskGraphLimits",
    "TaskGraphNodeConfig",
    "Workflow",
    "WorkflowExecution",
]
