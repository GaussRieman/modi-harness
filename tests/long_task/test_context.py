"""ContextManifest isolation, authority, and content-addressing tests."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from modi_harness.long_task import (
    ContextManifest,
    ContextManifestError,
    DependencyContext,
    IntentCriterion,
    IntentVersion,
    build_context_manifest,
)

from .helpers import task


def _intent() -> IntentVersion:
    return IntentVersion(
        intent_id="intent-1",
        version=2,
        status="confirmed",
        goal="Build the service",
        desired_outcome="A verified service",
        success_criteria=(
            IntentCriterion("criterion-1", "It works", True, "validator", "goal-v1"),
            IntentCriterion("criterion-2", "It is documented", False, "validator"),
        ),
        constraints=("No external writes",),
        non_goals=("No redesign",),
        assumptions=("Dependencies are stable",),
    )


def _manifest(**changes: object) -> ContextManifest:
    dependency = task("dependency", revision=3, status="completed")
    target = replace(
        task("target", depends_on=(dependency.ref,)),
        intent_version=2,
        supports=("criterion-1",),
    )
    values = {
        "root_run_id": "root-1",
        "parent_run_id": "root-1",
        "parent_node_id": "execute-goal",
        "parent_node_attempt": 2,
        "task_attempt_id": "attempt-1",
        "child_run_id": "child-1",
        "template_id": "worker",
        "template_fingerprint": "sha256:template",
        "child_workflow_fingerprint": "sha256:workflow",
        "child_execution_contract_fingerprint": "sha256:contract",
        "intent": _intent(),
        "task": target,
        "dependencies": (
            DependencyContext(
                ref=dependency.ref,
                result_summary="Dependency completed",
                artifact_refs=("artifact://dependency",),
            ),
        ),
        "artifact_refs": ("artifact://input",),
        "evidence_refs": ("evidence://one",),
        "memory_refs": ("memory://scoped",),
        "parent_adapters": ("search", "write"),
        "template_adapters": ("search", "write", "extra"),
        "workflow_adapters": ("search",),
        "task_adapters": ("search", "write"),
        "current_policy_adapters": ("search",),
        "parent_capabilities": ("network", "workspace"),
        "template_capabilities": ("network",),
        "workflow_capabilities": ("network",),
        "current_policy_capabilities": ("network", "other"),
        "readable_scope_sets": (
            ("artifact://input", "artifact://dependency"),
            ("artifact://input",),
        ),
        "writable_scope_sets": (
            ("workspace://child-1", "workspace://other"),
            ("workspace://child-1",),
        ),
        "parent_permission_profile": {
            "mode": "auto",
            "preauthorized": ["search", "write"],
            "deny": ["blocked-parent"],
            "review_required": [],
        },
        "template_permission_profile": {
            "mode": "trust",
            "preauthorized": ["search"],
            "deny": ["blocked-child"],
            "review_required": ["write"],
        },
        "current_permission_mode": "preview",
        "max_steps": 20,
        "timeout_seconds": 900,
    }
    values.update(changes)
    return build_context_manifest(**values)  # type: ignore[arg-type]


def test_context_manifest_is_canonical_isolated_and_round_trips() -> None:
    manifest = _manifest()
    snapshot = manifest.snapshot()
    restored = ContextManifest.from_snapshot(json.loads(json.dumps(snapshot)))

    assert restored == manifest
    assert restored.fingerprint == manifest.fingerprint
    assert snapshot["context_id"] == "context/attempt-1"
    assert snapshot["intent"]["relevant_criteria"] == [
        {
            "id": "criterion-1",
            "description": "It works",
            "required": True,
            "verification_mode": "validator",
            "validator_id": "goal-v1",
        }
    ]
    assert [item["ref"]["id"] for item in snapshot["dependencies"]] == ["dependency"]
    assert set(snapshot) == {
        "schema_version",
        "context_id",
        "root_run_id",
        "parent_run_id",
        "parent_node_id",
        "parent_node_attempt",
        "task_attempt_id",
        "child_run_id",
        "template_id",
        "template_fingerprint",
        "child_workflow_fingerprint",
        "child_execution_contract_fingerprint",
        "intent",
        "task",
        "dependencies",
        "inputs",
        "authority",
        "budgets",
        "fingerprint",
    }
    assert "messages" not in snapshot
    assert "graph" not in snapshot
    assert "transcript" not in snapshot


def test_context_manifest_authority_can_only_narrow() -> None:
    authority = _manifest().authority

    assert authority.adapters == ("search",)
    assert authority.capabilities == ("network",)
    assert authority.readable_scopes == ("artifact://input",)
    assert authority.writable_scopes == ("workspace://child-1",)
    assert authority.permission_profile == {
        "mode": "preview",
        "preauthorized": ("search",),
        "deny": ("blocked-child", "blocked-parent"),
        "review_required": ("write",),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("child_execution_contract_fingerprint", "sha256:changed"),
        ("max_steps", 19),
        ("current_policy_adapters", ()),
        ("artifact_refs", ("artifact://different",)),
    ],
)
def test_context_manifest_fingerprint_changes_with_attempt_binding(
    field: str,
    value: object,
) -> None:
    assert _manifest(**{field: value}).fingerprint != _manifest().fingerprint


def test_context_manifest_rejects_non_direct_dependency_or_unconfirmed_intent() -> None:
    with pytest.raises(ContextManifestError, match="exactly direct dependencies"):
        _manifest(dependencies=())
    with pytest.raises(ContextManifestError, match="confirmed Intent"):
        _manifest(intent=replace(_intent(), status="draft"))


def test_context_manifest_rejects_absolute_host_paths() -> None:
    with pytest.raises(ContextManifestError, match="absolute host paths"):
        _manifest(memory_refs=("/Users/example/secret.md",))


def test_context_manifest_nested_values_are_immutable() -> None:
    manifest = _manifest()
    with pytest.raises(TypeError):
        manifest.intent["goal"] = "changed"  # type: ignore[index]


def test_context_manifest_rejects_parent_state_fields_even_with_valid_fingerprint() -> None:
    manifest = _manifest()
    with pytest.raises(ContextManifestError, match=r"intent.*unknown messages"):
        replace(
            manifest,
            intent={**dict(manifest.intent), "messages": ["parent transcript"]},
            fingerprint="",
        )


@pytest.mark.parametrize("field", ["messages", "graph"])
def test_context_manifest_rejects_unknown_root_fields(field: str) -> None:
    snapshot = _manifest().snapshot()
    snapshot[field] = {"secret": "parent state"}

    with pytest.raises(ContextManifestError, match=rf"manifest.*unknown {field}"):
        ContextManifest.from_snapshot(snapshot)


def test_context_manifest_rejects_unknown_nested_snapshot_fields() -> None:
    snapshot = _manifest().snapshot()
    snapshot["authority"]["messages"] = ["parent transcript"]

    with pytest.raises(ContextManifestError, match=r"authority.*unknown messages"):
        ContextManifest.from_snapshot(snapshot)
