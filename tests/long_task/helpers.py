"""Focused fixtures for pure Task Graph state tests."""

from __future__ import annotations

from dataclasses import replace

from modi_harness.long_task import (
    CompletionContract,
    DependencyRef,
    ExecutorBinding,
    ExecutorPolicy,
    GraphLimits,
    TaskGraphRun,
    TaskRun,
)


def binding() -> ExecutorBinding:
    return ExecutorBinding("operation", "build-v1", "sha256:build")


def task(
    task_id: str,
    *,
    revision: int = 1,
    depends_on: tuple[DependencyRef, ...] = (),
    status: str = "pending",
    kind: str = "executable",
    priority: int = 50,
    supports: tuple[str, ...] = ("criterion-1",),
) -> TaskRun:
    selected = binding()
    return TaskRun(
        task_id=task_id,
        task_revision=revision,
        graph_id="graph-1",
        intent_version=1,
        intent_binding_hash="sha256:intent",
        intent_binding_state="current",
        goal=f"Do {task_id}",
        supports=supports,
        depends_on=depends_on,
        priority=priority,
        required=True,
        kind=kind,  # type: ignore[arg-type]
        completion_contract=CompletionContract("result-v1", ("task-v1",)),
        executor_policy=ExecutorPolicy((selected,), selected),
        status=status,  # type: ignore[arg-type]
    )


def graph(*tasks: TaskRun, revision: int = 1) -> TaskGraphRun:
    return TaskGraphRun(
        graph_id="graph-1",
        intent_id="intent-1",
        intent_version=1,
        revision=revision,
        status="active" if revision else "planning",
        limits=GraphLimits(20, 8, 5, 4, 10),
        required_criteria=("criterion-1",),
        tasks=tuple(tasks),
        active_task_refs=tuple(item.ref for item in tasks),
    )


def with_status(value: TaskRun, status: str) -> TaskRun:
    return replace(value, status=status)  # type: ignore[arg-type]
