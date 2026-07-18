"""Pinned Research Assistant components for the generic long-task runtime."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import GraphPatch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents"))

from research_assistant.long_task import (  # noqa: E402
    RESEARCH_CONTEXT_BUILDER_ID,
    RESEARCH_CRITERION_VERIFIER_ID,
    RESEARCH_FINDING_SCHEMA_ID,
    RESEARCH_GOAL_VERIFIER_ID,
    RESEARCH_GRAPH_POLICY_ID,
    RESEARCH_PLANNER_ID,
    RESEARCH_TASK_VERIFIER_ID,
    build_research_completion_validators,
    build_research_components,
    build_research_schema_registry,
)


def _component(component_id: str):
    return next(item for item in build_research_components() if item.id == component_id)


def _call(component_id: str, inputs: dict[str, Any]) -> Any:
    implementation = _component(component_id).implementation
    assert implementation is not None
    return implementation(inputs, idempotency_key="test/invocation")


def _intent() -> dict[str, Any]:
    return {
        "intent_id": "intent-1",
        "version": 1,
        "status": "confirmed",
        "goal": "Compare Tesla Model Y and Xiaomi YU7",
        "desired_outcome": "A source-grounded comparison",
        "success_criteria": [
            {
                "id": "dimensions",
                "description": "Compare dimensions",
                "required": True,
                "verification_mode": "verifier",
                "validator_id": RESEARCH_CRITERION_VERIFIER_ID,
            },
            {
                "id": "price",
                "description": "Compare price after dimensions",
                "required": True,
                "verification_mode": "verifier",
                "validator_id": RESEARCH_CRITERION_VERIFIER_ID,
            },
        ],
        "constraints": ["Use current public sources"],
        "non_goals": [],
        "assumptions": [],
        "authority_hash": "sha256:authority",
        "confirmation_proof_id": "proof-1",
        "planning_context": {
            "subject": "Tesla Model Y vs 小米 YU7",
            "candidate_dimensions": [
                {
                    "id": "dimensions",
                    "title": "车身尺寸",
                    "criterion_id": "dimensions",
                    "question": "两款车型的车身尺寸有何差异?",
                    "entities": ["Tesla Model Y", "小米 YU7"],
                    "aliases": {
                        "Tesla Model Y": ["Model Y", "Tesla ModelY"],
                        "小米 YU7": ["小米YU7", "Xiaomi YU7"],
                    },
                    "dimension": "车身尺寸与轴距",
                    "depends_on": [],
                    "verification_method": "official_primary_required",
                },
                {
                    "id": "price",
                    "title": "价格",
                    "criterion_id": "price",
                    "question": "两款车型的价格和配置有何差异?",
                    "entities": ["Tesla Model Y", "小米 YU7"],
                    "aliases": [],
                    "dimension": "价格与配置",
                    "depends_on": ["dimensions"],
                    "verification_method": "dual_independent_required",
                },
            ],
        },
    }


def _seed_inputs(intent: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "intent": intent or _intent(),
        "graph": {"graph_id": "graph-1", "revision": 0},
        "trigger": "seed",
        "allowed_operation_adapters": [],
        "allowed_child_templates": [
            {"id": "other", "fingerprint": "sha256:other"},
            {"id": "research-dimension", "fingerprint": "sha256:dimension"},
        ],
    }


def _finding(*, status: str = "sourced") -> dict[str, Any]:
    evidence = [
        {
            "claim": "Tesla Model Y has a 2890 mm wheelbase.",
            "source_url": "https://example.test/model-y",
            "source_type": "official",
            "stance": "supporting",
            "independence": "independent",
            "directness": "direct",
            "as_of": "2026-07-18",
        }
    ]
    citations = [item["source_url"] for item in evidence]
    limitations = [] if status == "sourced" else ["No usable public source was found."]
    return {
        "task_id": "dimensions",
        "question": "两款车型的车身尺寸有何差异?",
        "conclusion": "Tesla Model Y has a 2890 mm wheelbase.",
        "implications": "The dimensions differ.",
        "confidence": "high" if status == "sourced" else "low",
        "verification_method": "official_primary_required",
        "verification_id": "verification-1",
        "status": status,
        "evidence": evidence if status == "sourced" else [],
        "citations": citations if status == "sourced" else [],
        "limitations": limitations,
        "task_resolution": "completed" if status == "sourced" else "blocked",
        "operation_summary": {
            "task_id": "dimensions",
            "verification_id": "verification-1",
            "status": status,
            "verification_method": "official_primary_required",
            "evidence_count": 1 if status == "sourced" else 0,
            "citation_count": 1 if status == "sourced" else 0,
            "limitation_count": len(limitations),
            "search_count": 1,
        },
        "provenance": {
            "verification_id": "verification-1",
            "search_ids": ["search-1"],
            "evaluated_urls": ["https://example.test/model-y"],
            "searches": [
                {
                    "search_id": "search-1",
                    "structured_searches": [
                        {
                            "query": '"Tesla Model Y" wheelbase',
                            "entity": "Tesla Model Y",
                            "aliases": ["Model Y"],
                            "dimension": "车身尺寸与轴距",
                        }
                    ],
                    "usable_urls": ["https://example.test/model-y"],
                    "current_time": {
                        "issued_at": "2026-07-18T03:00:00.000Z",
                        "current_date": "2026-07-18",
                        "timezone": "Asia/Shanghai",
                    },
                }
            ],
        },
    }


def test_registration_factories_are_closed_and_schema_bound() -> None:
    components = build_research_components()
    assert [item.id for item in components] == [
        RESEARCH_PLANNER_ID,
        RESEARCH_GRAPH_POLICY_ID,
        RESEARCH_CONTEXT_BUILDER_ID,
        RESEARCH_TASK_VERIFIER_ID,
        RESEARCH_CRITERION_VERIFIER_ID,
        RESEARCH_GOAL_VERIFIER_ID,
    ]
    assert len({item.fingerprint for item in components}) == len(components)

    schemas = build_research_schema_registry()
    for component in components:
        schemas.resolve(component.input_schema_id)
        schemas.resolve(component.output_schema_id)
    schemas.resolve(RESEARCH_FINDING_SCHEMA_ID)

    validators = build_research_completion_validators()
    assert len(validators) == 1
    assert validators[0].validate(
        {"goal_verified": True, "committed_results": []}
    ) is True
    assert validators[0].validate(
        {"goal_verified": True}
    ) is False


def test_planner_creates_dimension_tasks_and_only_explicit_dependencies() -> None:
    patch = _call(RESEARCH_PLANNER_ID, _seed_inputs())

    assert isinstance(patch, GraphPatch)
    assert patch.base_revision == 0
    tasks = [operation.task for operation in patch.operations]
    assert [task.task_id for task in tasks if task is not None] == [
        "dimensions",
        "price",
    ]
    dimensions, price = tasks
    assert dimensions is not None and price is not None
    assert dimensions.depends_on == ()
    assert price.depends_on == (dimensions.ref,)
    for task in (dimensions, price):
        binding = task.executor_policy.preferred_binding
        assert binding.mode == "child_agent"
        assert binding.id == "research-dimension"
        assert binding.component_fingerprint == "sha256:dimension"
        assert task.completion_contract.output_schema_id == RESEARCH_FINDING_SCHEMA_ID
        assert task.completion_contract.validator_ids == (RESEARCH_TASK_VERIFIER_ID,)
        assert task.intent_binding_hash == compute_fingerprint(_intent())
    assert "Tesla Model Y" in dimensions.goal
    assert "official_primary_required" in dimensions.goal


def test_planner_falls_back_to_criteria_and_subject_without_inventing_dependencies() -> None:
    intent = _intent()
    intent["planning_context"] = {"subject": "Tesla Model Y vs 小米 YU7"}

    patch = _call(RESEARCH_PLANNER_ID, _seed_inputs(intent))

    tasks = [operation.task for operation in patch.operations]
    assert [task.task_id for task in tasks if task is not None] == [
        "dimensions",
        "price",
    ]
    assert all(task is not None and task.depends_on == () for task in tasks)
    assert all(task is not None and "Tesla Model Y" in task.goal for task in tasks)


def test_context_builder_projects_only_confirmed_task_and_direct_dependencies() -> None:
    patch = _call(RESEARCH_PLANNER_ID, _seed_inputs())
    task = patch.operations[1].task
    assert task is not None

    output = _call(
        RESEARCH_CONTEXT_BUILDER_ID,
        {
            "intent": _intent(),
            "task": {
                "task_id": task.task_id,
                "goal": task.goal,
                "depends_on": [
                    {"kind": "task", "id": "dimensions", "revision": 1}
                ],
            },
            "dependency_outputs": ["submission://dimensions/result"],
        },
    )

    manifest = output["context_manifest"]
    assert manifest["research_task"]["id"] == "price"
    assert manifest["research_task"]["dimension"] == "价格与配置"
    assert manifest["dependencies"] == ["dimensions"]
    assert manifest["dependency_output_refs"] == ["submission://dimensions/result"]
    assert "candidate_dimensions" not in manifest["intent"]


def test_task_verifier_accepts_only_canonical_finding_with_complete_provenance() -> None:
    inputs = {
        "intent": _intent(),
        "task": {"task_id": "dimensions"},
        "attempt": {},
        "candidate": _finding(),
        "receipt": {},
    }

    result = _call(RESEARCH_TASK_VERIFIER_ID, inputs)

    assert result == {
        "outcome": "passed",
        "evidence_refs": ["https://example.test/model-y"],
    }


def test_task_verifier_rejects_missing_or_incomplete_provenance_and_limitations() -> None:
    missing = _finding()
    missing.pop("provenance")
    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {"task": {"task_id": "dimensions"}, "candidate": missing},
    )
    assert result["outcome"] == "repairable"
    assert "provenance" in result["reason"]

    incomplete = _finding()
    incomplete["provenance"]["evaluated_urls"] = []
    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {"task": {"task_id": "dimensions"}, "candidate": incomplete},
    )
    assert result["outcome"] == "repairable"
    assert "usable URLs" in result["reason"]

    blocked = _finding(status="blocked")
    blocked["limitations"] = []
    blocked["operation_summary"]["limitation_count"] = 0
    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {"task": {"task_id": "dimensions"}, "candidate": blocked},
    )
    assert result["outcome"] == "repairable"
    assert "limitation" in result["reason"]


def test_unverifiable_blocker_requires_explicit_empty_provenance() -> None:
    finding = _finding(status="blocked")
    finding["verification_method"] = "unverifiable_flag"
    finding["verification_id"] = ""
    finding["operation_summary"]["verification_id"] = None
    finding["operation_summary"]["verification_method"] = "unverifiable_flag"
    finding["operation_summary"]["search_count"] = 0
    finding["provenance"] = {
        "verification_id": "",
        "search_ids": [],
        "evaluated_urls": [],
        "searches": [],
    }

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {"task": {"task_id": "dimensions"}, "candidate": finding},
    )

    assert result == {"outcome": "passed", "evidence_refs": []}


def test_criterion_goal_and_rebase_policy_are_safe_and_deterministic() -> None:
    criterion = _call(
        RESEARCH_CRITERION_VERIFIER_ID,
        {
            "criterion": {"id": "dimensions", "required": True},
            "tasks": [
                {
                    "task_id": "dimensions",
                    "status": "completed",
                    "output_refs": ["submission://dimensions/result"],
                }
            ],
            "groups": [],
        },
    )
    assert criterion == {
        "outcome": "passed",
        "evidence_refs": ["submission://dimensions/result"],
    }

    assert _call(
        RESEARCH_GOAL_VERIFIER_ID,
        {
            "graph": {"required_criteria": ["dimensions"], "status": "verifying"},
            "criterion_coverage": [
                {"criterion_id": "dimensions", "status": "satisfied"}
            ],
            "output_refs": ["submission://dimensions/result"],
        },
    ) == {
        "outcome": "passed",
        "evidence_refs": ["submission://dimensions/result"],
    }

    policy = _call(
        RESEARCH_GRAPH_POLICY_ID,
        {
            "candidates": [
                {
                    "target_ref": {"kind": "task", "id": "dimensions", "revision": 1},
                    "status": "completed",
                }
            ]
        },
    )
    assert policy == {
        "outcome": "passed",
        "reuse_decisions": [
            {
                "target_ref": {"kind": "task", "id": "dimensions", "revision": 1},
                "reusable": False,
            }
        ],
    }
