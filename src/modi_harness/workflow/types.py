"""Canonical Workflow definition records.

These frozen records are the validated runtime projection of author-provided
YAML. They intentionally keep only Workflow and Node as addressable definition
objects; completion, capabilities, and limits are flattened Node fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

WorkflowExecution = Literal["operation", "autonomous"]
CompletionReview = Literal["none", "required"]

WORKFLOW_COMPLETE = "$complete"
WORKFLOW_FAIL = "$fail"
WORKFLOW_TERMINALS = frozenset({WORKFLOW_COMPLETE, WORKFLOW_FAIL})


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
    "CompletionReview",
    "Node",
    "Workflow",
    "WorkflowExecution",
]
