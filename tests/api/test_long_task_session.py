"""Public Session integration for the Operation-only Task Graph slice."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiAgent, ModiHarness, ModiSession, ToolBinding
from modi_harness._utils import compute_fingerprint
from modi_harness.checkpoint import InMemoryRootCheckpointStore, RootStoreConflict
from modi_harness.long_task import (
    AuditEvent,
    ChildTemplateLimits,
    ChildTemplateRef,
    CompletionContract,
    ExecutorBinding,
    ExecutorPolicy,
    GraphPatch,
    GraphPatchOperation,
    InMemoryChildCheckpointStore,
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


def _human_task_agent() -> ModiAgent:
    base = _agent([])
    human = PinnedComponent(
        id="human-review-v1",
        version="1",
        kind="human_contract",
        implementation_digest="sha256:human-review-v1",
        protocol_version="human-task-v1",
        input_schema_id="human-prompt-v1",
        output_schema_id="human-response-v1",
        supported_outcomes=("passed",),
        configuration={
            "prompt_schema": {
                "type": "object",
                "required": ["title"],
                "properties": {"title": {"type": "string"}},
                "additionalProperties": False,
            },
            "response_schema": {
                "type": "object",
                "required": ["decision", "comment"],
                "properties": {
                    "decision": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "decision_class": "judgment",
            "allowed_decisions": ["approve", "reject"],
            "authority_requirement": {"role": "reviewer"},
            "timeout_behavior": "keep_waiting",
            "resume_policy": "exactly_once",
            "prompt": {"title": "Review the long-running Task"},
        },
        implementation=None,
    )


    def planner(inputs: dict[str, Any]) -> GraphPatch:
        binding = ExecutorBinding(
            "human",
            human.id,
            human.fingerprint,
        )
        task = TaskRun(
            task_id="human-review",
            task_revision=1,
            graph_id=inputs["graph"]["graph_id"],
            intent_version=inputs["intent"]["version"],
            intent_binding_hash=compute_fingerprint(inputs["intent"]),
            intent_binding_state="current",
            goal="Obtain one durable human judgment",
            supports=("criterion-1",),
            depends_on=(),
            priority=50,
            required=True,
            kind="executable",
            completion_contract=CompletionContract("human-result", ("task-v1",)),
            executor_policy=ExecutorPolicy((binding,), binding),
        )
        return GraphPatch(
            base_revision=0,
            trigger="seed",
            reason="one human Task",
            operations=(GraphPatchOperation("add_task", task=task),),
        )

    workflow = base.workflows[0]
    node = workflow.nodes[0]
    assert node.task_graph is not None
    components = (
        *(
            _component("planner-v1", "planner", planner)
            if component.id == "planner-v1"
            else component
            for component in base.task_graph_components
        ),
        human,
    )
    return replace(
        base,
        workflows=(
            replace(
                workflow,
                definition_fingerprint="human-long-task-fixture",
                nodes=(
                    replace(
                        node,
                        task_graph=replace(
                            node.task_graph,
                            operation_adapters=(),
                            human_task_contracts=(human.id,),
                        ),
                    ),
                ),
            ),
        ),
        task_graph_components=components,
        tools=(),
        permission_profile=None,
    )


def test_public_human_task_restores_exact_pending_decision_and_consumes_once(
    tmp_path,
) -> None:
    agent = _human_task_agent()
    root_store = InMemoryRootCheckpointStore()

    def build() -> ModiSession:
        return ModiSession(
            ModiHarness(_CompleteModel()),
            agents=[agent],
            checkpointer=MemorySaver(),
            workspace_root=tmp_path / "workspace",
            memory_root=tmp_path / "memory",
            root_checkpoint_store=root_store,
            max_steps=60,
        )

    first = build()
    waiting = first.run_task(
        agent=agent.name,
        input={"intent": _intent()},
        thread_id="public-human-task",
    )
    assert waiting["status"] == "interrupted"
    assert waiting["pending_judgment"] is not None
    judgment_id = waiting["pending_judgment"]["judgment_id"]
    snapshot = root_store.load_by_thread("public-human-task")
    assert snapshot is not None and snapshot.long_task_state is not None
    assert snapshot.long_task_state.pending_task_decisions[0].request_id == judgment_id

    restored = build()
    resumed = restored.resume_task(
        thread_id="public-human-task",
        payload={
            "judgment_id": judgment_id,
            "decision": "approve",
            "response": {"decision": "approve", "comment": "reviewed"},
        },
    )
    assert resumed["status"] == "completed"
    final_snapshot = root_store.load_by_thread("public-human-task")
    assert final_snapshot is not None and final_snapshot.long_task_state is not None
    final_state = final_snapshot.long_task_state
    assert final_state.pending_task_decisions[0].status == "consumed"
    assert final_state.graph is not None
    assert final_state.graph.tasks[0].status == "completed"


def _child_agents() -> tuple[ModiAgent, ModiAgent]:
    child_workflow = Workflow(
        id="child-work",
        description="Run one pinned child Task.",
        input_schema={
            "type": "object",
            "properties": {"context_manifest": {"type": "object"}},
            "required": ["context_manifest"],
        },
        start_node="work",
        nodes=(
            Node(
                id="work",
                execution="autonomous",
                inputs={"context_manifest": {"$ref": "#/workflow/input/context_manifest"}},
                goal="Complete the exact ContextManifest Task",
                completion_output_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
                completion_validator=None,
                completion_required=("answer",),
                completion_review="none",
                transitions={"completed": "$complete", "failed": "$fail"},
                max_steps=4,
            ),
        ),
        definition_fingerprint="child-work-v1",
    )
    child = ModiAgent(
        name="worker-agent",
        description="worker",
        instruction="Complete the assigned child Task and return answer.",
        workflows=(child_workflow,),
    )

    def planner(inputs: dict[str, Any]) -> GraphPatch:
        template = inputs["allowed_child_templates"][0]
        binding = ExecutorBinding(
            "child_agent",
            template["id"],
            template["fingerprint"],
        )
        task = TaskRun(
            task_id="child-task",
            task_revision=1,
            graph_id=inputs["graph"]["graph_id"],
            intent_version=inputs["intent"]["version"],
            intent_binding_hash=compute_fingerprint(inputs["intent"]),
            intent_binding_state="current",
            goal="Run isolated child work",
            supports=("criterion-1",),
            depends_on=(),
            priority=50,
            required=True,
            kind="executable",
            completion_contract=CompletionContract("child-result", ("task-v1",)),
            executor_policy=ExecutorPolicy((binding,), binding),
        )
        return GraphPatch(
            base_revision=0,
            trigger="seed",
            reason="one child Task",
            operations=(GraphPatchOperation("add_task", task=task),),
        )

    parent = _agent([])
    workflow = parent.workflows[0]
    node = workflow.nodes[0]
    assert node.task_graph is not None
    components = tuple(
        _component("planner-v1", "planner", planner)
        if component.id == "planner-v1"
        else component
        for component in parent.task_graph_components
    )
    parent = replace(
        parent,
        workflows=(
            replace(
                workflow,
                nodes=(
                    replace(
                        node,
                        task_graph=replace(
                            node.task_graph,
                            operation_adapters=(),
                            child_templates=("worker",),
                            limits=replace(
                                node.task_graph.limits,
                                max_child_runs=1,
                            ),
                        ),
                    ),
                ),
            ),
        ),
        task_graph_components=components,
        tools=(),
        permission_profile=None,
        child_templates=(
            ChildTemplateRef(
                id="worker",
                agent_name=child.name,
                workflow_id=child_workflow.id,
                limits=ChildTemplateLimits(max_steps=4, timeout_seconds=60),
            ),
        ),
    )
    return parent, child


def _repair_child_agents() -> tuple[ModiAgent, ModiAgent, list[str]]:
    parent, child = _child_agents()
    calls: list[str] = []

    def verifier(_inputs: dict[str, Any]) -> dict[str, str]:
        outcome = "repairable" if not calls else "passed"
        calls.append(outcome)
        return {"outcome": outcome, "reason": "retry once"}

    return (
        replace(
            parent,
            task_graph_components=tuple(
                _component(
                    "task-v1",
                    "task_verifier",
                    verifier,
                    outcomes=("passed", "repairable"),
                )
                if component.id == "task-v1"
                else component
                for component in parent.task_graph_components
            ),
        ),
        child,
        calls,
    )


def _parallel_child_agents() -> tuple[ModiAgent, ModiAgent]:
    parent, child = _child_agents()

    def planner(inputs: dict[str, Any]) -> GraphPatch:
        template = inputs["allowed_child_templates"][0]
        binding = ExecutorBinding(
            "child_agent",
            template["id"],
            template["fingerprint"],
        )
        tasks = tuple(
            TaskRun(
                task_id=f"child-task-{index}",
                task_revision=1,
                graph_id=inputs["graph"]["graph_id"],
                intent_version=inputs["intent"]["version"],
                intent_binding_hash=compute_fingerprint(inputs["intent"]),
                intent_binding_state="current",
                goal=f"Run isolated child work {index}",
                supports=("criterion-1",),
                depends_on=(),
                priority=50,
                required=True,
                kind="executable",
                completion_contract=CompletionContract("child-result", ("task-v1",)),
                executor_policy=ExecutorPolicy((binding,), binding),
            )
            for index in (1, 2)
        )
        return GraphPatch(
            base_revision=0,
            trigger="seed",
            reason="two independent child Tasks",
            operations=tuple(
                GraphPatchOperation("add_task", task=task) for task in tasks
            ),
        )

    workflow = parent.workflows[0]
    node = workflow.nodes[0]
    assert node.task_graph is not None
    components = tuple(
        _component("planner-v1", "planner", planner)
        if component.id == "planner-v1"
        else component
        for component in parent.task_graph_components
    )
    parent = replace(
        parent,
        workflows=(
            replace(
                workflow,
                nodes=(
                    replace(
                        node,
                        task_graph=replace(
                            node.task_graph,
                            limits=replace(
                                node.task_graph.limits,
                                max_concurrency=2,
                                max_child_runs=2,
                            ),
                        ),
                    ),
                ),
            ),
        ),
        task_graph_components=components,
    )
    return parent, child


def test_child_task_graph_runs_exact_pinned_workflow_and_persists_checkpoint(
    tmp_path,
) -> None:
    parent, child = _child_agents()
    root_store = InMemoryRootCheckpointStore()
    child_store = InMemoryChildCheckpointStore()
    session = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[parent],
        dependency_agents=[child],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        child_checkpoint_store=child_store,
        max_steps=40,
    )

    completed = session.run_task(
        agent=parent.name,
        input={"intent": _intent()},
        thread_id="child-task-thread",
    )

    assert completed["status"] == "completed"
    snapshot = root_store.load_by_thread("child-task-thread")
    assert snapshot is not None and snapshot.long_task_state is not None
    attempt = snapshot.long_task_state.attempts[0]
    assert attempt.status == "completed"
    assert attempt.child_run_id is not None
    child_snapshot = child_store.load_by_child_run_id(attempt.child_run_id)
    assert child_snapshot is not None
    assert child_snapshot.status == "completed"
    assert child_snapshot.submissions[0].result == {"answer": "ok"}
    assert child_snapshot.delivery_acks[0].decision == "accepted"


def test_child_task_graph_repair_resumes_same_child_with_new_fence(tmp_path) -> None:
    parent, child, verifier_calls = _repair_child_agents()
    root_store = InMemoryRootCheckpointStore()
    child_store = InMemoryChildCheckpointStore()
    session = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[parent],
        dependency_agents=[child],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        child_checkpoint_store=child_store,
        max_steps=70,
    )

    completed = session.run_task(
        agent=parent.name,
        input={"intent": _intent()},
        thread_id="child-repair-thread",
    )

    assert completed["status"] == "completed"
    snapshot = root_store.load_by_thread("child-repair-thread")
    assert snapshot is not None and snapshot.long_task_state is not None
    attempt = snapshot.long_task_state.attempts[0]
    assert attempt.status == "completed"
    assert attempt.lease.epoch == 2
    assert attempt.lease.retiring is False
    assert attempt.child_run_id is not None
    child_snapshot = child_store.load_by_child_run_id(attempt.child_run_id)
    assert child_snapshot is not None
    assert len(child_snapshot.submissions) == 2
    assert [item.decision for item in child_snapshot.delivery_acks] == [
        "repairable",
        "accepted",
    ]
    assert child_snapshot.active_lease is not None
    assert child_snapshot.active_lease.epoch == 2
    assert verifier_calls == ["repairable", "passed"]


def test_child_repair_recovers_after_root_cas_before_delivery_ack(tmp_path) -> None:
    parent, child, verifier_calls = _repair_child_agents()
    child_store = InMemoryChildCheckpointStore()

    class CrashAfterRepairRootStore(InMemoryRootCheckpointStore):
        def __init__(self) -> None:
            super().__init__()
            self.crashed = False

        def compare_and_swap(self, root_run_id, *, expected_revision, snapshot, event):
            committed = super().compare_and_swap(
                root_run_id,
                expected_revision=expected_revision,
                snapshot=snapshot,
                event=event,
            )
            if event.event_type == "candidate_repair_requested" and not self.crashed:
                self.crashed = True
                raise BaseException("simulated crash before repair delivery ACK")
            return committed

    root_store = CrashAfterRepairRootStore()

    def session() -> ModiSession:
        return ModiSession(
            ModiHarness(_CompleteModel()),
            agents=[parent],
            dependency_agents=[child],
            checkpointer=MemorySaver(),
            workspace_root=tmp_path / "workspace",
            memory_root=tmp_path / "memory",
            root_checkpoint_store=root_store,
            child_checkpoint_store=child_store,
            max_steps=70,
        )

    with pytest.raises(BaseException, match="before repair delivery ACK"):
        session().run_task(
            agent=parent.name,
            input={"intent": _intent()},
            thread_id="child-repair-ack-crash",
        )

    crashed = root_store.load_by_thread("child-repair-ack-crash")
    assert crashed is not None and crashed.long_task_state is not None
    attempt = crashed.long_task_state.attempts[0]
    assert attempt.lease.epoch == 2
    assert attempt.child_run_id is not None
    before_resume = child_store.load_by_child_run_id(attempt.child_run_id)
    assert before_resume is not None and before_resume.active_lease is not None
    assert before_resume.active_lease.epoch == 1

    completed = session().resume_task(thread_id="child-repair-ack-crash")

    assert completed["status"] == "completed"
    after_resume = child_store.load_by_child_run_id(attempt.child_run_id)
    assert after_resume is not None and after_resume.active_lease is not None
    assert after_resume.active_lease.epoch == 2
    assert len(after_resume.submissions) == 2
    assert [item.decision for item in after_resume.delivery_acks] == [
        "repairable",
        "accepted",
    ]
    assert verifier_calls == ["repairable", "passed"]


def test_public_session_advances_independent_child_workflows_concurrently(
    tmp_path,
) -> None:
    parent, child = _parallel_child_agents()
    barrier = threading.Barrier(2)
    thread_ids: set[int] = set()
    lock = threading.Lock()

    class BarrierCompleteModel(_CompleteModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            with lock:
                thread_ids.add(threading.get_ident())
            barrier.wait(timeout=2)
            return super()._generate(messages, stop, run_manager, **kwargs)

    session = ModiSession(
        ModiHarness(BarrierCompleteModel()),
        agents=[parent],
        dependency_agents=[child],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=InMemoryRootCheckpointStore(),
        child_checkpoint_store=InMemoryChildCheckpointStore(),
        max_steps=60,
    )

    completed = session.run_task(
        agent=parent.name,
        input={"intent": _intent()},
        thread_id="parallel-child-task-thread",
    )

    assert completed["status"] == "completed"
    assert len(thread_ids) == 2


def test_child_task_graph_restores_after_parent_prepare_with_same_attempt(
    tmp_path,
) -> None:
    parent, child = _child_agents()
    root_store = InMemoryRootCheckpointStore()
    child_store = InMemoryChildCheckpointStore()

    class CrashAfterPrepare(InMemoryRootCheckpointStore):
        def __init__(self) -> None:
            super().__init__()
            self.crashed = False

        def compare_and_swap(self, root_run_id, *, expected_revision, snapshot, event):
            committed = super().compare_and_swap(
                root_run_id,
                expected_revision=expected_revision,
                snapshot=snapshot,
                event=event,
            )
            if event.event_type == "attempt_prepared" and not self.crashed:
                self.crashed = True
                raise BaseException("simulated process crash after parent prepare")
            return committed

    root_store = CrashAfterPrepare()

    def session() -> ModiSession:
        return ModiSession(
            ModiHarness(_CompleteModel()),
            agents=[parent],
            dependency_agents=[child],
            checkpointer=MemorySaver(),
            workspace_root=tmp_path / "workspace",
            memory_root=tmp_path / "memory",
            root_checkpoint_store=root_store,
            child_checkpoint_store=child_store,
            max_steps=40,
        )

    with pytest.raises(BaseException, match="simulated process crash"):
        session().run_task(
            agent=parent.name,
            input={"intent": _intent()},
            thread_id="child-prepare-crash",
        )
    prepared = root_store.load_by_thread("child-prepare-crash")
    assert prepared is not None and prepared.long_task_state is not None
    attempt_id = prepared.long_task_state.attempts[0].attempt_id

    completed = session().resume_task(thread_id="child-prepare-crash")

    assert completed["status"] == "completed"
    restored = root_store.load_by_thread("child-prepare-crash")
    assert restored is not None and restored.long_task_state is not None
    assert len(restored.long_task_state.attempts) == 1
    assert restored.long_task_state.attempts[0].attempt_id == attempt_id


def test_child_task_graph_restores_persisted_submission_without_rerunning_child(
    tmp_path,
) -> None:
    parent, child = _child_agents()
    child_store = InMemoryChildCheckpointStore()

    class CrashAfterSubmission(InMemoryRootCheckpointStore):
        def __init__(self) -> None:
            super().__init__()
            self.crashed = False

        def compare_and_swap(self, root_run_id, *, expected_revision, snapshot, event):
            if event.event_type == "candidate_submitted" and not self.crashed:
                self.crashed = True
                raise BaseException("simulated process crash before parent receipt CAS")
            return super().compare_and_swap(
                root_run_id,
                expected_revision=expected_revision,
                snapshot=snapshot,
                event=event,
            )

    root_store = CrashAfterSubmission()

    def session() -> ModiSession:
        return ModiSession(
            ModiHarness(_CompleteModel()),
            agents=[parent],
            dependency_agents=[child],
            checkpointer=MemorySaver(),
            workspace_root=tmp_path / "workspace",
            memory_root=tmp_path / "memory",
            root_checkpoint_store=root_store,
            child_checkpoint_store=child_store,
            max_steps=40,
        )

    with pytest.raises(BaseException, match="before parent receipt CAS"):
        session().run_task(
            agent=parent.name,
            input={"intent": _intent()},
            thread_id="child-submission-crash",
        )
    root = root_store.load_by_thread("child-submission-crash")
    assert root is not None and root.long_task_state is not None
    attempt = root.long_task_state.attempts[0]
    assert attempt.child_run_id is not None
    persisted_child = child_store.load_by_child_run_id(attempt.child_run_id)
    assert persisted_child is not None and len(persisted_child.submissions) == 1
    submission_id = persisted_child.submissions[0].submission_id

    completed = session().resume_task(thread_id="child-submission-crash")

    assert completed["status"] == "completed"
    final = root_store.load_by_thread("child-submission-crash")
    assert final is not None and final.long_task_state is not None
    assert len(final.long_task_state.receipts) == 1
    assert final.long_task_state.receipts[0].submission_id == submission_id


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
    prepared = root_store.load_by_thread("long-task-thread")
    assert prepared is not None
    persisted_contract = prepared.workflow_state["execution_contract"]
    assert persisted_contract["fingerprint"]
    assert persisted_contract["snapshot"]["task_graph"]["protocol_version"] == "task-graph-v1"
    assert prepared.long_task_state is not None
    attempt = prepared.long_task_state.attempts[0]
    assert attempt.parent_node_id == "execute"
    assert attempt.parent_node_attempt == 1

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


def test_task_graph_restore_rejects_missing_persisted_contract(tmp_path) -> None:
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
    first.run_task(
        agent=agent.name,
        input={"intent": _intent()},
        thread_id="missing-contract-thread",
    )
    prepared = root_store.load_by_thread("missing-contract-thread")
    assert prepared is not None
    workflow_state = dict(prepared.workflow_state)
    workflow_state.pop("execution_contract")
    corrupted_revision = prepared.revision + 1
    root_store.compare_and_swap(
        prepared.root_run_id,
        expected_revision=prepared.revision,
        snapshot=replace(
            prepared,
            revision=corrupted_revision,
            workflow_state=workflow_state,
        ),
        event=AuditEvent(
            event_id="corrupt-contract",
            event_type="checkpoint_corrupted",
            root_revision=corrupted_revision,
            payload={},
        ),
    )
    restored = ModiSession(
        ModiHarness(_CompleteModel()),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        max_steps=40,
    )

    with pytest.raises(RuntimeError, match="missing its execution contract"):
        restored.get_state("missing-contract-thread")


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
