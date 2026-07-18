"""Child checkpoint identity, dispatch idempotency, and recovery tests."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from modi_harness.long_task import (
    ChildAuditEvent,
    ChildCheckpointConflict,
    ChildDispatcher,
    ChildDispatchError,
    ChildLaunchCoordinator,
    ChildRunBinding,
    ChildRunError,
    ContextManifest,
    ContextManifestError,
    DispatchObservation,
    InMemoryChildCheckpointStore,
    InMemoryChildExecutorBackend,
    ManifestAuthority,
    ManifestBudgets,
    PinnedChildTemplate,
    PinnedChildTemplateRegistry,
    PinnedChildWorkflowBackend,
    ResolvedChildTemplate,
    SqliteChildCheckpointStore,
    child_checkpoint_namespace,
    child_workspace_partition,
    initial_child_snapshot,
    prepare_child_run,
)
from modi_harness.workspace import WorkspaceManager, WorkspaceRunMissingError


def _manifest(**changes: object) -> ContextManifest:
    values = {
        "context_id": "context/attempt-1",
        "root_run_id": "root-1",
        "parent_run_id": "root-1",
        "parent_node_id": "execute/goal",
        "parent_node_attempt": 2,
        "task_attempt_id": "attempt-1",
        "child_run_id": "child-1",
        "template_id": "worker",
        "template_fingerprint": "sha256:template",
        "child_workflow_fingerprint": "sha256:workflow",
        "child_execution_contract_fingerprint": "sha256:contract",
        "intent": {
            "intent_id": "intent-1",
            "version": 1,
            "binding_hash": "sha256:intent",
            "goal": "Do the work",
            "desired_outcome": "Verified result",
            "relevant_criteria": [],
        },
        "task": {
            "ref": {"kind": "task", "id": "task-1", "revision": 1},
            "goal": "Do task-1",
            "completion_contract": {"output_schema_id": "result-v1"},
            "constraints": [],
            "non_goals": [],
            "assumptions": [],
        },
        "dependencies": (),
        "inputs": {"artifact_refs": [], "evidence_refs": [], "memory_refs": []},
        "authority": ManifestAuthority((), (), (), ("workspace://child-1",), {}),
        "budgets": ManifestBudgets(20, 900),
    }
    values.update(changes)
    return ContextManifest(**values)  # type: ignore[arg-type]


def _binding(**changes: object) -> ChildRunBinding:
    manifest = _manifest()
    values = {
        "root_run_id": "root-1",
        "parent_run_id": "root-1",
        "parent_node_id": "execute/goal",
        "parent_node_attempt": 2,
        "parent_attempt_id": "attempt-1",
        "child_run_id": "child-1",
        "template_id": "worker",
        "template_fingerprint": "sha256:template",
        "dispatch_key": "dispatch-1",
        "context_manifest_ref": "blob://sha256/context",
        "context_manifest_fingerprint": manifest.fingerprint,
        "parent_execution_contract_fingerprint": "sha256:parent-contract",
        "child_workflow_fingerprint": "sha256:workflow",
        "child_execution_contract_fingerprint": "sha256:contract",
        "lease_epoch": 1,
        "lease_token": "lease-1",
        "checkpoint_ns": child_checkpoint_namespace(
            root_run_id="root-1",
            parent_node_id="execute/goal",
            parent_node_attempt=2,
            parent_attempt_id="attempt-1",
            child_run_id="child-1",
        ),
        "workspace_partition": child_workspace_partition("root-1", "child-1"),
    }
    values.update(changes)
    return ChildRunBinding(**values)  # type: ignore[arg-type]


def test_child_namespace_is_deterministic_encoded_and_collision_free() -> None:
    first = _binding()
    second = _binding(
        child_run_id="child-2",
        checkpoint_ns=child_checkpoint_namespace(
            root_run_id="root-1",
            parent_node_id="execute/goal",
            parent_node_attempt=2,
            parent_attempt_id="attempt-1",
            child_run_id="child-2",
        ),
        workspace_partition=child_workspace_partition("root-1", "child-2"),
    )
    retried = _binding(
        parent_node_attempt=3,
        checkpoint_ns=child_checkpoint_namespace(
            root_run_id="root-1",
            parent_node_id="execute/goal",
            parent_node_attempt=3,
            parent_attempt_id="attempt-1",
            child_run_id="child-1",
        ),
    )

    assert "%2F" in first.checkpoint_ns
    assert len({first.checkpoint_ns, second.checkpoint_ns, retried.checkpoint_ns}) == 3


def test_child_checkpoint_create_is_idempotent_and_binding_closed() -> None:
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    store = InMemoryChildCheckpointStore()

    assert store.create_or_load(initial) is initial
    assert store.create_or_load(initial) is initial
    changed = _binding(dispatch_key="dispatch-changed")
    with pytest.raises(ChildCheckpointConflict, match="binding mismatch"):
        store.create_or_load(initial_child_snapshot(changed, _manifest()))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"root_run_id": "other-root", "parent_run_id": "other-root"},
            "parent binding mismatch",
        ),
        ({"parent_node_id": "other-node"}, "parent binding mismatch"),
        ({"parent_node_attempt": 3}, "parent binding mismatch"),
        ({"task_attempt_id": "other-attempt"}, "Attempt mismatch"),
        ({"child_run_id": "other-child"}, "child run mismatch"),
        ({"template_id": "other-template"}, "template ID mismatch"),
        ({"template_fingerprint": "sha256:other-template"}, "template fingerprint mismatch"),
        ({"child_workflow_fingerprint": "sha256:other-workflow"}, "Workflow binding mismatch"),
        (
            {"child_execution_contract_fingerprint": "sha256:other-contract"},
            "Workflow binding mismatch",
        ),
    ],
)
def test_child_checkpoint_rejects_nonreciprocal_binding(
    changes: dict[str, object],
    message: str,
) -> None:
    if "task_attempt_id" in changes:
        changes = {**changes, "context_id": f"context/{changes['task_attempt_id']}"}
    manifest = _manifest(**changes)
    binding = replace(
        _binding(),
        context_manifest_fingerprint=manifest.fingerprint,
        fingerprint="",
    )
    with pytest.raises(ChildRunError, match=message):
        initial_child_snapshot(binding, manifest)


def test_v1_child_parent_must_be_the_root_run() -> None:
    with pytest.raises(ContextManifestError, match="parent run must be the root run"):
        _manifest(parent_run_id="other-parent")
    with pytest.raises(ChildRunError, match="parent run must be the root run"):
        _binding(
            parent_run_id="other-parent",
            workspace_partition=child_workspace_partition("other-parent", "child-1"),
        )


def test_child_checkpoint_cas_and_json_round_trip() -> None:
    initial = initial_child_snapshot(_binding(), _manifest())
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial)
    event = ChildAuditEvent("event-1", "child_started", 1, {"handle_id": "handle-1"})
    committed = store.compare_and_swap(
        initial.binding.checkpoint_ns,
        expected_revision=0,
        snapshot=replace(
            initial,
            revision=1,
            status="running",
            launch_handle_id="handle-1",
        ),
        event=event,
    )

    restored = type(committed).from_snapshot(json.loads(json.dumps(committed.snapshot())))
    assert restored == committed
    assert store.list_by_root("root-1") == (committed,)
    with pytest.raises(ChildCheckpointConflict, match="stale child revision"):
        store.compare_and_swap(
            initial.binding.checkpoint_ns,
            expected_revision=0,
            snapshot=replace(committed, revision=1),
            event=event,
        )


def test_sqlite_child_checkpoint_survives_reopen(tmp_path) -> None:
    path = tmp_path / "children.db"
    initial = initial_child_snapshot(_binding(), _manifest())
    first = SqliteChildCheckpointStore(path)
    first.create_or_load(initial)
    first.close()

    restored = SqliteChildCheckpointStore(path)
    assert restored.load(initial.binding.checkpoint_ns) == initial
    assert restored.load_by_child_run_id("child-1") == initial
    assert restored.list_by_root("root-1") == (initial,)
    restored.close()


def test_sqlite_binding_conflict_rolls_back_and_store_remains_usable(tmp_path) -> None:
    path = tmp_path / "children.db"
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    store = SqliteChildCheckpointStore(path)
    store.create_or_load(initial)
    changed = _binding(dispatch_key="dispatch-changed")

    with pytest.raises(ChildCheckpointConflict, match="binding mismatch"):
        store.create_or_load(initial_child_snapshot(changed, _manifest()))

    assert store.load(binding.checkpoint_ns) == initial
    event = ChildAuditEvent("event-1", "child_started", 1, {"handle_id": "handle-1"})
    committed = store.compare_and_swap(
        binding.checkpoint_ns,
        expected_revision=0,
        snapshot=replace(
            initial,
            revision=1,
            status="running",
            launch_handle_id="handle-1",
        ),
        event=event,
    )
    assert committed.revision == 1
    with pytest.raises(ChildCheckpointConflict, match="stale child revision"):
        store.compare_and_swap(
            binding.checkpoint_ns,
            expected_revision=0,
            snapshot=committed,
            event=event,
        )
    assert store.load(binding.checkpoint_ns) == committed
    waiting = store.compare_and_swap(
        binding.checkpoint_ns,
        expected_revision=1,
        snapshot=replace(committed, revision=2, status="waiting"),
        event=ChildAuditEvent("event-2", "child_waiting", 2, {}),
    )
    assert waiting.revision == 2
    store.close()


def test_sqlite_child_run_id_collision_rolls_back(tmp_path) -> None:
    path = tmp_path / "children.db"
    initial = initial_child_snapshot(_binding(), _manifest())
    store = SqliteChildCheckpointStore(path)
    store.create_or_load(initial)
    colliding_manifest = _manifest(parent_node_attempt=3)
    colliding_binding = _binding(
        parent_node_attempt=3,
        context_manifest_fingerprint=colliding_manifest.fingerprint,
        checkpoint_ns=child_checkpoint_namespace(
            root_run_id="root-1",
            parent_node_id="execute/goal",
            parent_node_attempt=3,
            parent_attempt_id="attempt-1",
            child_run_id="child-1",
        ),
    )

    with pytest.raises(ChildCheckpointConflict, match="identity already exists"):
        store.create_or_load(initial_child_snapshot(colliding_binding, colliding_manifest))

    assert store.load(initial.binding.checkpoint_ns) == initial
    assert store.load(colliding_binding.checkpoint_ns) is None
    other_manifest = _manifest(child_run_id="child-2")
    other_binding = _binding(
        child_run_id="child-2",
        context_manifest_fingerprint=other_manifest.fingerprint,
        checkpoint_ns=child_checkpoint_namespace(
            root_run_id="root-1",
            parent_node_id="execute/goal",
            parent_node_attempt=2,
            parent_attempt_id="attempt-1",
            child_run_id="child-2",
        ),
        workspace_partition=child_workspace_partition("root-1", "child-2"),
    )
    other = initial_child_snapshot(other_binding, other_manifest)
    assert store.create_or_load(other) == other
    store.close()


def test_prepare_child_run_creates_checkpoint_and_isolated_workspace(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path / "workspace")
    manager.create_run("root-1")
    store = InMemoryChildCheckpointStore()

    first, workspace = prepare_child_run(
        checkpoints=store,
        workspace=manager,
        binding=_binding(),
        manifest=_manifest(),
    )
    second, restored_workspace = prepare_child_run(
        checkpoints=store,
        workspace=manager,
        binding=_binding(),
        manifest=_manifest(),
    )
    workspace.save_draft("child-1", "result.json", {"ok": True})

    assert first == second
    assert restored_workspace.index_workspace("child-1")[0]["run_id"] == "child-1"
    assert (
        tmp_path / "workspace" / "root-1" / "sub" / "child-1" / "drafts" / "result.json"
    ).is_file()


def test_prepare_child_run_missing_parent_leaves_no_checkpoint(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path / "workspace")
    store = InMemoryChildCheckpointStore()
    binding = _binding()

    with pytest.raises(WorkspaceRunMissingError):
        prepare_child_run(
            checkpoints=store,
            workspace=manager,
            binding=binding,
            manifest=_manifest(),
        )

    assert store.load(binding.checkpoint_ns) is None


def test_ensure_started_is_idempotent_and_recovers_lost_acknowledgement() -> None:
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial)
    backend = InMemoryChildExecutorBackend(lambda _binding: "handle-1")
    backend.start(binding)
    coordinator = ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(backend),
    )

    first = coordinator.ensure_started(binding)
    second = coordinator.ensure_started(binding)

    assert first == second
    assert first.handle_id == "handle-1"
    assert backend.start_count == 1
    checkpoint = store.load(binding.checkpoint_ns)
    assert checkpoint is not None
    assert checkpoint.status == "running"
    assert checkpoint.launch_handle_id == "handle-1"


def test_active_child_without_dispatch_observation_fails_closed() -> None:
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial)
    first_backend = InMemoryChildExecutorBackend(lambda _binding: "handle-1")
    ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(first_backend),
    ).ensure_started(binding)
    restarted_backend = InMemoryChildExecutorBackend(lambda _binding: "handle-2")
    restarted = ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(restarted_backend),
    )

    with pytest.raises(ChildDispatchError, match="no reconcilable executor"):
        restarted.ensure_started(binding)

    assert restarted_backend.start_count == 0
    checkpoint = store.load(binding.checkpoint_ns)
    assert checkpoint is not None
    assert checkpoint.launch_handle_id == "handle-1"


@pytest.mark.parametrize(
    ("observation", "message"),
    [
        (
            DispatchObservation(
                dispatch_key="dispatch-1",
                binding_fingerprint=_binding().fingerprint,
                child_run_id="child-1",
                checkpoint_ns=_binding().checkpoint_ns,
                handle_id="handle-1",
                status="terminal",
            ),
            "observation is 'terminal'",
        ),
        (
            DispatchObservation(
                dispatch_key="dispatch-1",
                binding_fingerprint=_binding().fingerprint,
                child_run_id="child-1",
                checkpoint_ns=_binding().checkpoint_ns,
                handle_id="handle-2",
                status="running",
            ),
            "launch handle changed",
        ),
    ],
)
def test_active_child_rejects_conflicting_dispatch_observation(
    observation: DispatchObservation,
    message: str,
) -> None:
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial)
    store.compare_and_swap(
        binding.checkpoint_ns,
        expected_revision=0,
        snapshot=replace(
            initial,
            revision=1,
            status="running",
            launch_handle_id="handle-1",
        ),
        event=ChildAuditEvent("event-started", "child_started", 1, {}),
    )
    backend = _StaticExecutorBackend(observation)
    coordinator = ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(backend),
    )

    with pytest.raises(ChildDispatchError, match=message):
        coordinator.ensure_started(binding)

    assert backend.start_count == 0


def test_created_child_rejects_terminal_executor_observation() -> None:
    binding = _binding()
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial_child_snapshot(binding, _manifest()))
    backend = _StaticExecutorBackend(
        DispatchObservation(
            dispatch_key=binding.dispatch_key,
            binding_fingerprint=binding.fingerprint,
            child_run_id=binding.child_run_id,
            checkpoint_ns=binding.checkpoint_ns,
            handle_id="handle-1",
            status="terminal",
        )
    )
    coordinator = ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(backend),
    )

    with pytest.raises(ChildDispatchError, match="terminal executor requires"):
        coordinator.ensure_started(binding)

    assert backend.start_count == 0


def test_waiting_child_reconciliation_preserves_waiting_checkpoint() -> None:
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial)
    backend = InMemoryChildExecutorBackend(lambda _binding: "handle-1")
    coordinator = ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(backend),
    )
    coordinator.ensure_started(binding)
    running = store.load(binding.checkpoint_ns)
    assert running is not None
    waiting = store.compare_and_swap(
        binding.checkpoint_ns,
        expected_revision=running.revision,
        snapshot=replace(running, revision=running.revision + 1, status="waiting"),
        event=ChildAuditEvent("event-waiting", "child_waiting", 2, {}),
    )

    observation = coordinator.ensure_started(binding)

    assert observation.handle_id == "handle-1"
    assert store.load(binding.checkpoint_ns) == waiting


def test_dispatch_rejects_missing_checkpoint_or_binding_mismatch() -> None:
    binding = _binding()
    backend = InMemoryChildExecutorBackend(lambda _binding: "handle-1")
    coordinator = ChildLaunchCoordinator(
        checkpoints=InMemoryChildCheckpointStore(),
        dispatcher=ChildDispatcher(backend),
    )
    with pytest.raises(ChildDispatchError, match="checkpoint must exist"):
        coordinator.ensure_started(binding)
    assert backend.start_count == 0

    backend.start(binding)
    changed = _binding(template_fingerprint="sha256:changed")
    with pytest.raises(ChildDispatchError, match="another child definition"):
        ChildDispatcher(backend).ensure_started(changed)
    assert backend.start_count == 1


def test_orphan_child_is_cancelled_and_never_rebound() -> None:
    binding = _binding()
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial_child_snapshot(binding, _manifest()))
    backend = InMemoryChildExecutorBackend(lambda _binding: "handle-1")
    coordinator = ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(backend),
    )
    coordinator.ensure_started(binding)

    orphan = coordinator.mark_orphaned(binding, reason="parent Attempt missing")

    assert orphan.status == "orphaned"
    assert backend.inspect(binding.dispatch_key).status == "cancelled"  # type: ignore[union-attr]
    with pytest.raises(ChildDispatchError, match="cannot be dispatched"):
        coordinator.ensure_started(binding)


@pytest.mark.parametrize(
    "status",
    ["completed", "failed", "cancelled", "orphaned", "reconciliation_required"],
)
def test_terminal_child_checkpoint_cannot_be_restarted(status: str) -> None:
    binding = _binding()
    initial = initial_child_snapshot(binding, _manifest())
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial)
    terminal = store.compare_and_swap(
        binding.checkpoint_ns,
        expected_revision=0,
        snapshot=replace(initial, revision=1, status=status),  # type: ignore[arg-type]
        event=ChildAuditEvent("event-terminal", "child_terminal", 1, {}),
    )
    backend = InMemoryChildExecutorBackend(lambda _binding: "handle-1")
    coordinator = ChildLaunchCoordinator(
        checkpoints=store,
        dispatcher=ChildDispatcher(backend),
    )

    with pytest.raises(ChildDispatchError, match="cannot be dispatched"):
        coordinator.ensure_started(binding)

    assert backend.start_count == 0
    assert store.load(binding.checkpoint_ns) == terminal


def test_pinned_child_backend_executes_exact_workflow_without_routing() -> None:
    binding = _binding()
    definition = {
        "template": {"id": "worker"},
        "child_agent": {"definition": {"name": "worker-agent"}},
        "child_workflow": {
            "definition": {"id": "execute"},
            "fingerprint": "sha256:workflow",
        },
        "child_execution_contract": {
            "snapshot": {"protocol_version": "workflow-v1"},
            "fingerprint": "sha256:contract",
        },
    }
    pinned = PinnedChildTemplate.from_snapshot("worker", definition)
    registry = PinnedChildTemplateRegistry()
    executable = ResolvedChildTemplate(
        pinned=pinned,
        agent=object(),
        workflow=object(),
        execution_contract=object(),
    )
    registry.register(pinned, executable)
    exact_manifest = _manifest(template_fingerprint=pinned.fingerprint)
    exact_binding = replace(
        binding,
        template_fingerprint=pinned.fingerprint,
        context_manifest_fingerprint=exact_manifest.fingerprint,
        fingerprint="",
    )
    store = InMemoryChildCheckpointStore()
    store.create_or_load(initial_child_snapshot(exact_binding, exact_manifest))
    calls = []

    def runner(resolved, run_binding, manifest):
        calls.append((resolved, run_binding, manifest))
        return "handle-exact"

    backend = PinnedChildWorkflowBackend(
        checkpoints=store,
        templates=registry,
        template_snapshots={
            "worker": {
                "id": "worker",
                "fingerprint": pinned.fingerprint,
                "definition": definition,
            }
        },
        runner=runner,
    )

    observation = ChildDispatcher(backend).ensure_started(exact_binding)

    assert observation.handle_id == "handle-exact"
    assert calls == [(executable, exact_binding, exact_manifest)]
    assert backend.start_count == 1


class _StaticExecutorBackend:
    def __init__(self, observation: DispatchObservation) -> None:
        self.observation = observation
        self.start_count = 0

    def inspect(self, dispatch_key: str) -> DispatchObservation | None:
        assert dispatch_key == self.observation.dispatch_key
        return self.observation

    def start(self, binding: ChildRunBinding) -> DispatchObservation:
        del binding
        self.start_count += 1
        return self.observation

    def cancel(self, dispatch_key: str, *, lease_token: str) -> DispatchObservation:
        del dispatch_key, lease_token
        return self.observation
