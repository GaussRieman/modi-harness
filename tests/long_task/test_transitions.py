"""Authoritative Task Graph transition tests."""

from __future__ import annotations

import pytest

from modi_harness.long_task import (
    LeaseRecord,
    TaskAttempt,
    TransitionError,
    transition_attempt,
    transition_graph,
    transition_task,
)

from .helpers import binding, graph, task


def test_task_wait_resume_verify_complete_path() -> None:
    value = transition_task(task("work"), "waiting")
    value = transition_task(value, "running")
    value = transition_task(value, "verifying")
    value = transition_task(value, "completed", output_refs=("artifact://result",))

    assert value.status == "completed"
    assert value.output_refs == ("artifact://result",)
    with pytest.raises(TransitionError, match="illegal Task transition"):
        transition_task(value, "running")


def test_attempt_supports_prelaunch_and_submitted_cancellation() -> None:
    base = TaskAttempt(
        attempt_id="attempt-1",
        task_ref=task("work").ref,
        status="created",
        executor_binding=binding(),
        context_manifest_ref="context://1",
        completion_contract_hash="sha256:contract",
        dispatch_key="dispatch-1",
        lease=LeaseRecord("scheduler", 1, "token", "later"),
        parent_execution_contract_fingerprint="sha256:root",
    )
    assert transition_attempt(base, "cancelled").status == "cancelled"

    submitted = transition_attempt(transition_attempt(transition_attempt(base, "leased"), "running"), "submitted")
    assert transition_attempt(submitted, "cancelled").status == "cancelled"


def test_graph_goal_verification_path_is_closed() -> None:
    value = transition_graph(graph(task("work")), "verifying")
    value = transition_graph(value, "waiting")
    value = transition_graph(value, "verifying")
    value = transition_graph(value, "completed")
    assert value.status == "completed"
    with pytest.raises(TransitionError):
        transition_graph(value, "active")
