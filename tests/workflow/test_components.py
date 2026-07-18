"""Pinned Task Graph component contracts and replay records."""

from __future__ import annotations

import pytest

from modi_harness.workflow import (
    ComponentInvocationRecord,
    ComponentRegistryError,
    PinnedComponent,
    PinnedComponentRegistry,
)


def _component(*, digest: str = "sha256:impl-1") -> PinnedComponent:
    return PinnedComponent(
        id="goal-v1",
        version="1",
        kind="goal_verifier",
        implementation_digest=digest,
        protocol_version="verifier-v1",
        input_schema_id="goal-input-v1",
        output_schema_id="goal-output-v1",
        supported_outcomes=("passed", "repairable", "ambiguous", "terminal"),
        configuration={"strict": True},
        implementation=lambda value: {"outcome": "passed", "value": value},
    )


def test_component_snapshot_and_fingerprint_exclude_callable() -> None:
    component = _component()
    snapshot = component.snapshot()

    assert snapshot["id"] == "goal-v1"
    assert "implementation" not in snapshot
    assert component.fingerprint == snapshot["fingerprint"]


def test_registry_resolves_exact_snapshot_and_rejects_changed_digest() -> None:
    registry = PinnedComponentRegistry()
    component = _component()
    registry.register(component)

    assert registry.resolve_pinned(component.snapshot()) is component
    frozen_snapshot = dict(component.snapshot())
    frozen_snapshot["supported_outcomes"] = tuple(frozen_snapshot["supported_outcomes"])
    assert registry.resolve_pinned(frozen_snapshot) is component

    changed = dict(component.snapshot())
    changed["implementation_digest"] = "sha256:changed"
    with pytest.raises(ComponentRegistryError, match="unavailable"):
        registry.resolve_pinned(changed)


def test_registry_rejects_duplicates_and_kind_mismatch() -> None:
    registry = PinnedComponentRegistry()
    registry.register(_component())
    with pytest.raises(ComponentRegistryError, match="duplicate"):
        registry.register(_component())
    with pytest.raises(ComponentRegistryError, match="expected 'planner'"):
        registry.resolve("goal-v1", kind="planner")


def test_invocation_record_has_stable_input_hash_and_key() -> None:
    component = _component()
    record = ComponentInvocationRecord.prepared(
        root_run_id="root-1",
        component=component,
        idempotency_key="root-1/graph-1/goal-1",
        inputs={"criteria": ["a", "b"]},
    )

    assert record.status == "prepared"
    assert record.component_fingerprint == component.fingerprint
    assert record.input_hash
    assert record.idempotency_key == "root-1/graph-1/goal-1"


@pytest.mark.parametrize("kind", ["task_verifier", "group_verifier", "criterion_verifier"])
def test_non_human_components_require_callables(kind: str) -> None:
    with pytest.raises(ComponentRegistryError, match="requires a callable"):
        PinnedComponent(
            id="component",
            version="1",
            kind=kind,  # type: ignore[arg-type]
            implementation_digest="sha256:impl",
            protocol_version="v1",
            input_schema_id="input",
            output_schema_id="output",
            supported_outcomes=("passed",),
            configuration={},
        )
