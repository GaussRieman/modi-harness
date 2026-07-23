"""Pinned Research Assistant components for the generic long-task runtime."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

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
    authority_binding_fingerprint,
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
                    "authority_bindings": [
                        {"host": "example.test", "source_type": "official"}
                    ],
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
                    "authority_bindings": [],
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
    limitations = (
        []
        if status == "sourced"
        else [
            "verification_method official_primary_required expects official or "
            "primary supporting sources"
        ]
    )
    evaluations = (
        evidence
        if status == "sourced"
        else [{**evidence[0], "stance": "unrelated"}]
    )
    return {
        "task_id": "dimensions",
        "question": "两款车型的车身尺寸有何差异?",
        "conclusion": "Tesla Model Y has a 2890 mm wheelbase.",
        "implications": "The dimensions differ.",
        "confidence": "medium" if status == "sourced" else "low",
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
            "evaluations": evaluations,
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
            "authority_binding_fingerprint": authority_binding_fingerprint(
                [{"host": "example.test", "source_type": "official"}]
            ),
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
    assert dimensions.goal == "车身尺寸"
    assert price.goal == "价格"


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
    assert [task.goal for task in tasks if task is not None] == [
        "Compare dimensions",
        "Compare price after dimensions",
    ]


def test_planner_adds_bounded_deduplicated_discovered_work() -> None:
    intent = _intent()
    context = {
        "intent": {
            "intent_id": intent["intent_id"],
            "version": intent["version"],
            "binding_hash": compute_fingerprint(intent),
            "goal": intent["goal"],
        },
        "graph": {
            "graph_id": "graph-1",
            "revision": 1,
            "tasks": [
                {
                    "ref": {"kind": "task", "id": "dimensions", "revision": 1},
                    "goal": "两款车型的车身尺寸有何差异?",
                    "status": "completed",
                }
            ],
        },
        "budgets": {"max_tasks": 8, "active_tasks": 1},
        "authority_boundaries": {
            "child_templates": [
                {"id": "research-dimension", "fingerprint": "sha256:dimension"}
            ]
        },
        "discovered_work": [
            {"goal": "两款车型的车身尺寸有何差异?", "rationale": "重复问题"},
            {"goal": "两款车的智驾能力是否存在关键差异?", "rationale": "影响选择"},
            {"goal": "两款车的补能体验有何差异?", "rationale": "影响长途使用"},
            {"goal": "第三个问题不应进入本波", "rationale": "超过单波上限"},
        ],
    }

    patch = _call(
        RESEARCH_PLANNER_ID,
        {
            "context": context,
            "trigger": {"kind": "discovered_work"},
        },
    )

    assert patch.trigger == "discovered_work"
    assert len(patch.operations) == 2
    tasks = [item.task for item in patch.operations]
    assert all(task is not None for task in tasks)
    assert all(task.executor_policy.preferred_binding.component_fingerprint == "sha256:dimension" for task in tasks if task)
    assert all(task.intent_binding_hash == compute_fingerprint(intent) for task in tasks if task)
    assert all(task.required is False for task in tasks if task)
    assert all(task.supports == () for task in tasks if task)
    assert "智驾能力" in str(tasks[0].goal)
    assert "补能体验" in str(tasks[1].goal)


def test_planner_consumes_duplicate_discovered_work_without_graph_patch() -> None:
    intent = _intent()
    result = _call(
        RESEARCH_PLANNER_ID,
        {
            "trigger": {"kind": "discovered_work"},
            "context": {
                "intent": {
                    "intent_id": intent["intent_id"],
                    "version": intent["version"],
                    "binding_hash": compute_fingerprint(intent),
                    "goal": intent["goal"],
                },
                "graph": {
                    "graph_id": "graph-1",
                    "revision": 1,
                    "tasks": [
                        {
                            "ref": {"kind": "task", "id": "dimensions", "revision": 1},
                            "goal": "两款车型的车身尺寸有何差异?",
                            "status": "completed",
                        }
                    ],
                },
                "budgets": {"max_tasks": 8, "active_tasks": 1},
                "authority_boundaries": {
                    "child_templates": [
                        {"id": "research-dimension", "fingerprint": "sha256:dimension"}
                    ]
                },
                "discovered_work": [
                    {"goal": "两款车型的车身尺寸有何差异?", "rationale": "重复"}
                ],
            },
        },
    )

    assert result["noop"] is True


def test_planner_applies_live_user_steering_to_pending_work() -> None:
    intent = _intent()
    result = _call(
        RESEARCH_PLANNER_ID,
        {
            "trigger": {
                "kind": "user_change",
                "details": {
                    "request_id": "steer-1",
                    "feedback": "优先比较两款车在冬季的续航表现",
                },
            },
            "context": {
                "intent": {
                    "intent_id": intent["intent_id"],
                    "version": intent["version"],
                    "binding_hash": compute_fingerprint(intent),
                    "goal": intent["goal"],
                },
                "graph": {
                    "graph_id": "graph-1",
                    "revision": 1,
                    "tasks": [
                        {
                            "ref": {"kind": "task", "id": "price", "revision": 1},
                            "goal": "价格对比",
                            "status": "pending",
                            "priority": 60,
                            "required": True,
                            "supports": ["core-answer"],
                            "depends_on": [],
                        }
                    ],
                },
                "budgets": {"max_tasks": 8, "active_tasks": 1},
                "authority_boundaries": {
                    "child_templates": [
                        {
                            "id": "research-dimension",
                            "fingerprint": "sha256:dimension",
                        }
                    ]
                },
            },
        },
    )

    assert result.trigger == "user_change"
    assert len(result.operations) == 1
    replacement = result.operations[0].task
    assert replacement is not None
    assert replacement.task_id == "price"
    assert replacement.task_revision == 2
    assert replacement.priority == 100
    assert "冬季" in replacement.goal


def test_planner_adds_live_steering_task_when_all_existing_work_is_running() -> None:
    intent = _intent()
    result = _call(
        RESEARCH_PLANNER_ID,
        {
            "trigger": {
                "kind": "user_change",
                "details": {"feedback": "补充比较实际充电速度"},
            },
            "context": {
                "intent": {
                    "intent_id": intent["intent_id"],
                    "version": intent["version"],
                    "binding_hash": compute_fingerprint(intent),
                    "goal": intent["goal"],
                },
                "graph": {
                    "graph_id": "graph-1",
                    "revision": 1,
                    "tasks": [
                        {
                            "ref": {"kind": "task", "id": "price", "revision": 1},
                            "goal": "价格对比",
                            "status": "running",
                            "priority": 60,
                        }
                    ],
                },
                "budgets": {"max_tasks": 8, "active_tasks": 1},
                "authority_boundaries": {
                    "child_templates": [
                        {
                            "id": "research-dimension",
                            "fingerprint": "sha256:dimension",
                        }
                    ]
                },
            },
        },
    )

    assert result.operations[0].op == "add_task"
    added = result.operations[0].task
    assert added is not None and added.task_revision == 1
    assert "充电速度" in added.goal


def test_context_builder_projects_live_user_steering() -> None:
    patch = _call(RESEARCH_PLANNER_ID, _seed_inputs())
    task = patch.operations[0].task
    assert task is not None

    output = _call(
        RESEARCH_CONTEXT_BUILDER_ID,
        {
            "intent": _intent(),
            "task": {
                "task_id": task.task_id,
                "goal": task.goal,
                "depends_on": [],
            },
            "dependency_outputs": [],
            "committed_results": [],
            "user_steering": [
                {
                    "request_id": "steer-1",
                    "feedback": "重点看冬季续航",
                    "received_at": "2026-07-22T10:00:00Z",
                }
            ],
        },
    )

    steering = output["context_manifest"]["research_context"]["user_steering"]
    assert steering[0]["feedback"] == "重点看冬季续航"


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
    assert manifest["research_task"]["authority_bindings"] == []
    assert manifest["research_task"]["authority_binding_fingerprint"] == (
        authority_binding_fingerprint([])
    )
    assert manifest["dependencies"] == ["dimensions"]
    assert manifest["dependency_output_refs"] == ["submission://dimensions/result"]
    assert "candidate_dimensions" not in manifest["intent"]


def test_context_builder_fails_closed_for_missing_or_duplicate_dimension_id() -> None:
    task = {"task_id": "missing", "goal": "Readable title", "depends_on": []}
    with pytest.raises(ValueError, match="exactly one candidate dimension"):
        _call(
            RESEARCH_CONTEXT_BUILDER_ID,
            {"intent": _intent(), "task": task, "dependency_outputs": []},
        )

    duplicate = _intent()
    duplicate["planning_context"]["candidate_dimensions"].append(
        dict(duplicate["planning_context"]["candidate_dimensions"][0])
    )
    with pytest.raises(ValueError, match="exactly one candidate dimension"):
        _call(
            RESEARCH_CONTEXT_BUILDER_ID,
            {
                "intent": duplicate,
                "task": {"task_id": "dimensions", "goal": "Readable title"},
                "dependency_outputs": [],
            },
        )


def test_planner_allows_builtin_authority_policy_without_explicit_bindings() -> None:
    intent = _intent()
    planning_context = intent["planning_context"]
    planning_context["subject"] = "Government public records"
    dimension = planning_context["candidate_dimensions"][0]
    dimension.update(
        {
            "title": "Public record",
            "question": "What does the government public record establish?",
            "entities": ["Government agency"],
            "dimension": "official public record",
            "authority_bindings": [],
        }
    )

    patch = _call(RESEARCH_PLANNER_ID, _seed_inputs(intent))

    task = patch.operations[0].task
    assert task is not None
    assert task.goal == "Public record"


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


@pytest.mark.parametrize(
    ("field", "replacement", "reason"),
    [
        ("task_id", "price", "task_id"),
        ("question", "A weaker question", "question"),
        ("verification_method", "single_source_sufficient", "verification_method"),
    ],
)
def test_task_verifier_rejects_immutable_contract_substitution(
    field: str,
    replacement: str,
    reason: str,
) -> None:
    candidate = _finding()
    candidate[field] = replacement

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert reason in result["reason"]


def test_task_verifier_rejects_stale_authority_binding_fingerprint() -> None:
    candidate = _finding()
    candidate["provenance"]["authority_binding_fingerprint"] = (
        authority_binding_fingerprint(
            [{"host": "old.example", "source_type": "official"}]
        )
    )

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert "stale or forged" in result["reason"]


def test_task_verifier_rejects_forged_authoritative_source_type() -> None:
    candidate = _finding()
    candidate["evidence"][0]["source_url"] = "https://unbound-blog.test/model-y"
    candidate["citations"] = ["https://unbound-blog.test/model-y"]
    candidate["provenance"]["evaluated_urls"] = [
        "https://unbound-blog.test/model-y"
    ]
    candidate["provenance"]["searches"][0]["usable_urls"] = [
        "https://unbound-blog.test/model-y"
    ]

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert "source_type is not canonical" in result["reason"]


def test_task_verifier_rejects_sourced_finding_without_method_coverage() -> None:
    intent = _intent()
    dimension = intent["planning_context"]["candidate_dimensions"][0]
    dimension["verification_method"] = "dual_independent_required"
    dimension["authority_bindings"] = []
    candidate = _finding()
    candidate["verification_method"] = "dual_independent_required"
    candidate["evidence"][0]["source_type"] = "secondary"
    candidate["operation_summary"]["verification_method"] = (
        "dual_independent_required"
    )
    candidate["provenance"]["authority_binding_fingerprint"] = (
        authority_binding_fingerprint([])
    )

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": intent,
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert "does not satisfy its verification_method" in result["reason"]


def test_task_verifier_rejects_conclusion_stronger_than_evidence_claim() -> None:
    candidate = _finding()
    candidate["conclusion"] = "Tesla Model Y is categorically larger in every dimension."

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert "evidence claim does not match its conclusion" in result["reason"]


def test_task_verifier_rejects_hidden_contradicting_evaluation() -> None:
    intent = _intent()
    dimension = intent["planning_context"]["candidate_dimensions"][0]
    dimension["verification_method"] = "contradiction_sensitive"
    dimension["authority_bindings"] = []
    candidate = _finding()
    claim = candidate["conclusion"]
    supporting = [
        {
            "claim": claim,
            "source_url": f"https://source-{index}.example/evidence",
            "source_type": "secondary",
            "stance": "supporting",
            "independence": "independent",
            "directness": "direct",
            "as_of": "2026-07-18",
        }
        for index in (1, 2)
    ]
    hidden_url = "https://source-3.example/contradiction"
    contradicting = {
        "claim": claim,
        "source_url": hidden_url,
        "source_type": "secondary",
        "stance": "contradicting",
        "independence": "independent",
        "directness": "direct",
        "as_of": "2026-07-18",
    }
    forged_unrelated = {**contradicting, "stance": "unrelated"}
    candidate.update(
        {
            "confidence": "low",
            "verification_method": "contradiction_sensitive",
            "evidence": supporting,
            "citations": [item["source_url"] for item in supporting],
        }
    )
    candidate["operation_summary"].update(
        {
            "verification_method": "contradiction_sensitive",
            "evidence_count": 2,
            "citation_count": 2,
        }
    )
    candidate["provenance"].update(
        {
            "evaluated_urls": [
                *(item["source_url"] for item in supporting),
                hidden_url,
            ],
            "evaluations": [*supporting, forged_unrelated],
            "authority_binding_fingerprint": authority_binding_fingerprint([]),
        }
    )
    candidate["provenance"]["searches"][0]["usable_urls"] = [
        *(item["source_url"] for item in supporting),
        hidden_url,
    ]
    fingerprint = authority_binding_fingerprint([])
    trusted_verification = {
        "verification_id": "verification-1",
        "task_id": "dimensions",
        "claim": claim,
        "search_ids": ["search-1"],
        "evaluated_urls": [
            *(item["source_url"] for item in supporting),
            hidden_url,
        ],
        "evaluations": [*supporting, contradicting],
        "evidence": [*supporting, contradicting],
        "authority_binding_fingerprint": fingerprint,
        "operation_summary": {
            "verification_id": "verification-1",
            "task_id": "dimensions",
            "search_ids": ["search-1"],
            "evaluated_url_count": 3,
            "evidence_count": 3,
            "authority_binding_fingerprint": fingerprint,
        },
    }

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": intent,
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
            "trusted_submission_context": {
                "operation_attestations": [
                    {
                        "tool_name": "get_current_time",
                        "argument_scalars": {},
                        "result_scalars": {
                            "time_token": "time-1",
                            "current_date": "2026-07-18",
                        },
                        "result_fingerprint": "unused",
                    },
                    {
                        "tool_name": "public_web_search",
                        "argument_scalars": {"time_token": "time-1"},
                        "result_scalars": {"search_id": "search-1"},
                        "result_fingerprint": "unused",
                    },
                    {
                        "tool_name": "verify_claim_evidence",
                        "argument_scalars": {},
                        "result_scalars": {
                            "verification_id": "verification-1"
                        },
                        "result_fingerprint": compute_fingerprint(
                            trusted_verification
                        ),
                    },
                ]
            },
        },
    )

    assert result["outcome"] == "repairable"
    assert "does not match the trusted verification output" in result["reason"]


def test_task_verifier_rejects_forged_search_provenance() -> None:
    candidate = _finding()
    provenance = candidate["provenance"]
    original_searches = provenance["searches"][0]["structured_searches"]
    fingerprint = provenance["authority_binding_fingerprint"]
    trusted_verification = {
        "verification_id": candidate["verification_id"],
        "task_id": candidate["task_id"],
        "claim": candidate["conclusion"],
        "search_ids": provenance["search_ids"],
        "evaluated_urls": provenance["evaluated_urls"],
        "evaluations": provenance["evaluations"],
        "evidence": candidate["evidence"],
        "authority_binding_fingerprint": fingerprint,
        "operation_summary": {
            "verification_id": candidate["verification_id"],
            "task_id": candidate["task_id"],
            "search_ids": provenance["search_ids"],
            "evaluated_url_count": len(provenance["evaluated_urls"]),
            "evidence_count": len(candidate["evidence"]),
            "authority_binding_fingerprint": fingerprint,
        },
    }
    provenance["searches"][0]["structured_searches"] = [
        {
            "query": "forged query",
            "entity": "forged entity",
            "aliases": [],
            "dimension": "forged dimension",
        }
    ]
    trusted_context = {
        "operation_attestations": [
            {
                "tool_name": "get_current_time",
                "argument_scalars": {},
                "argument_fingerprints": {},
                "result_scalars": {
                    "time_token": "time-1",
                    "issued_at": "2026-07-18T03:00:00.000Z",
                    "current_date": "2026-07-18",
                    "timezone": "Asia/Shanghai",
                },
                "result_fingerprint": "unused",
                "operation_summary": {},
            },
            {
                "tool_name": "public_web_search",
                "argument_scalars": {"time_token": "time-1"},
                "argument_fingerprints": {
                    "searches": compute_fingerprint(original_searches)
                },
                "result_scalars": {"search_id": "search-1"},
                "result_fingerprint": "unused",
                "operation_summary": {
                    "usable_sources": [
                        {"url": "https://example.test/model-y", "title": "Specs"}
                    ]
                },
            },
            {
                "tool_name": "verify_claim_evidence",
                "argument_scalars": {},
                "argument_fingerprints": {},
                "result_scalars": {"verification_id": "verification-1"},
                "result_fingerprint": compute_fingerprint(trusted_verification),
                "operation_summary": {},
            },
        ]
    }

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
            "trusted_submission_context": trusted_context,
        },
    )

    assert result["outcome"] == "repairable"
    assert "structured search provenance is forged" in result["reason"]


def test_task_verifier_rejects_forged_confidence() -> None:
    candidate = _finding()
    candidate["confidence"] = "high"

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert "confidence does not match" in result["reason"]


def test_task_verifier_recomputes_confidence_at_persisted_search_date() -> None:
    intent = _intent()
    dimension = intent["planning_context"]["candidate_dimensions"][0]
    dimension["verification_method"] = "dual_independent_required"
    dimension["authority_bindings"] = [
        {"host": f"source-{index}.example", "source_type": "official"}
        for index in (1, 2)
    ]
    candidate = _finding()
    evidence = [
        {
            "claim": candidate["conclusion"],
            "source_url": f"https://source-{index}.example/record",
            "source_type": "official",
            "stance": "supporting",
            "independence": "independent",
            "directness": "direct",
            "as_of": "2020-01-02",
        }
        for index in (1, 2)
    ]
    candidate.update(
        {
            "confidence": "high",
            "verification_method": "dual_independent_required",
            "evidence": evidence,
            "citations": [item["source_url"] for item in evidence],
        }
    )
    candidate["operation_summary"].update(
        {
            "verification_method": "dual_independent_required",
            "evidence_count": 2,
            "citation_count": 2,
        }
    )
    candidate["provenance"].update(
        {
            "evaluated_urls": [item["source_url"] for item in evidence],
            "evaluations": evidence,
            "authority_binding_fingerprint": authority_binding_fingerprint(
                dimension["authority_bindings"]
            ),
        }
    )
    candidate["provenance"]["searches"][0].update(
        {
            "usable_urls": [item["source_url"] for item in evidence],
            "current_time": {
                "issued_at": "2020-04-01T00:00:00Z",
                "current_date": "2020-04-01",
                "timezone": "Asia/Shanghai",
            },
        }
    )

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": intent,
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result == {
        "outcome": "passed",
        "evidence_refs": [item["source_url"] for item in evidence],
    }


def test_task_verifier_rejects_blocked_finding_without_exact_coverage_gap() -> None:
    candidate = _finding(status="blocked")
    candidate["limitations"] = ["No material limitation."]

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert "must retain its exact verification gap" in result["reason"]


def test_task_verifier_rejects_unverifiable_finding_with_evidence() -> None:
    intent = _intent()
    dimension = intent["planning_context"]["candidate_dimensions"][0]
    dimension["verification_method"] = "unverifiable_flag"
    dimension["authority_bindings"] = []
    candidate = _finding(status="blocked")
    candidate["verification_method"] = "unverifiable_flag"
    candidate["verification_id"] = ""
    candidate["evidence"] = [
        {
            "claim": candidate["conclusion"],
            "source_url": "https://reference.example/model-y",
            "source_type": "secondary",
            "stance": "supporting",
            "independence": "independent",
            "directness": "indirect",
        }
    ]
    candidate["citations"] = ["https://reference.example/model-y"]
    candidate["operation_summary"].update(
        {
            "verification_id": None,
            "verification_method": "unverifiable_flag",
            "evidence_count": 1,
            "citation_count": 1,
            "search_count": 0,
        }
    )
    candidate["provenance"] = {
        "verification_id": "",
        "search_ids": [],
        "evaluated_urls": [],
        "evaluations": [],
        "searches": [],
        "authority_binding_fingerprint": authority_binding_fingerprint([]),
    }

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": intent,
            "task": {"task_id": "dimensions"},
            "candidate": candidate,
        },
    )

    assert result["outcome"] == "repairable"
    assert "must not contain evidence" in result["reason"]


def test_task_verifier_rejects_missing_or_incomplete_provenance_and_limitations() -> None:
    missing = _finding()
    missing.pop("provenance")
    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": missing,
        },
    )
    assert result["outcome"] == "repairable"
    assert "provenance" in result["reason"]

    incomplete = _finding()
    incomplete["provenance"]["evaluated_urls"] = []
    incomplete["provenance"]["evaluations"] = []
    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": incomplete,
        },
    )
    assert result["outcome"] == "repairable"
    assert "evaluations" in result["reason"]

    blocked = _finding(status="blocked")
    blocked["limitations"] = []
    blocked["operation_summary"]["limitation_count"] = 0
    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": _intent(),
            "task": {"task_id": "dimensions"},
            "candidate": blocked,
        },
    )
    assert result["outcome"] == "repairable"
    assert "limitation" in result["reason"]


def test_unverifiable_blocker_requires_explicit_empty_provenance() -> None:
    intent = _intent()
    dimension = intent["planning_context"]["candidate_dimensions"][0]
    dimension["verification_method"] = "unverifiable_flag"
    dimension["authority_bindings"] = []
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
        "evaluations": [],
        "searches": [],
        "authority_binding_fingerprint": authority_binding_fingerprint([]),
    }

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": intent,
            "task": {"task_id": "dimensions"},
            "candidate": finding,
            "trusted_submission_context": {"operation_attestations": []},
        },
    )

    assert result == {"outcome": "passed", "evidence_refs": []}


def test_unverifiable_blocker_rejects_persisted_time_operation() -> None:
    intent = _intent()
    dimension = intent["planning_context"]["candidate_dimensions"][0]
    dimension["verification_method"] = "unverifiable_flag"
    dimension["authority_bindings"] = []
    finding = _finding(status="blocked")
    finding["verification_method"] = "unverifiable_flag"
    finding["verification_id"] = ""
    finding["operation_summary"].update(
        {
            "verification_id": None,
            "verification_method": "unverifiable_flag",
            "search_count": 0,
        }
    )
    finding["provenance"] = {
        "verification_id": "",
        "search_ids": [],
        "evaluated_urls": [],
        "evaluations": [],
        "searches": [],
        "authority_binding_fingerprint": authority_binding_fingerprint([]),
    }

    result = _call(
        RESEARCH_TASK_VERIFIER_ID,
        {
            "intent": intent,
            "task": {"task_id": "dimensions"},
            "candidate": finding,
            "trusted_submission_context": {
                "operation_attestations": [
                    {
                        "tool_name": "get_current_time",
                        "argument_scalars": {},
                        "argument_fingerprints": {},
                        "result_scalars": {
                            "time_token": "time-1",
                            "current_date": "2026-07-18",
                        },
                        "result_fingerprint": "unused",
                        "operation_summary": {},
                    }
                ]
            },
        },
    )

    assert result["outcome"] == "repairable"
    assert "unexpected trusted research operations" in result["reason"]


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
