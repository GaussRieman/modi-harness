"""Static child template value and registry tests."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from modi_harness import ModiAgent, ToolBinding
from modi_harness._utils import canonical_json, compute_fingerprint
from modi_harness.long_task import (
    ChildTemplateError,
    ChildTemplateLimits,
    ChildTemplateRef,
    PinnedChildTemplate,
    PinnedChildTemplateRegistry,
    resolve_child_template_registry,
)
from modi_harness.types import ModelSpec, PermissionProfile
from modi_harness.workflow import Node, Workflow, parse_workflow
from modi_harness.workflow.contract import (
    OperationAdapter,
    OperationAdapterRegistry,
)


def _operation_workflow(*, workflow_id: str, operation: str) -> Workflow:
    return parse_workflow(
        {
            "id": workflow_id,
            "description": "Execute one operation.",
            "input_schema": {"type": "object"},
            "start_node": "execute",
            "nodes": [
                {
                    "id": "execute",
                    "execution": "operation",
                    "operation": operation,
                    "transitions": {"completed": "$complete", "failed": "$fail"},
                }
            ],
        }
    )


def _adapter(*, required: tuple[str, ...] = ("search",)) -> OperationAdapter:
    return OperationAdapter(
        id="lookup",
        version="1",
        kind="tool",
        target="lookup",
        node_selectable=True,
        required_capabilities=required,
        side_effect=False,
        recovery_mode="pure",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def _registry(*, required: tuple[str, ...] = ("search",)) -> OperationAdapterRegistry:
    registry = OperationAdapterRegistry()
    registry.register(_adapter(required=required))
    return registry


def _template() -> ChildTemplateRef:
    return ChildTemplateRef(
        id="worker",
        agent_name="worker-agent",
        workflow_id="execute",
        limits=ChildTemplateLimits(max_steps=20, timeout_seconds=900),
    )


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def test_child_template_ref_is_frozen_and_snapshot_stable() -> None:
    template = ChildTemplateRef(
        id="worker",
        agent_name="worker-agent",
        workflow_id="execute",
        limits=ChildTemplateLimits(max_steps=20, timeout_seconds=900),
    )

    assert template.snapshot() == {
        "id": "worker",
        "agent_name": "worker-agent",
        "workflow_id": "execute",
        "limits": {"max_steps": 20, "timeout_seconds": 900},
    }


def test_child_template_limits_reject_non_positive_values() -> None:
    with pytest.raises(ChildTemplateError, match="positive integer"):
        ChildTemplateLimits(max_steps=0, timeout_seconds=10)
    with pytest.raises(ChildTemplateError, match="positive integer"):
        ChildTemplateLimits(max_steps=10, timeout_seconds=-1)


def test_pinned_child_template_registry_is_closed_and_fingerprinted() -> None:
    pinned = PinnedChildTemplate.from_snapshot(
        "worker",
        {
            "template": {"id": "worker"},
            "child_agent": {"name": "worker-agent"},
        },
    )
    registry = PinnedChildTemplateRegistry()
    registry.register(pinned)

    assert registry.resolve("worker") is pinned
    assert pinned.fingerprint
    with pytest.raises(ChildTemplateError, match="duplicate"):
        registry.register(pinned)
    with pytest.raises(ChildTemplateError, match="unknown"):
        registry.resolve("missing")


def test_pinned_child_template_rejects_changed_fingerprint() -> None:
    with pytest.raises(ChildTemplateError, match="fingerprint"):
        PinnedChildTemplate(
            id="worker",
            snapshot={"template": {"id": "worker"}},
            fingerprint="stale",
        )


def test_resolves_complete_child_template_without_persisting_secrets() -> None:
    parent_permission = PermissionProfile(
        mode="preview",
        preauthorized=["lookup"],
        deny=["blocked-parent"],
        review_required=[],
    )
    child_permission = PermissionProfile(
        mode="trust",
        preauthorized=["lookup", "other"],
        deny=["blocked-child"],
        review_required=["lookup"],
    )
    parent = ModiAgent(
        name="parent",
        description="parent",
        instruction="coordinate",
        workflows=(_operation_workflow(workflow_id="parent", operation="lookup"),),
        child_templates=(_template(),),
        permission_profile=parent_permission,
    )
    child = ModiAgent(
        name="worker-agent",
        description="worker",
        instruction="do the work",
        workflows=(_operation_workflow(workflow_id="execute", operation="lookup"),),
        tools=(
            ToolBinding(
                spec={
                    "name": "lookup",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                },
                handler=lambda **_: {},
            ),
        ),
        output_contract={"free_form": True},
        permission_profile=child_permission,
        model_override=ModelSpec(
            provider="test",
            name="child-model",
            api_key="super-secret",
            extra={"temperature": 0, "access_token": "also-secret"},
        ),
        metadata={
            "package": {"root": "/tmp/environment-specific/worker"},
            "schema": {
                "type": "object",
                "properties": {"time_token": {"type": "string"}},
            },
        },
    )

    pinned = resolve_child_template_registry(
        parent_agent=parent,
        agents={parent.name: parent, child.name: child},
        adapters=_registry(),
        parent_capability_ceiling={"search", "write"},
        visible_adapter_ids={parent.name: {"lookup"}, child.name: {"lookup"}},
    ).resolve("worker")
    snapshot = _plain(pinned.snapshot)

    assert canonical_json(snapshot)
    assert snapshot["child_agent"]["definition"]["instruction"] == "do the work"
    assert snapshot["child_agent"]["definition"]["model_override"] == {
        "provider": "test",
        "name": "child-model",
        "base_url": None,
        "api_key_configured": True,
        "extra": {"temperature": 0, "access_token": "[redacted]"},
    }
    assert "super-secret" not in canonical_json(snapshot).decode()
    assert "package" not in snapshot["child_agent"]["definition"]["metadata"]
    assert snapshot["child_agent"]["definition"]["metadata"]["schema"]["properties"] == {
        "time_token": {"type": "string"}
    }
    assert snapshot["authority"]["workflow_adapters"] == ["lookup"]
    assert snapshot["authority"]["workflow_required_capabilities"] == ["search"]
    assert snapshot["authority"]["effective_capability_ceiling"] == ["search"]
    assert snapshot["authority"]["permission_profile"]["static_intersection"] == {
        "mode": "preview",
        "preauthorized": ["lookup"],
        "deny": ["blocked-child", "blocked-parent"],
        "review_required": ["lookup"],
    }
    child_contract = snapshot["child_execution_contract"]
    assert child_contract["fingerprint"] == compute_fingerprint(child_contract["snapshot"])
    assert child_contract["snapshot"]["protocol_version"] == "workflow-v1"


def test_child_template_resolution_rejects_unknown_agent_or_workflow() -> None:
    parent = ModiAgent(
        name="parent",
        description="parent",
        instruction="coordinate",
        workflows=(_operation_workflow(workflow_id="parent", operation="lookup"),),
        child_templates=(_template(),),
    )
    with pytest.raises(ChildTemplateError, match="unknown child Agent"):
        resolve_child_template_registry(
            parent_agent=parent,
            agents={parent.name: parent},
            adapters=_registry(),
            parent_capability_ceiling={"search"},
        )

    child = ModiAgent(
        name="worker-agent",
        description="worker",
        instruction="work",
        workflows=(_operation_workflow(workflow_id="different", operation="lookup"),),
    )
    with pytest.raises(ChildTemplateError, match="unknown child Workflow"):
        resolve_child_template_registry(
            parent_agent=parent,
            agents={parent.name: parent, child.name: child},
            adapters=_registry(),
            parent_capability_ceiling={"search"},
        )


def test_child_template_resolution_rejects_authority_expansion() -> None:
    parent = ModiAgent(
        name="parent",
        description="parent",
        instruction="coordinate",
        workflows=(_operation_workflow(workflow_id="parent", operation="lookup"),),
        child_templates=(_template(),),
    )
    child = ModiAgent(
        name="worker-agent",
        description="worker",
        instruction="work",
        workflows=(_operation_workflow(workflow_id="execute", operation="lookup"),),
        tools=(ToolBinding(spec={"name": "lookup"}, handler=lambda **_: {}),),
    )
    with pytest.raises(ChildTemplateError, match=r"expands root authority.*network"):
        resolve_child_template_registry(
            parent_agent=parent,
            agents={parent.name: parent, child.name: child},
            adapters=_registry(required=("network",)),
            parent_capability_ceiling={"search"},
        )


def test_child_template_resolution_rejects_task_graph_child_workflow() -> None:
    recursive = Workflow(
        id="execute",
        description="recursive",
        input_schema={},
        start_node="graph",
        nodes=(
            Node(
                id="graph",
                execution="task_graph",
                inputs={},
                completion_output_schema=None,
                completion_validator=None,
                completion_required=(),
                completion_review="none",
                transitions={"completed": "$complete"},
            ),
        ),
        definition_fingerprint="recursive",
    )
    parent = ModiAgent(
        name="parent",
        description="parent",
        instruction="coordinate",
        workflows=(_operation_workflow(workflow_id="parent", operation="lookup"),),
        child_templates=(_template(),),
    )
    child = ModiAgent(
        name="worker-agent",
        description="worker",
        instruction="work",
        workflows=(recursive,),
    )
    with pytest.raises(ChildTemplateError, match=r"task_graph.*not supported"):
        resolve_child_template_registry(
            parent_agent=parent,
            agents={parent.name: parent, child.name: child},
            adapters=_registry(),
            parent_capability_ceiling={"search"},
        )
