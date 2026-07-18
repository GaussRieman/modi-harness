"""Pure bounded Task scheduler and exclusive resource lock tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from modi_harness.long_task.resources import (
    ResourcePathError,
    canonical_resource_paths,
    exclusive_path_conflicts,
    resource_paths_conflict,
    resource_sets_conflict,
)
from modi_harness.long_task.scheduler import (
    SchedulerPolicy,
    SchedulerPolicyError,
    deterministic_ready_tasks,
    occupied_slot_count,
    schedule_ready_tasks,
)
from modi_harness.long_task.types import (
    AttemptStatus,
    ExecutorBinding,
    LeaseRecord,
    TaskAttempt,
    TaskRun,
)

from .helpers import graph, task, with_status


def _attempt(
    attempt_id: str,
    *,
    template_id: str = "build-v1",
    status: AttemptStatus = "running",
    resources: tuple[str, ...] = (),
    retiring: bool = False,
) -> TaskAttempt:
    return TaskAttempt(
        attempt_id=attempt_id,
        task_ref=task(f"task-{attempt_id}").ref,
        status=status,
        executor_binding=ExecutorBinding("child_agent", template_id, f"sha256:{template_id}"),
        context_manifest_ref=f"context://{attempt_id}",
        completion_contract_hash="sha256:contract",
        dispatch_key=f"dispatch-{attempt_id}",
        lease=LeaseRecord(
            "scheduler",
            1,
            f"token-{attempt_id}",
            "2026-07-18T12:00:00+00:00",
            resource_keys=resources,
            retiring=retiring,
        ),
        parent_execution_contract_fingerprint="sha256:root",
    )


def _with_template(value: TaskRun, template_id: str) -> TaskRun:
    selected = ExecutorBinding("child_agent", template_id, f"sha256:{template_id}")
    return replace(
        value,
        executor_policy=replace(
            value.executor_policy,
            allowed_bindings=(selected,),
            preferred_binding=selected,
        ),
    )


def test_ready_order_is_stable_across_input_order_and_mixed_dag() -> None:
    done = with_status(task("done"), "completed")
    high_b = task("high-b", depends_on=(done.ref,), priority=90)
    high_a = task("high-a", depends_on=(done.ref,), priority=90)
    low = task("low", priority=10)
    blocked = task("blocked", depends_on=(high_a.ref,), priority=100)

    first = deterministic_ready_tasks(graph(low, blocked, high_b, done, high_a))
    second = deterministic_ready_tasks(graph(high_a, done, high_b, blocked, low))

    assert [item.task_id for item in first] == ["high-a", "high-b", "low"]
    assert [item.task_id for item in second] == ["high-a", "high-b", "low"]


def test_scheduler_applies_global_limit_with_deterministic_first_fit() -> None:
    work = graph(
        task("third", priority=10),
        task("first", priority=90),
        task("second", priority=50),
    )

    batch = schedule_ready_tasks(work, (), SchedulerPolicy(max_concurrency=2))

    assert [item.task_id for item in batch.selected] == ["first", "second"]
    assert [(item.task_ref.id, item.reason) for item in batch.blocked] == [
        ("third", "global_limit")
    ]
    assert batch.occupied_slots == 0


def test_scheduler_policy_cannot_exceed_persisted_graph_limit() -> None:
    work = graph(*(task(f"task-{index}") for index in range(6)))

    batch = schedule_ready_tasks(work, (), SchedulerPolicy(max_concurrency=100))

    assert len(batch.selected) == work.limits.max_concurrency == 4
    assert len(batch.blocked) == 2
    assert {item.reason for item in batch.blocked} == {"global_limit"}


def test_active_and_retiring_attempts_both_consume_global_slots() -> None:
    attempts = (
        _attempt("active"),
        _attempt("retiring", status="cancelled", retiring=True),
        _attempt("stopped", status="cancelled"),
    )

    batch = schedule_ready_tasks(
        graph(task("new")), attempts, SchedulerPolicy(max_concurrency=2)
    )

    assert occupied_slot_count(attempts) == 2
    assert batch.selected == ()
    assert batch.blocked[0].reason == "global_limit"


def test_scheduler_enforces_per_template_limit_without_blocking_other_template() -> None:
    browser = _with_template(task("browser", priority=90), "browser-worker")
    code = _with_template(task("code", priority=80), "code-worker")
    attempts = (_attempt("existing", template_id="browser-worker"),)

    batch = schedule_ready_tasks(
        graph(browser, code),
        attempts,
        SchedulerPolicy(
            max_concurrency=3,
            per_template_limits={"browser-worker": 1, "code-worker": 2},
        ),
    )

    assert [item.task_id for item in batch.selected] == ["code"]
    assert [(item.task_ref.id, item.reason) for item in batch.blocked] == [
        ("browser", "template_limit")
    ]


def test_scheduler_serializes_active_and_same_batch_resource_conflicts() -> None:
    parent = task("parent", priority=90)
    child = task("child", priority=80)
    independent = task("independent", priority=70)
    attempts = (_attempt("writer", resources=("/workspace/reports",)),)

    batch = schedule_ready_tasks(
        graph(parent, child, independent),
        attempts,
        SchedulerPolicy(max_concurrency=4),
        resource_paths_by_task={
            parent.ref: ("/workspace/reports/annual",),
            child.ref: ("/workspace/source",),
            independent.ref: ("/workspace/source/api",),
        },
    )

    assert [item.task_id for item in batch.selected] == ["child"]
    assert [(item.task_ref.id, item.reason) for item in batch.blocked] == [
        ("parent", "resource_conflict"),
        ("independent", "resource_conflict"),
    ]
    assert batch.blocked[0].conflicting_attempt_ids == ("writer",)
    assert batch.blocked[1].conflicting_attempt_ids == ()


def test_resource_paths_are_canonical_and_component_aware() -> None:
    assert canonical_resource_paths(
        (" /workspace/reports/../reports/annual ", "/workspace/reports/annual")
    ) == ("/workspace/reports/annual",)
    assert resource_paths_conflict("/workspace/reports", "/workspace/reports/annual")
    assert not resource_paths_conflict("/workspace/report", "/workspace/reports")
    assert resource_sets_conflict(("/workspace/a",), ("/workspace/a/b",))
    with pytest.raises(ResourcePathError, match="absolute"):
        canonical_resource_paths(("workspace/relative",))


def test_terminal_attempt_releases_lock_but_retiring_attempt_keeps_it() -> None:
    requested = ("/workspace/output/file",)
    stopped = _attempt("stopped", status="cancelled", resources=("/workspace/output",))
    retiring = _attempt(
        "retiring",
        status="cancelled",
        resources=("/workspace/output",),
        retiring=True,
    )

    assert exclusive_path_conflicts(requested, (stopped,)) == ()
    conflicts = exclusive_path_conflicts(requested, (retiring,))
    assert len(conflicts) == 1
    assert conflicts[0].holder_attempt_id == "retiring"
    assert conflicts[0].holder_retiring is True


@pytest.mark.parametrize("limit", [0, -1, True])
def test_scheduler_rejects_invalid_limits(limit: int) -> None:
    with pytest.raises(SchedulerPolicyError, match="positive integer"):
        SchedulerPolicy(max_concurrency=limit)
