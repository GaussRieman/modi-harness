"""Pure lease expiry, reconciliation, and renewal tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modi_harness.long_task.scheduler import (
    LeaseRenewalError,
    LeaseTimeError,
    assess_attempt_lease,
    reconciliation_action,
    renew_attempt_lease,
)
from modi_harness.long_task.types import (
    AttemptStatus,
    ExecutorBinding,
    LeaseRecord,
    TaskAttempt,
)

from .helpers import task

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _attempt(
    *,
    status: AttemptStatus = "running",
    expires_at: str = "2026-07-18T10:05:00+00:00",
    retiring: bool = False,
    resources: tuple[str, ...] = ("/workspace/output",),
) -> TaskAttempt:
    return TaskAttempt(
        attempt_id="attempt-1",
        task_ref=task("work").ref,
        status=status,
        executor_binding=ExecutorBinding("child_agent", "worker", "sha256:worker"),
        context_manifest_ref="context://1",
        completion_contract_hash="sha256:contract",
        dispatch_key="dispatch-1",
        lease=LeaseRecord(
            "root-1",
            3,
            "token-3",
            expires_at,
            resource_keys=resources,
            retiring=retiring,
        ),
        parent_execution_contract_fingerprint="sha256:root",
    )


def test_unexpired_lease_is_valid_and_expired_lease_is_only_suspect() -> None:
    valid = assess_attempt_lease(_attempt(), now=NOW)
    expired = assess_attempt_lease(
        _attempt(expires_at="2026-07-18T09:59:59Z"), now=NOW
    )

    assert (valid.state, valid.action) == ("valid", "none")
    assert (expired.state, expired.action) == ("suspect", "reconcile")
    assert "reconciliation" in expired.reason


def test_terminal_nonretiring_attempt_is_inactive_but_retiring_is_suspect() -> None:
    inactive = assess_attempt_lease(_attempt(status="cancelled"), now=NOW)
    retiring = assess_attempt_lease(
        _attempt(status="cancelled", retiring=True), now=NOW
    )

    assert inactive.state == "inactive"
    assert (retiring.state, retiring.action) == ("suspect", "reconcile")


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        ("live", "renew_same_attempt"),
        ("durably_resumable", "resume_same_attempt"),
        ("definitely_absent", "replace_and_release"),
        ("uncertain", "retain_and_reconcile"),
        ("side_effecting", "retain_and_reconcile"),
    ],
)
def test_expired_attempt_requires_observation_before_replacement(
    observation: str, expected: str
) -> None:
    expired = _attempt(expires_at="2026-07-18T09:00:00+00:00")

    assert reconciliation_action(expired, observation, now=NOW) == expected  # type: ignore[arg-type]


def test_retiring_attempt_releases_only_after_definite_absence() -> None:
    retiring = _attempt(status="cancelled", retiring=True)

    assert (
        reconciliation_action(retiring, "live", now=NOW)
        == "retain_and_reconcile"
    )
    assert (
        reconciliation_action(retiring, "definitely_absent", now=NOW)
        == "release_retiring"
    )


def test_renewal_preserves_epoch_token_and_advances_expiry() -> None:
    attempt = _attempt(expires_at="2026-07-18T10:01:00+00:00")

    renewed = renew_attempt_lease(
        attempt,
        now=NOW,
        ttl=timedelta(minutes=5),
        observed_dispatch_key="dispatch-1",
        verified_liveness=True,
        executor_checkpoint_active=True,
        graph_terminal=False,
        held_resource_paths=("/workspace/output",),
    )

    assert renewed.lease.expires_at == "2026-07-18T10:05:00+00:00"
    assert renewed.lease.epoch == attempt.lease.epoch
    assert renewed.lease.token == attempt.lease.token
    assert attempt.lease.expires_at == "2026-07-18T10:01:00+00:00"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"observed_dispatch_key": "wrong"}, "dispatch binding"),
        ({"verified_liveness": False}, "liveness"),
        ({"executor_checkpoint_active": False}, "checkpoint"),
        ({"graph_terminal": True}, "terminal graph"),
        ({"held_resource_paths": ()}, "resource locks"),
    ],
)
def test_renewal_rejects_missing_parent_proofs(
    changes: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "now": NOW,
        "ttl": timedelta(minutes=10),
        "observed_dispatch_key": "dispatch-1",
        "verified_liveness": True,
        "executor_checkpoint_active": True,
        "graph_terminal": False,
        "held_resource_paths": ("/workspace/output",),
    }
    arguments.update(changes)

    with pytest.raises(LeaseRenewalError, match=message):
        renew_attempt_lease(_attempt(), **arguments)  # type: ignore[arg-type]


def test_renewal_rejects_retiring_or_nonadvancing_lease() -> None:
    common = {
        "now": NOW,
        "observed_dispatch_key": "dispatch-1",
        "verified_liveness": True,
        "executor_checkpoint_active": True,
        "graph_terminal": False,
        "held_resource_paths": ("/workspace/output",),
    }
    with pytest.raises(LeaseRenewalError, match="non-retiring"):
        renew_attempt_lease(
            _attempt(retiring=True), ttl=timedelta(minutes=10), **common
        )
    with pytest.raises(LeaseRenewalError, match="advance"):
        renew_attempt_lease(_attempt(), ttl=timedelta(seconds=1), **common)


def test_lease_times_must_be_valid_and_timezone_aware() -> None:
    with pytest.raises(LeaseTimeError, match="ISO-8601"):
        assess_attempt_lease(_attempt(expires_at="later"), now=NOW)
    with pytest.raises(LeaseTimeError, match="timezone-aware"):
        assess_attempt_lease(_attempt(), now=NOW.replace(tzinfo=None))
