"""Deterministic Workflow selection."""

from __future__ import annotations

from collections.abc import Sequence

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


__all__ = ["WorkflowRoutingError", "select_workflow"]
