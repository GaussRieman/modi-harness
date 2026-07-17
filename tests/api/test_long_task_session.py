"""Public Session integration for the Operation-only Task Graph slice."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiAgent, ModiHarness, ModiSession, ToolBinding
from modi_harness._utils import compute_fingerprint
from modi_harness.checkpoint import InMemoryRootCheckpointStore, RootStoreConflict
from modi_harness.long_task import (
    CompletionContract,
    ExecutorBinding,
    ExecutorPolicy,
    GraphPatch,
    GraphPatchOperation,
    TaskRun,
)
from modi_harness.types import PermissionProfile
from modi_harness.workflow import (
    CompletionValidator,
    Node,
    PinnedComponent,
    TaskGraphLimits,
    TaskGraphNodeConfig,
    Workflow,
)

from .test_workflow_session import _CompleteModel


def _component(
    component_id: str,
    kind: str,
    implementation: Any,
    *,
    outcomes: tuple[str, ...] = ("passed",),
) -> PinnedComponent:
    def keyed(inputs: dict[str, Any], *, idempotency_key: str) -> Any:
        assert idempotency_key
        return implementation(inputs)

    return PinnedComponent(
        id=component_id,
        version="1",
        kind=kind,  # type: ignore[arg-type]
        implementation_digest=f"sha256:{component_id}",
        protocol_version="v1",
        input_schema_id=f"{component_id}-input",
        output_schema_id=f"{component_id}-output",
        supported_outcomes=outcomes,  # type: ignore[arg-type]
        configuration={},
        implementation=keyed,
    )


def _agent(calls: list[str]) -> ModiAgent:
    def reviewed_tool(question: str) -> dict[str, str]:
        calls.append(question)
        return {"answer": "approved"}

    def planner(inputs: dict[str, Any]) -> GraphPatch:
        adapter = inputs["allowed_operation_adapters"][0]
        binding = ExecutorBinding(
            "operation",
            adapter["id"],
            adapter["fingerprint"],
        )
        task = TaskRun(
            task_id="reviewed-task",
            task_revision=1,
            graph_id=inputs["graph"]["graph_id"],
            intent_version=inputs["intent"]["version"],
            intent_binding_hash=compute_fingerprint(inputs["intent"]),
            intent_binding_state="current",
            goal="Run the reviewed operation",
            supports=("criterion-1",),
            depends_on=(),
            priority=50,
            required=True,
            kind="executable",
            completion_contract=CompletionContract("reviewed-result", ("task-v1",)),
            executor_policy=ExecutorPolicy((binding,), binding),
        )
        return GraphPatch(
            base_revision=0,
            trigger="seed",
            reason="single reviewed Task",
            operations=(GraphPatchOperation("add_task", task=task),),
        )

    components = (
        _component("planner-v1", "planner", planner),
        _component("policy-v1", "graph_policy", lambda value: value),
        _component(
            "context-v1",
            "context_builder",
            lambda _inputs: {
                "context_manifest": {"scope": "reviewed-task"},
                "operation_arguments": {"question": "same proposal"},
            },
        ),
        _component("task-v1", "task_verifier", lambda _value: {"outcome": "passed"}),
        _component(
            "criterion-v1",
            "criterion_verifier",
            lambda _value: {"outcome": "passed"},
        ),
        _component("goal-v1", "goal_verifier", lambda _value: {"outcome": "passed"}),
    )
    config = TaskGraphNodeConfig(
        planner="planner-v1",
        graph_policy="policy-v1",
        context_builder="context-v1",
        task_validators=("task-v1",),
        group_validators=(),
        criterion_validators=("criterion-v1",),
        goal_verifier="goal-v1",
        operation_adapters=("reviewed_tool",),
        parent_inline_components=(),
        human_task_contracts=(),
        child_templates=(),
        limits=TaskGraphLimits(4, 2, 1, 1, 0),
    )
    workflow = Workflow(
        id="reviewed-long-task",
        description="Run one reviewed long Task.",
        input_schema={"type": "object", "required": ["intent"]},
        start_node="execute",
        nodes=(
            Node(
                id="execute",
                execution="task_graph",
                inputs={"intent": {"$ref": "#/workflow/input/intent"}},
                completion_output_schema={
                    "type": "object",
                    "properties": {"goal_verified": {"const": True}},
                    "required": ["goal_verified"],
                },
                completion_validator="node-result-v1",
                completion_required=("goal_verified",),
                completion_review="none",
                transitions={
                    "completed": "$complete",
                    "failed": "$fail",
                    "waiting": "$wait",
                },
                task_graph=config,
            ),
        ),
        definition_fingerprint="reviewed-long-task-fixture",
    )
    return ModiAgent(
        name="reviewed-long-task",
        description="reviewed long task",
        instruction="unused",
        workflows=(workflow,),
        completion_validators=(
            CompletionValidator(
                "node-result-v1",
                "1",
                lambda value: value.get("goal_verified") is True,
            ),
        ),
        task_graph_components=components,
        tools=(
            ToolBinding(
                spec={
                    "name": "reviewed_tool",
                    "description": "reviewed operation",
                    "input_schema": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                        "required": ["question"],
                        "additionalProperties": False,
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                    "risk_level": "L1",
                    "side_effect": False,
                    "idempotent": True,
                },
                handler=reviewed_tool,
            ),
        ),
        permission_profile=PermissionProfile(
            mode="auto",
            preauthorized=[],
            deny=[],
            review_required=["reviewed_tool"],
        ),
    )


def _intent() -> dict[str, Any]:
    return {
        "intent_id": "intent-1",
        "version": 1,
        "status": "confirmed",
        "goal": "Run reviewed work",
        "desired_outcome": "One verified answer",
        "success_criteria": [
            {
                "id": "criterion-1",
                "description": "The reviewed answer is accepted",
                "required": True,
                "verification_mode": "verifier",
                "validator_id": "criterion-v1",
            }
        ],
    }


def test_task_graph_session_restores_from_root_store_without_legacy_checkpoint(
    tmp_path,
) -> None:
    calls: list[str] = []
    agent = _agent(calls)
    root_store = InMemoryRootCheckpointStore()
    first = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        max_steps=40,
    )

    interrupted = first.run_task(
        agent=agent.name,
        input={"intent": _intent()},
        thread_id="long-task-thread",
    )

    assert interrupted["status"] == "interrupted"
    assert interrupted["pending_judgment"] is not None
    assert calls == []
    judgment_id = interrupted["pending_judgment"]["judgment_id"]

    restored = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        max_steps=40,
    )
    completed = restored.respond_to_judgment(
        thread_id="long-task-thread",
        judgment_id=judgment_id,
        kind="approve",
    )

    assert completed["status"] == "completed"
    assert completed["output"] is not None
    assert completed["output"]["goal_verified"] is True
    assert calls == ["same proposal"]
    snapshot = root_store.load_by_thread("long-task-thread")
    assert snapshot is not None and snapshot.long_task_state is not None
    assert snapshot.long_task_state.graph is not None
    assert snapshot.long_task_state.graph.status == "completed"


def test_task_plan_projection_is_a_copy_of_root_graph_state(tmp_path) -> None:
    calls: list[str] = []
    agent = _agent(calls)
    root_store = InMemoryRootCheckpointStore()
    session = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        max_steps=40,
    )
    response = session.run_task(
        agent=agent.name,
        input={"intent": _intent()},
        thread_id="projection-thread",
    )
    assert response["status"] == "interrupted"

    plan = session.get_task_plan("projection-thread")
    assert plan is not None
    plan["items"][0]["status"] = "completed"

    fresh = session.get_task_plan("projection-thread")
    assert fresh is not None
    assert fresh["items"][0]["status"] == "blocked"


def test_rejected_task_graph_operation_closes_attempt_task_and_graph(tmp_path) -> None:
    calls: list[str] = []
    agent = _agent(calls)
    root_store = InMemoryRootCheckpointStore()
    session = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        max_steps=40,
    )
    interrupted = session.run_task(
        agent=agent.name,
        input={"intent": _intent()},
        thread_id="reject-thread",
    )

    rejected = session.respond_to_judgment(
        thread_id="reject-thread",
        judgment_id=interrupted["pending_judgment"]["judgment_id"],
        kind="reject",
        rationale="not authorized",
    )

    assert rejected["status"] == "failed"
    assert calls == []
    snapshot = root_store.load_by_thread("reject-thread")
    assert snapshot is not None and snapshot.long_task_state is not None
    state = snapshot.long_task_state
    assert state.graph is not None and state.graph.status == "failed"
    assert state.graph.tasks[0].status == "failed"
    assert state.attempts[0].status == "failed"


def test_cancelled_task_graph_wait_closes_internal_attempt_and_graph(tmp_path) -> None:
    calls: list[str] = []
    agent = _agent(calls)
    root_store = InMemoryRootCheckpointStore()
    session = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        max_steps=40,
    )
    interrupted = session.run_task(
        agent=agent.name,
        input={"intent": _intent()},
        thread_id="cancel-thread",
    )

    cancelled = session.respond_to_judgment(
        thread_id="cancel-thread",
        judgment_id=interrupted["pending_judgment"]["judgment_id"],
        kind="cancel",
    )

    assert cancelled["status"] == "cancelled"
    assert calls == []
    snapshot = root_store.load_by_thread("cancel-thread")
    assert snapshot is not None and snapshot.long_task_state is not None
    state = snapshot.long_task_state
    assert state.graph is not None and state.graph.status == "cancelled"
    assert state.graph.tasks[0].status == "cancelled"
    assert state.attempts[0].status == "cancelled"


def test_root_cas_conflict_discards_stale_process_local_branch(tmp_path) -> None:
    calls: list[str] = []
    agent = _agent(calls)
    root_store = InMemoryRootCheckpointStore()

    def session() -> ModiSession:
        return ModiSession(
            ModiHarness(_CompleteModel()),
            agents=[agent],
            checkpointer=MemorySaver(),
            workspace_root=tmp_path / "workspace",
            memory_root=tmp_path / "memory",
            root_checkpoint_store=root_store,
            max_steps=40,
        )

    first = session()
    interrupted = first.run_task(
        agent=agent.name,
        input={"intent": _intent()},
        thread_id="conflict-thread",
    )
    judgment_id = interrupted["pending_judgment"]["judgment_id"]
    stale = session()
    assert stale.get_state("conflict-thread")["status"] == "waiting"

    completed = first.respond_to_judgment(
        thread_id="conflict-thread",
        judgment_id=judgment_id,
        kind="approve",
    )
    assert completed["status"] == "completed"

    with pytest.raises(RootStoreConflict):
        stale.respond_to_judgment(
            thread_id="conflict-thread",
            judgment_id=judgment_id,
            kind="approve",
        )

    reloaded = stale.get_state("conflict-thread")
    assert reloaded is not None and reloaded["status"] == "completed"
