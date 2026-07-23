"""Research-specific pinned components for the generic Task Graph runtime.

The generic runtime owns graph state, scheduling, durable invocations, and
parent/child fencing.  This module owns the application semantics: turning a
confirmed research Intent into dimension Tasks and deciding whether a child
returned a canonical, fully provenance-bound Finding.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, cast

from modi_harness._utils import compute_fingerprint
from modi_harness.long_task import (
    CompletionContract,
    DependencyRef,
    ExecutorBinding,
    ExecutorPolicy,
    GraphPatch,
    GraphPatchOperation,
    TaskRun,
)
from modi_harness.workflow import (
    CompletionValidator,
    PinnedComponent,
    SchemaDefinition,
    SchemaRegistry,
)

from . import confidence as confidence_policy

RESEARCH_PLANNER_ID = "research-planner"
RESEARCH_GRAPH_POLICY_ID = "research-graph-policy"
RESEARCH_CONTEXT_BUILDER_ID = "research-context-builder"
RESEARCH_TASK_VERIFIER_ID = "research-task-verifier"
RESEARCH_CRITERION_VERIFIER_ID = "research-criterion-verifier"
RESEARCH_GOAL_VERIFIER_ID = "research-goal-verifier"

RESEARCH_CHILD_TEMPLATE_ID = "research-dimension"
RESEARCH_INTENT_SCHEMA_ID = "research-intent-v1"
RESEARCH_FINDING_SCHEMA_ID = "research-finding-v1"
RESEARCH_TASK_GRAPH_RESULT_SCHEMA_ID = "research-task-graph-result-v1"
RESEARCH_TASK_GRAPH_RESULT_VALIDATOR_ID = "research-task-graph-result"

_PROTOCOL_VERSION = "research-long-task-v1"
_VERIFICATION_METHODS = frozenset(
    {
        "single_source_sufficient",
        "dual_independent_required",
        "official_primary_required",
        "contradiction_sensitive",
        "unverifiable_flag",
    }
)
_SOURCE_TYPES = frozenset(
    {
        "official",
        "primary",
        "reputable_media",
        "industry_report",
        "job_board",
        "secondary",
    }
)
_FINDING_FIELDS = frozenset(
    {
        "task_id",
        "question",
        "conclusion",
        "implications",
        "confidence",
        "verification_method",
        "verification_id",
        "status",
        "evidence",
        "citations",
        "limitations",
        "task_resolution",
        "operation_summary",
        "provenance",
    }
)
_AUTHORITY_SOURCE_TYPES = frozenset({"official", "primary"})
_MAX_AUTHORITY_BINDINGS = 8
_SECONDARY_DOMAIN_CAPS = (
    "wikipedia.org",
    "plato.stanford.edu",
    "iep.utm.edu",
    "britannica.com",
    "thoughtco.com",
    "sparknotes.com",
    "cliffsnotes.com",
    "coursehero.com",
    "study.com",
)
_BUILTIN_OFFICIAL_SUFFIXES = (
    ".gov",
    ".gov.au",
    ".gov.cn",
    ".gov.uk",
    ".gc.ca",
    ".europa.eu",
)
_MULTI_LABEL_PUBLIC_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "gov.uk",
        "com.cn",
        "org.cn",
        "gov.cn",
        "com.au",
        "org.au",
        "gov.au",
        "gc.ca",
    }
)


def build_research_schema_registry() -> SchemaRegistry:
    """Return the closed schemas used by the Research Assistant migration."""

    registry = SchemaRegistry()
    for definition in _schema_definitions():
        registry.register(definition)
    return registry


def build_research_completion_validators() -> tuple[CompletionValidator, ...]:
    """Return Workflow-level validators referenced by the research graph Node."""

    return (
        CompletionValidator(
            id=RESEARCH_TASK_GRAPH_RESULT_VALIDATOR_ID,
            version="1",
            validate=_valid_task_graph_result,
            explain=lambda _value: "research Goal verification did not pass",
        ),
    )


def build_research_components() -> tuple[PinnedComponent, ...]:
    """Return the complete immutable component set registered by ``agent.py``."""

    return (
        _component(
            RESEARCH_PLANNER_ID,
            "planner",
            _research_planner,
            outcomes=("needs_replan",),
        ),
        _component(
            RESEARCH_GRAPH_POLICY_ID,
            "graph_policy",
            _research_graph_policy,
            outcomes=("passed",),
        ),
        _component(
            RESEARCH_CONTEXT_BUILDER_ID,
            "context_builder",
            _research_context_builder,
            outcomes=("passed",),
        ),
        _component(
            RESEARCH_TASK_VERIFIER_ID,
            "task_verifier",
            _research_task_verifier,
            outcomes=("passed", "repairable", "needs_replan"),
        ),
        _component(
            RESEARCH_CRITERION_VERIFIER_ID,
            "criterion_verifier",
            _research_criterion_verifier,
            outcomes=("passed", "repairable"),
        ),
        _component(
            RESEARCH_GOAL_VERIFIER_ID,
            "goal_verifier",
            _research_goal_verifier,
            outcomes=("passed", "repairable_gap", "impossible"),
        ),
    )


def _component(
    component_id: str,
    kind: str,
    implementation: Any,
    *,
    outcomes: tuple[str, ...],
) -> PinnedComponent:
    digest = compute_fingerprint(
        {
            "id": component_id,
            "protocol": _PROTOCOL_VERSION,
            "implementation_revision": 4,
        }
    )
    return PinnedComponent(
        id=component_id,
        version="1",
        kind=cast(Any, kind),
        implementation_digest=f"sha256:{digest}",
        protocol_version=_PROTOCOL_VERSION,
        input_schema_id=f"{component_id}-input-v1",
        output_schema_id=f"{component_id}-output-v1",
        supported_outcomes=cast(Any, outcomes),
        configuration={"application": "research-assistant"},
        implementation=implementation,
    )


def _research_planner(
    inputs: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> GraphPatch | Mapping[str, Any]:
    del idempotency_key
    if inputs.get("trigger") == "seed":
        return _seed_research_graph(inputs)
    return _extend_research_graph(inputs)


def _seed_research_graph(inputs: Mapping[str, Any]) -> GraphPatch:
    intent = _mapping(inputs.get("intent"), "intent")
    graph = _mapping(inputs.get("graph"), "graph")
    if _integer(graph, "revision") != 0:
        raise ValueError("Research Planner seed requires graph revision zero")
    if intent.get("status") != "confirmed":
        raise ValueError("Research Planner requires a confirmed Intent")
    graph_id = _string(graph, "graph_id")
    intent_version = _integer(intent, "version")
    if intent_version < 1:
        raise ValueError("Intent version must be positive")
    template = _research_template(inputs.get("allowed_child_templates"))
    binding = ExecutorBinding(
        mode="child_agent",
        id=RESEARCH_CHILD_TEMPLATE_ID,
        component_fingerprint=_string(template, "fingerprint"),
    )
    criteria = _criteria(intent)
    dimensions = _candidate_dimensions(intent, criteria)
    if not dimensions:
        raise ValueError("confirmed research Intent has no candidate dimensions or criteria")
    intent_hash = compute_fingerprint(_plain(intent))

    tasks: list[TaskRun] = []
    known_ids = [item["id"] for item in dimensions]
    if len(known_ids) != len(set(known_ids)):
        raise ValueError("research candidate dimension ids must be unique")
    for index, dimension in enumerate(dimensions):
        dependency_ids = _string_items(
            dimension.get("depends_on", ()),
            "candidate dimension depends_on",
        )
        if dimension["id"] in dependency_ids:
            raise ValueError("research candidate dimension cannot depend on itself")
        unknown = sorted(set(dependency_ids) - set(known_ids))
        if unknown:
            raise ValueError(
                "research candidate dimension has unknown dependency: " + ", ".join(unknown)
            )
        supports = _dimension_supports(
            dimension,
            criteria,
            dimension_index=index,
            dimension_count=len(dimensions),
        )
        task = TaskRun(
            task_id=dimension["id"],
            task_revision=1,
            graph_id=graph_id,
            intent_version=intent_version,
            intent_binding_hash=intent_hash,
            intent_binding_state="current",
            goal=str(dimension["title"]),
            supports=supports,
            depends_on=tuple(
                DependencyRef("task", dependency_id, 1) for dependency_id in dependency_ids
            ),
            priority=_priority(dimension.get("priority", 50)),
            required=False,
            kind="executable",
            completion_contract=CompletionContract(
                output_schema_id=RESEARCH_FINDING_SCHEMA_ID,
                validator_ids=(RESEARCH_TASK_VERIFIER_ID,),
                required_evidence=("verified-public-url-provenance",),
            ),
            executor_policy=ExecutorPolicy((binding,), binding),
        )
        tasks.append(task)

    return GraphPatch(
        base_revision=0,
        trigger="seed",
        reason="seed one isolated child Task per confirmed research dimension",
        operations=tuple(GraphPatchOperation("add_task", task=task) for task in tasks),
    )


def _extend_research_graph(inputs: Mapping[str, Any]) -> GraphPatch | Mapping[str, Any]:
    """Turn bounded child suggestions into the next search wave."""

    trigger = _mapping(inputs.get("trigger"), "trigger")
    trigger_kind = _string(trigger, "kind")
    if trigger_kind == "user_change":
        return _apply_user_steering(inputs, trigger)
    if trigger_kind != "discovered_work":
        raise ValueError(f"Research Planner cannot handle trigger {trigger_kind!r}")
    context = _mapping(inputs.get("context"), "context")
    graph = _mapping(context.get("graph"), "graph")
    intent = _mapping(context.get("intent"), "intent")
    graph_id = _string(graph, "graph_id")
    graph_revision = _integer(graph, "revision")
    intent_version = _integer(intent, "version")
    intent_hash = _string(intent, "binding_hash")
    binding = ExecutorBinding(
        mode="child_agent",
        id=RESEARCH_CHILD_TEMPLATE_ID,
        component_fingerprint=_rolling_template_fingerprint(context),
    )
    tasks = graph.get("tasks", ())
    if not isinstance(tasks, tuple | list):
        raise ValueError("rolling research graph tasks must be an array")
    if any(
        isinstance(raw, Mapping)
        and not _task_ref_id(cast(Mapping[str, Any], raw)).startswith("followup-")
        and raw.get("status") not in {"completed", "failed", "cancelled"}
        for raw in tasks
    ):
        return {
            "noop": True,
            "reason": "defer discovered work until the initial research wave is terminal",
        }
    known_goal_values = [
        _normalized_text(_task_question_from_projection(item))
        for raw in tasks
        if isinstance(raw, Mapping)
        and (item := cast(Mapping[str, Any], raw)).get("status") != "cancelled"
    ]
    known_goals = set(known_goal_values)
    known_ids = {
        str(ref.get("id") or "")
        for raw in tasks
        if isinstance(raw, Mapping)
        and isinstance((ref := raw.get("ref")), Mapping)
    }
    discovered = context.get("discovered_work", ())
    if not isinstance(discovered, tuple | list):
        raise ValueError("rolling research discovered_work must be an array")
    claimed_coverage = _claimed_coverage_ids(intent, tasks)
    budget = _mapping(context.get("budgets"), "budgets")
    remaining = max(0, _integer(budget, "max_tasks") - _integer(budget, "active_tasks"))
    operations: list[GraphPatchOperation] = []
    for raw in discovered:
        if len(operations) >= min(2, remaining):
            break
        item = _mapping(raw, "discovered work")
        question = _normalized_text(item.get("goal"))
        rationale = _normalized_text(item.get("rationale"))
        coverage_ids = _coverage_ids_from_text(f"{question} {rationale}")
        if (
            not question
            or question in known_goals
            or coverage_ids.intersection(claimed_coverage)
            or any(_research_questions_overlap(question, known) for known in known_goal_values)
        ):
            continue
        task_id = _discovered_task_id(question, known_ids)
        known_ids.add(task_id)
        known_goals.add(question)
        known_goal_values.append(question)
        claimed_coverage.update(coverage_ids)
        dimension = {
            "schema_version": "research-task-goal-v1",
            "id": task_id,
            "title": question[:200],
            "question": question,
            "entities": _entities_from_question(question),
            "dimension": rationale[:200] or "搜索中新发现的关键信息缺口",
            "coverage_ids": sorted(coverage_ids),
            "verification_method": "single_source_sufficient",
            "authority_bindings": [],
            "authority_binding_fingerprint": authority_binding_fingerprint([]),
            "constraints": [],
        }
        task = TaskRun(
            task_id=task_id,
            task_revision=1,
            graph_id=graph_id,
            intent_version=intent_version,
            intent_binding_hash=intent_hash,
            intent_binding_state="current",
            goal=json.dumps(dimension, ensure_ascii=False, sort_keys=True),
            supports=(),
            depends_on=(),
            priority=max(55, 75 - len(operations) * 5),
            required=False,
            kind="executable",
            completion_contract=CompletionContract(
                output_schema_id=RESEARCH_FINDING_SCHEMA_ID,
                validator_ids=(RESEARCH_TASK_VERIFIER_ID,),
                required_evidence=("verified-public-url-provenance",),
            ),
            executor_policy=ExecutorPolicy((binding,), binding),
        )
        operations.append(GraphPatchOperation("add_task", task=task))
    if not operations:
        return {
            "noop": True,
            "reason": "all discovered questions are duplicates or outside the remaining budget",
        }
    return GraphPatch(
        base_revision=graph_revision,
        trigger="discovered_work",
        reason="add bounded high-value questions discovered by completed research",
        operations=tuple(operations),
    )


def _apply_user_steering(
    inputs: Mapping[str, Any],
    trigger: Mapping[str, Any],
) -> GraphPatch:
    """Make live user feedback the next bounded search priority."""

    context = _mapping(inputs.get("context"), "context")
    graph = _mapping(context.get("graph"), "graph")
    intent = _mapping(context.get("intent"), "intent")
    details = _mapping(trigger.get("details"), "trigger details")
    feedback = _normalized_text(details.get("feedback"))
    if not feedback:
        raise ValueError("user_change trigger requires non-empty feedback")
    tasks = graph.get("tasks", ())
    if not isinstance(tasks, tuple | list):
        raise ValueError("rolling research graph tasks must be an array")
    pending = [
        cast(Mapping[str, Any], item)
        for item in tasks
        if isinstance(item, Mapping) and item.get("status") == "pending"
    ]
    target = (
        sorted(
            pending,
            key=lambda item: (-int(item.get("priority") or 0), _task_ref_id(item)),
        )[0]
        if pending
        else None
    )
    target_ref = (
        _mapping(target.get("ref"), "pending Task ref")
        if target is not None
        else None
    )
    task_id = (
        _string(target_ref, "id")
        if target_ref is not None
        else _discovered_task_id(
            feedback,
            {
                _task_ref_id(item)
                for item in tasks
                if isinstance(item, Mapping)
            },
        )
    )
    task_revision = _integer(target_ref, "revision") if target_ref is not None else 0
    dimension = {
        "schema_version": "research-task-goal-v1",
        "id": task_id,
        "title": feedback[:200],
        "question": feedback,
        "entities": _entities_from_question(feedback),
        "dimension": "用户运行中补充的研究重点",
        "verification_method": "single_source_sufficient",
        "authority_bindings": [],
        "authority_binding_fingerprint": authority_binding_fingerprint([]),
        "constraints": [],
    }
    current_task = TaskRun(
        task_id=task_id,
        task_revision=task_revision + 1,
        graph_id=_string(graph, "graph_id"),
        intent_version=_integer(intent, "version"),
        intent_binding_hash=_string(intent, "binding_hash"),
        intent_binding_state="current",
        goal=json.dumps(dimension, ensure_ascii=False, sort_keys=True),
        supports=(
            tuple(str(item) for item in target.get("supports", ()))
            if target is not None
            else ("core-answer",)
        ),
        depends_on=(
            tuple(_dependency_ref(item) for item in target.get("depends_on", ()))
            if target is not None
            else ()
        ),
        priority=100,
        required=False,
        kind="executable",
        completion_contract=CompletionContract(
            output_schema_id=RESEARCH_FINDING_SCHEMA_ID,
            validator_ids=(RESEARCH_TASK_VERIFIER_ID,),
            required_evidence=("verified-public-url-provenance",),
        ),
        executor_policy=ExecutorPolicy(
            (
                ExecutorBinding(
                    mode="child_agent",
                    id=RESEARCH_CHILD_TEMPLATE_ID,
                    component_fingerprint=_rolling_template_fingerprint(context),
                ),
            ),
            ExecutorBinding(
                mode="child_agent",
                id=RESEARCH_CHILD_TEMPLATE_ID,
                component_fingerprint=_rolling_template_fingerprint(context),
            ),
        ),
    )
    operation = (
        GraphPatchOperation(
            "replace_pending_task",
            task_id=task_id,
            expected_revision=task_revision,
            task=current_task,
        )
        if target is not None
        else GraphPatchOperation("add_task", task=current_task)
    )
    return GraphPatch(
        base_revision=_integer(graph, "revision"),
        trigger="user_change",
        reason="apply live user steering to pending research work",
        operations=(operation,),
    )


def _task_ref_id(task: Mapping[str, Any]) -> str:
    ref = task.get("ref")
    return str(ref.get("id") or "") if isinstance(ref, Mapping) else ""


def _dependency_ref(value: Any) -> DependencyRef:
    item = _mapping(value, "Task dependency")
    return DependencyRef(
        cast(Any, _string(item, "kind")),
        _string(item, "id"),
        _integer(item, "revision"),
    )


def _research_graph_policy(
    inputs: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> Mapping[str, Any]:
    """Conservatively invalidate prior application outputs after Intent change."""

    del idempotency_key
    candidates = inputs.get("candidates", ())
    if not isinstance(candidates, tuple | list):
        raise ValueError("graph policy candidates must be an array")
    decisions: list[dict[str, Any]] = []
    for candidate in candidates:
        item = _mapping(candidate, "graph policy candidate")
        target_ref = _mapping(item.get("target_ref"), "candidate target_ref")
        decisions.append(
            {
                "target_ref": {
                    "kind": _string(target_ref, "kind"),
                    "id": _string(target_ref, "id"),
                    "revision": _integer(target_ref, "revision"),
                },
                "reusable": False,
            }
        )
    return {"outcome": "passed", "reuse_decisions": decisions}


def _research_context_builder(
    inputs: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> Mapping[str, Any]:
    del idempotency_key
    intent = _mapping(inputs.get("intent"), "intent")
    task = _mapping(inputs.get("task"), "task")
    research_task = _research_task_for_run(intent, task)
    dependencies = task.get("depends_on", ())
    if not isinstance(dependencies, tuple | list):
        raise ValueError("task depends_on must be an array")
    direct_ids = []
    for dependency in dependencies:
        ref = _mapping(dependency, "task dependency")
        if _string(ref, "kind") != "task":
            raise ValueError("research Task dependencies must reference Tasks")
        direct_ids.append(_string(ref, "id"))
    dependency_outputs = inputs.get("dependency_outputs", {})
    if not isinstance(dependency_outputs, tuple | list):
        raise ValueError("dependency_outputs must be an array")
    dependency_output_refs = [str(item).strip() for item in dependency_outputs if str(item).strip()]
    committed_results = inputs.get("committed_results", ())
    if not isinstance(committed_results, tuple | list):
        raise ValueError("committed_results must be an array")
    related_results = [
        _plain(item)
        for item in committed_results[-6:]
        if isinstance(item, Mapping)
    ]
    steering = inputs.get("user_steering", ())
    if not isinstance(steering, tuple | list):
        raise ValueError("user_steering must be an array")
    planning_context = _mapping(intent.get("planning_context"), "Intent planning_context")
    exploration_sources = planning_context.get("exploration_sources", ())
    if not isinstance(exploration_sources, tuple | list):
        exploration_sources = ()
    intent_projection = {
        key: _plain(intent[key])
        for key in (
            "intent_id",
            "version",
            "goal",
            "desired_outcome",
            "constraints",
            "non_goals",
            "assumptions",
        )
        if key in intent
    }
    return {
        "context_manifest": {
            "schema_version": "research-context-v1",
            "intent": intent_projection,
            "research_task": _plain(research_task),
            "research_context": {
                "research_brief": _plain(planning_context.get("research_brief", {})),
                "landscape_map": _plain(planning_context.get("landscape_map", {})),
                "coverage_map": _plain(planning_context.get("coverage_map", {})),
                "exploration_queries": _plain(
                    planning_context.get("exploration_queries", ())
                ),
                "exploration_sources": [
                    _plain(item)
                    for item in exploration_sources[:6]
                    if isinstance(item, Mapping)
                ],
                "committed_results": related_results,
                "user_steering": [
                    _plain(item)
                    for item in steering[-5:]
                    if isinstance(item, Mapping)
                ],
                "exploration_time": _plain(
                    planning_context.get("exploration_time", {})
                ),
            },
            "dependencies": direct_ids,
            "dependency_output_refs": dependency_output_refs,
        }
    }


def _research_task_verifier(
    inputs: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> Mapping[str, Any]:
    del idempotency_key
    candidate = inputs.get("candidate")
    try:
        task = _mapping(inputs.get("task"), "task")
        task_id = _string(task, "task_id")
        intent = _mapping(inputs.get("intent"), "intent")
        dimension = _research_task_for_run(intent, task)
        expected_question = str(dimension["question"])
        expected_method = str(dimension["verification_method"])
        expected_authority_bindings = normalize_authority_bindings(dimension["authority_bindings"])
        expected_authority_fingerprint = authority_binding_fingerprint(expected_authority_bindings)
    except ValueError as exc:
        return {"outcome": "repairable", "reason": str(exc), "evidence_refs": []}
    reason = _finding_rejection_reason(
        candidate,
        expected_task_id=task_id,
        expected_question=expected_question,
        expected_method=expected_method,
        expected_authority_bindings=expected_authority_bindings,
        expected_authority_fingerprint=expected_authority_fingerprint,
        trusted_research_context=_research_attestation_view(
            _mapping_or_none(inputs.get("trusted_submission_context"))
        ),
    )
    if reason is not None:
        return {"outcome": "repairable", "reason": reason, "evidence_refs": []}
    finding = cast(Mapping[str, Any], candidate)
    return {
        "outcome": "passed",
        "evidence_refs": list(cast(Sequence[str], finding["citations"])),
    }


def _research_criterion_verifier(
    inputs: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> Mapping[str, Any]:
    del idempotency_key
    criterion = _mapping(inputs.get("criterion"), "criterion")
    criterion_id = _string(criterion, "id")
    tasks = inputs.get("tasks", ())
    groups = inputs.get("groups", ())
    if not isinstance(tasks, tuple | list) or not isinstance(groups, tuple | list):
        raise ValueError("criterion supporting tasks and groups must be arrays")
    evidence_refs: list[str] = []
    completed = 0
    for raw in (*tasks, *groups):
        item = _mapping(raw, "criterion support")
        if item.get("status") != "completed":
            continue
        completed += 1
        refs = item.get("output_refs", ())
        if isinstance(refs, tuple | list):
            evidence_refs.extend(str(ref).strip() for ref in refs if str(ref).strip())
    evidence_refs = list(dict.fromkeys(evidence_refs))
    if completed and evidence_refs:
        return {"outcome": "passed", "evidence_refs": evidence_refs}
    return {
        "outcome": "repairable",
        "reason": f"criterion {criterion_id!r} has no committed Finding output",
        "evidence_refs": [],
    }


def _research_goal_verifier(
    inputs: Mapping[str, Any],
    *,
    idempotency_key: str,
) -> Mapping[str, Any]:
    del idempotency_key
    graph = _mapping(inputs.get("graph"), "graph")
    required = set(_string_items(graph.get("required_criteria", ()), "required_criteria"))
    coverage = inputs.get("criterion_coverage", ())
    if not isinstance(coverage, tuple | list):
        raise ValueError("criterion_coverage must be an array")
    satisfied = {
        _string(item, "criterion_id")
        for raw in coverage
        if (item := _mapping(raw, "criterion coverage")).get("status") == "satisfied"
    }
    missing = sorted(required - satisfied)
    output_refs = list(
        dict.fromkeys(
            str(item).strip() for item in inputs.get("output_refs", ()) if str(item).strip()
        )
    )
    committed = inputs.get("committed_results", ())
    if not isinstance(committed, tuple | list):
        raise ValueError("committed_results must be an array")
    useful = []
    for item in committed:
        if not isinstance(item, Mapping):
            continue
        result = item.get("result", item)
        if (
            isinstance(result, Mapping)
            and str(result.get("conclusion") or "").strip()
            and result.get("status") in {"sourced", "blocked"}
        ):
            useful.append(result)
    useful_results_present = bool(useful) or "committed_results" not in inputs
    if not missing and output_refs and useful_results_present:
        return {"outcome": "passed", "evidence_refs": output_refs}
    gap = {
        "missing_criteria": missing,
        "missing_outputs": not bool(output_refs or useful),
    }
    return {
        "outcome": "repairable_gap",
        "reason": "research Goal lacks verified criterion coverage or committed Findings",
        "gap": gap,
        "evidence_refs": output_refs,
    }


def _finding_rejection_reason(
    value: Any,
    *,
    expected_task_id: str = "",
    expected_question: str = "",
    expected_method: str = "",
    expected_authority_bindings: Sequence[Mapping[str, Any]] = (),
    expected_authority_fingerprint: str = "",
    trusted_research_context: Mapping[str, Any] | None = None,
) -> str | None:
    if not isinstance(value, Mapping):
        return "candidate must be a canonical Finding mapping"
    if set(value) != _FINDING_FIELDS:
        missing = sorted(_FINDING_FIELDS - set(value))
        unknown = sorted(set(value) - _FINDING_FIELDS)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        return "canonical Finding fields are invalid: " + "; ".join(details)
    try:
        task_id = _string(value, "task_id")
        question = _string(value, "question")
        conclusion = _string(value, "conclusion")
        _string(value, "implications")
        status = _string(value, "status")
        method = _string(value, "verification_method")
        confidence_level = _string(value, "confidence")
    except ValueError as exc:
        return str(exc)
    if expected_task_id and task_id != expected_task_id:
        return "canonical Finding task_id does not match the exact Task"
    if expected_question and _normalized_text(question) != _normalized_text(expected_question):
        return "canonical Finding question does not match the confirmed research dimension"
    if expected_method and method != expected_method:
        return (
            "canonical Finding verification_method does not match the confirmed research dimension"
        )
    if status not in {"sourced", "blocked"}:
        return "canonical Finding status must be sourced or blocked"
    if method not in _VERIFICATION_METHODS:
        return "canonical Finding verification_method is unsupported"
    if confidence_level not in {"low", "medium", "high"}:
        return "canonical Finding confidence is invalid"
    expected_resolution = "completed" if status == "sourced" else "blocked"
    if value.get("task_resolution") != expected_resolution:
        return "canonical Finding task_resolution does not match status"
    limitations = value.get("limitations")
    if not _nonempty_string_array(limitations, allow_empty=True):
        return "canonical Finding limitations must be an explicit string array"
    if status == "blocked" and not limitations:
        return "a blocked canonical Finding requires an explicit limitation"

    evidence = value.get("evidence")
    if not isinstance(evidence, tuple | list):
        return "canonical Finding evidence must be an array"
    evidence_urls: list[str] = []
    seen_evidence: set[tuple[str, str]] = set()
    independent_domains: set[str] = set()
    for raw in evidence:
        if not isinstance(raw, Mapping):
            return "canonical Finding evidence items must be mappings"
        required = {
            "claim",
            "source_url",
            "source_type",
            "stance",
            "independence",
            "directness",
        }
        allowed = required | {"as_of", "excerpt"}
        if not required <= set(raw) or not set(raw) <= allowed:
            return "canonical Finding evidence has incomplete verification fields"
        try:
            claim = _string(raw, "claim")
            url = _string(raw, "source_url")
            source_type = _string(raw, "source_type")
            stance = _string(raw, "stance")
            independence = _string(raw, "independence")
            directness = _string(raw, "directness")
            if "as_of" in raw:
                _string(raw, "as_of")
            if "excerpt" in raw:
                _string(raw, "excerpt")
        except ValueError as exc:
            return str(exc)
        if not _is_http_url(url):
            return "canonical Finding evidence source_url must be http(s)"
        if source_type not in _SOURCE_TYPES:
            return "canonical Finding evidence source_type is unsupported"
        if canonical_source_type(url, source_type, expected_authority_bindings) != source_type:
            return "canonical Finding evidence source_type is not canonical"
        if _normalized_text(claim) != _normalized_text(conclusion):
            return "canonical Finding evidence claim does not match its conclusion"
        if stance not in {"supporting", "contradicting"}:
            return "canonical Finding evidence stance is unsupported"
        if independence not in {"independent", "same_origin"}:
            return "canonical Finding evidence independence is unsupported"
        if directness not in {"direct", "indirect"}:
            return "canonical Finding evidence directness is unsupported"
        if independence == "independent":
            domain = registrable_domain(url)
            if domain and domain in independent_domains:
                return "canonical Finding independent evidence shares a source domain"
            if domain:
                independent_domains.add(domain)
        signature = (claim, url)
        if signature in seen_evidence:
            return "canonical Finding evidence must not contain duplicates"
        seen_evidence.add(signature)
        if url not in evidence_urls:
            evidence_urls.append(url)
    if status == "sourced" and not evidence_urls:
        return "a sourced canonical Finding requires verified evidence"
    if method == "unverifiable_flag" and evidence:
        return "unverifiable_flag Finding must not contain evidence"
    coverage_gap = verification_coverage_gap(evidence, method)
    if status == "sourced" and coverage_gap:
        return "a sourced canonical Finding does not satisfy its verification_method"
    if (
        status == "blocked"
        and coverage_gap
        and coverage_gap not in cast(Sequence[Any], limitations)
    ):
        return "a blocked canonical Finding must retain its exact verification gap"
    citations = value.get("citations")
    if not isinstance(citations, tuple | list) or any(
        not isinstance(item, str) or not _is_http_url(item) for item in citations
    ):
        return "canonical Finding citations must be an http(s) URL array"
    if list(citations) != evidence_urls:
        return "canonical Finding citations must exactly equal its evidence URLs"

    operation_summary = value.get("operation_summary")
    if not isinstance(operation_summary, Mapping):
        return "canonical Finding requires trusted operation_summary"
    provenance = value.get("provenance")
    search_count = (
        len(cast(Sequence[Any], provenance.get("searches", ())))
        if isinstance(provenance, Mapping)
        and isinstance(provenance.get("searches", ()), tuple | list)
        else 0
    )
    expected_summary = {
        "task_id": task_id,
        "verification_id": value.get("verification_id") or None,
        "status": status,
        "verification_method": method,
        "evidence_count": len(evidence),
        "citation_count": len(citations),
        "limitation_count": len(cast(Sequence[Any], limitations)),
        "search_count": search_count,
    }
    if dict(operation_summary) != expected_summary:
        return "canonical Finding operation_summary does not match its content"
    provenance_reason = _provenance_rejection_reason(
        provenance,
        task_id=task_id,
        method=method,
        status=status,
        verification_id=str(value.get("verification_id") or ""),
        conclusion=conclusion,
        evidence=cast(Sequence[Mapping[str, Any]], evidence),
        evidence_urls=evidence_urls,
        expected_authority_bindings=expected_authority_bindings,
        expected_authority_fingerprint=expected_authority_fingerprint,
    )
    if provenance_reason is not None:
        return provenance_reason
    if (
        trusted_research_context is not None
        and trusted_research_context.get("attestation_valid") is not True
    ):
        return "canonical Finding received malformed trusted submission context"
    if trusted_research_context is not None and method == "unverifiable_flag":
        verification_outputs = trusted_research_context.get("verification_outputs")
        search_current_time = trusted_research_context.get("search_current_time")
        research_operation_names = trusted_research_context.get("research_operation_names")
        if verification_outputs or search_current_time or research_operation_names:
            return "unverifiable_flag Finding has unexpected trusted research operations"
    elif trusted_research_context is not None:
        attestation_reason = _verification_attestation_rejection_reason(
            trusted_research_context,
            task_id=task_id,
            conclusion=conclusion,
            verification_id=str(value.get("verification_id") or ""),
            evidence=cast(Sequence[Mapping[str, Any]], evidence),
            provenance=cast(Mapping[str, Any], provenance),
            authority_fingerprint=expected_authority_fingerprint,
        )
        if attestation_reason is not None:
            return attestation_reason
    expected_confidence = (
        confidence_policy.score_finding(
            evidence,
            method,
            today=_verification_reference_date(
                trusted_research_context,
                cast(Mapping[str, Any], provenance),
            ),
        )["overall"]
        if status == "sourced"
        else "low"
    )
    if confidence_level != expected_confidence:
        return "canonical Finding confidence does not match trusted evidence scoring"
    return None


def _provenance_rejection_reason(
    raw: Any,
    *,
    task_id: str,
    method: str,
    status: str,
    verification_id: str,
    conclusion: str,
    evidence: Sequence[Mapping[str, Any]],
    evidence_urls: Sequence[str],
    expected_authority_bindings: Sequence[Mapping[str, Any]],
    expected_authority_fingerprint: str,
) -> str | None:
    if not isinstance(raw, Mapping):
        return "canonical Finding requires complete provenance"
    required = {
        "verification_id",
        "search_ids",
        "evaluated_urls",
        "evaluations",
        "searches",
        "authority_binding_fingerprint",
    }
    if set(raw) != required:
        return "canonical Finding provenance fields are incomplete"
    search_ids = raw.get("search_ids")
    evaluated_urls = raw.get("evaluated_urls")
    evaluations = raw.get("evaluations")
    searches = raw.get("searches")
    authority_fingerprint = raw.get("authority_binding_fingerprint")
    if not _is_authority_fingerprint(authority_fingerprint):
        return "canonical Finding provenance requires an authority binding fingerprint"
    if expected_authority_fingerprint and authority_fingerprint != expected_authority_fingerprint:
        return "canonical Finding authority binding fingerprint is stale or forged"
    if not _string_array(search_ids) or not _string_array(evaluated_urls):
        return "canonical Finding provenance IDs and URLs must be arrays"
    if not isinstance(evaluations, tuple | list):
        return "canonical Finding provenance evaluations must be an array"
    if not isinstance(searches, tuple | list):
        return "canonical Finding provenance searches must be an array"
    search_id_values = list(cast(Sequence[str], search_ids))
    evaluated_url_values = list(cast(Sequence[str], evaluated_urls))
    if len(search_id_values) != len(set(search_id_values)):
        return "canonical Finding provenance search_ids must be unique"
    if len(evaluated_url_values) != len(set(evaluated_url_values)) or any(
        not _is_http_url(url) for url in evaluated_url_values
    ):
        return "canonical Finding provenance evaluated_urls must be unique http(s) URLs"

    if method == "unverifiable_flag":
        if status != "blocked":
            return "unverifiable_flag Finding must be blocked"
        if (
            verification_id
            or raw.get("verification_id")
            or search_ids
            or evaluated_urls
            or evaluations
            or searches
        ):
            return "unverifiable_flag Finding requires explicit empty provenance"
        return None
    if not verification_id or raw.get("verification_id") != verification_id:
        return "canonical Finding provenance verification_id does not match"
    if not search_id_values:
        return "researched canonical Finding requires at least one search provenance record"

    normalized_evaluations: list[dict[str, Any]] = []
    evaluation_urls: list[str] = []
    for raw_evaluation in evaluations:
        if not isinstance(raw_evaluation, Mapping):
            return "canonical Finding provenance evaluations must be mappings"
        required = {
            "claim",
            "source_url",
            "source_type",
            "stance",
            "independence",
            "directness",
        }
        allowed = required | {"as_of", "excerpt"}
        if not required <= set(raw_evaluation) or not set(raw_evaluation) <= allowed:
            return "canonical Finding provenance evaluation fields are incomplete"
        try:
            claim = _string(raw_evaluation, "claim")
            url = _string(raw_evaluation, "source_url")
            source_type = _string(raw_evaluation, "source_type")
            stance = _string(raw_evaluation, "stance")
            independence = _string(raw_evaluation, "independence")
            directness = _string(raw_evaluation, "directness")
            as_of = _string(raw_evaluation, "as_of") if "as_of" in raw_evaluation else ""
            excerpt = _string(raw_evaluation, "excerpt") if "excerpt" in raw_evaluation else ""
        except ValueError as exc:
            return str(exc)
        if not _is_http_url(url):
            return "canonical Finding provenance evaluation URL must be http(s)"
        if source_type not in _SOURCE_TYPES:
            return "canonical Finding provenance evaluation source_type is unsupported"
        if canonical_source_type(url, source_type, expected_authority_bindings) != source_type:
            return "canonical Finding provenance evaluation source_type is not canonical"
        if _normalized_text(claim) != _normalized_text(conclusion):
            return "canonical Finding provenance evaluation claim does not match conclusion"
        if stance not in {"supporting", "contradicting", "unrelated"}:
            return "canonical Finding provenance evaluation stance is unsupported"
        if independence not in {"independent", "same_origin"}:
            return "canonical Finding provenance evaluation independence is unsupported"
        if directness not in {"direct", "indirect"}:
            return "canonical Finding provenance evaluation directness is unsupported"
        if url in evaluation_urls:
            return "canonical Finding provenance evaluations must have unique URLs"
        evaluation_urls.append(url)
        normalized_evaluations.append(
            {
                "claim": claim,
                "source_url": url,
                "source_type": source_type,
                "stance": stance,
                "independence": independence,
                "directness": directness,
                **({"as_of": as_of} if as_of else {}),
                **({"excerpt": excerpt} if excerpt else {}),
            }
        )
    if evaluation_urls != evaluated_url_values:
        return "canonical Finding provenance evaluations must cover every evaluated URL"
    related_evaluations = [item for item in normalized_evaluations if item["stance"] != "unrelated"]
    if related_evaluations != [dict(item) for item in evidence]:
        return "canonical Finding evidence must exactly match its related evaluations"

    observed_ids: list[str] = []
    usable_urls: list[str] = []
    for raw_search in searches:
        if not isinstance(raw_search, Mapping):
            return "canonical Finding search provenance items must be mappings"
        if set(raw_search) != {
            "search_id",
            "structured_searches",
            "usable_urls",
            "current_time",
        }:
            return "canonical Finding search provenance fields are incomplete"
        try:
            search_id = _string(raw_search, "search_id")
        except ValueError as exc:
            return str(exc)
        observed_ids.append(search_id)
        structured = raw_search.get("structured_searches")
        if not isinstance(structured, tuple | list) or not structured:
            return "search provenance requires structured searches"
        for raw_intent in structured:
            if not isinstance(raw_intent, Mapping):
                return "structured search provenance items must be mappings"
            if not all(
                isinstance(raw_intent.get(field), str) and str(raw_intent[field]).strip()
                for field in ("query", "entity", "dimension")
            ):
                return "structured search provenance requires query, entity, and dimension"
            aliases = raw_intent.get("aliases", ())
            if not _nonempty_string_array(aliases, allow_empty=True):
                return "structured search aliases must be an explicit string array"
        urls = raw_search.get("usable_urls")
        if not _string_array(urls) or any(
            not _is_http_url(url) for url in cast(Sequence[str], urls)
        ):
            return "search provenance usable_urls must be an http(s) URL array"
        for url in cast(Sequence[str], urls):
            if url not in usable_urls:
                usable_urls.append(url)
        current_time = raw_search.get("current_time")
        if not isinstance(current_time, Mapping) or not all(
            isinstance(current_time.get(field), str) and str(current_time[field]).strip()
            for field in ("issued_at", "current_date", "timezone")
        ):
            return "each search provenance record requires current_time issuance context"
    if observed_ids != search_id_values:
        return "canonical Finding provenance search_ids do not match searches"
    if set(usable_urls) != set(evaluated_url_values):
        return "canonical Finding provenance must evaluate every usable URLs set"
    if any(url not in evaluated_url_values for url in evidence_urls):
        return "canonical Finding evidence includes an unevaluated URL"
    return None


def _candidate_dimensions(
    intent: Mapping[str, Any],
    criteria: tuple[Mapping[str, Any], ...],
) -> list[dict[str, Any]]:
    planning_context = intent.get("planning_context", {})
    if planning_context is None:
        planning_context = {}
    if not isinstance(planning_context, Mapping):
        raise ValueError("Intent planning_context must be a mapping")
    raw_dimensions = planning_context.get(
        "candidate_dimensions",
        intent.get("candidate_dimensions", ()),
    )
    if raw_dimensions and not isinstance(raw_dimensions, tuple | list):
        raise ValueError("Intent candidate_dimensions must be an array")
    subject = str(
        planning_context.get("subject") or intent.get("subject") or intent.get("goal") or ""
    ).strip()
    raw_items: Sequence[Any]
    if raw_dimensions:
        raw_items = cast(Sequence[Any], raw_dimensions)
    else:
        raw_items = [
            {
                "id": str(item.get("id") or f"dimension-{index + 1}"),
                "title": str(item.get("description") or item.get("id") or "Research"),
                "question": str(item.get("description") or item.get("id") or "Research"),
                "entities": [subject] if subject else [],
                "aliases": [],
                "dimension": str(item.get("description") or item.get("id") or "Research"),
                "depends_on": [],
                "verification_method": _criterion_method(item),
                "supports": [str(item.get("id") or "")],
                "required": bool(item.get("required", True)),
            }
            for index, item in enumerate(criteria)
        ]
    shared_bindings: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for raw in raw_items:
        if not isinstance(raw, Mapping) or "authority_bindings" not in raw:
            continue
        signature = _entity_signature(raw.get("entities", ()))
        bindings = normalize_authority_bindings(raw.get("authority_bindings", ()))
        if signature and bindings:
            shared_bindings.setdefault(signature, bindings)
    dimensions: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        item = _mapping(raw, "candidate dimension")
        dimension_id = str(item.get("id") or f"dimension-{index + 1}").strip()
        title = str(item.get("title") or item.get("dimension") or dimension_id).strip()
        question = str(item.get("question") or title).strip()
        dimension = str(item.get("dimension") or title).strip()
        method = str(item.get("verification_method") or "single_source_sufficient").strip()
        if not all((dimension_id, title, question, dimension)):
            raise ValueError("candidate dimension requires id, title, question, and dimension")
        if method not in _VERIFICATION_METHODS:
            raise ValueError(f"unsupported verification_method {method!r}")
        entities = item.get("entities", ())
        if not entities and subject:
            entities = [subject]
        binding_input = item.get("authority_bindings", ())
        if "authority_bindings" not in item:
            binding_input = shared_bindings.get(_entity_signature(entities), ())
        authority_bindings = normalize_authority_bindings(binding_input)
        dimensions.append(
            {
                **{str(key): _plain(value) for key, value in item.items()},
                "id": dimension_id,
                "title": title,
                "question": question,
                "entities": _entities(entities, item.get("aliases", ())),
                "dimension": dimension,
                "depends_on": list(_string_items(item.get("depends_on", ()), "depends_on")),
                "verification_method": method,
                "authority_bindings": authority_bindings,
            }
        )
    return dimensions


def _entity_signature(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        values: Sequence[Any] = [raw]
    elif isinstance(raw, tuple | list):
        values = raw
    else:
        return ()
    names = []
    for item in values:
        name = (
            str(item.get("name") or item.get("entity") or "")
            if isinstance(item, Mapping)
            else str(item or "")
        )
        normalized = _normalized_text(name)
        if normalized:
            names.append(normalized)
    return tuple(names)


def _task_goal(
    dimension: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "research-task-goal-v1",
        "id": dimension["id"],
        "title": dimension["title"],
        "question": dimension["question"],
        "entities": _plain(dimension["entities"]),
        "dimension": dimension["dimension"],
        "rationale": dimension.get("rationale", ""),
        "information_gap": dimension.get("information_gap", dimension["question"]),
        "coverage_ids": _plain(dimension.get("coverage_ids", ())),
        "verification_method": dimension["verification_method"],
        "authority_bindings": _plain(dimension["authority_bindings"]),
        "authority_binding_fingerprint": authority_binding_fingerprint(
            dimension["authority_bindings"]
        ),
        "constraints": _plain(intent.get("constraints", ())),
    }


def _confirmed_dimension(
    intent: Mapping[str, Any],
    task_id: str,
) -> Mapping[str, Any]:
    if intent.get("status") != "confirmed":
        raise ValueError("Research Context Builder and Verifier require a confirmed Intent")
    criteria = _criteria(intent)
    planning_context = _mapping(intent.get("planning_context"), "Intent planning_context")
    raw_dimensions = planning_context.get("candidate_dimensions")
    if not isinstance(raw_dimensions, tuple | list):
        raise ValueError("confirmed Intent candidate_dimensions must be an array")
    dimensions = _candidate_dimensions(intent, criteria)
    matches = [dimension for dimension in dimensions if dimension["id"] == task_id]
    if len(matches) != 1:
        raise ValueError(
            f"confirmed Intent must contain exactly one candidate dimension for task_id {task_id!r}"
        )
    return matches[0]


def _research_task_for_run(
    intent: Mapping[str, Any],
    task: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Resolve seed Tasks from Intent and rolling Tasks from their trusted goal."""

    task_id = _string(task, "task_id")
    try:
        return _task_goal(_confirmed_dimension(intent, task_id), intent)
    except ValueError as exc:
        goal = task.get("goal")
        if not isinstance(goal, str):
            raise exc
        try:
            decoded = json.loads(goal)
        except json.JSONDecodeError:
            raise exc from None
        if not isinstance(decoded, Mapping) or decoded.get("schema_version") != (
            "research-task-goal-v1"
        ):
            raise exc
        if _string(decoded, "id") != task_id:
            raise ValueError(
                "rolling research Task goal id does not match task_id"
            ) from exc
        return cast(Mapping[str, Any], decoded)


def _rolling_template_fingerprint(context: Mapping[str, Any]) -> str:
    boundaries = _mapping(context.get("authority_boundaries"), "authority_boundaries")
    return _string(
        _research_template(boundaries.get("child_templates")),
        "fingerprint",
    )


def _task_question_from_projection(task: Mapping[str, Any]) -> str:
    goal = str(task.get("goal") or "").strip()
    if goal.startswith("{"):
        try:
            decoded = json.loads(goal)
        except json.JSONDecodeError:
            return goal
        if isinstance(decoded, Mapping):
            return str(decoded.get("question") or decoded.get("title") or goal)
    return goal


def _claimed_coverage_ids(
    intent: Mapping[str, Any],
    tasks: Sequence[Any],
) -> set[str]:
    statuses = {
        _task_ref_id(cast(Mapping[str, Any], raw)): str(raw.get("status") or "")
        for raw in tasks
        if isinstance(raw, Mapping)
    }
    planning_context = intent.get("planning_context")
    dimensions = (
        planning_context.get("candidate_dimensions", ())
        if isinstance(planning_context, Mapping)
        else ()
    )
    claimed: set[str] = set()
    if isinstance(dimensions, tuple | list):
        for raw in dimensions:
            if not isinstance(raw, Mapping):
                continue
            task_id = str(raw.get("id") or "")
            if statuses.get(task_id) in {"failed", "cancelled"}:
                continue
            claimed.update(
                str(item).lower()
                for item in raw.get("coverage_ids") or ()
                if str(item).strip()
            )
    for raw in tasks:
        if not isinstance(raw, Mapping) or raw.get("status") in {"failed", "cancelled"}:
            continue
        claimed.update(_coverage_ids_from_text(str(raw.get("goal") or "")))
    return claimed


def _coverage_ids_from_text(value: str) -> set[str]:
    return {
        item.lower()
        for item in re.findall(r"\bcoverage-[a-z0-9_-]+\b", value, flags=re.IGNORECASE)
    }


def _research_questions_overlap(left: str, right: str) -> bool:
    left_terms = _research_question_terms(left)
    right_terms = _research_question_terms(right)
    if not left_terms or not right_terms:
        return False
    return len(left_terms & right_terms) / min(len(left_terms), len(right_terms)) >= 0.55


def _research_question_terms(value: str) -> set[str]:
    normalized = value.lower()
    for filler in (
        "进一步",
        "深入",
        "补充",
        "调研",
        "了解",
        "具体",
        "当前",
        "未来",
        "方向",
        "情况",
    ):
        normalized = normalized.replace(filler, "")
    terms = set(re.findall(r"[a-z0-9]+", normalized))
    compact_cjk = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    terms.update(compact_cjk[index : index + 2] for index in range(len(compact_cjk) - 1))
    return terms


def _discovered_task_id(question: str, known_ids: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40]
    base = f"followup-{slug}" if slug else "followup"
    suffix = compute_fingerprint(question)[:8]
    candidate = f"{base}-{suffix}"
    counter = 2
    while candidate in known_ids:
        candidate = f"{base}-{suffix}-{counter}"
        counter += 1
    return candidate


def _entities_from_question(question: str) -> list[dict[str, Any]]:
    return [{"name": question[:240], "aliases": []}]


def normalize_authority_bindings(value: Any) -> list[dict[str, Any]]:
    """Validate and canonicalize reviewed source-authority bindings."""

    if value is None:
        value = ()
    if not isinstance(value, tuple | list):
        raise ValueError("authority_bindings must be an array")
    if len(value) > _MAX_AUTHORITY_BINDINGS:
        raise ValueError("authority_bindings may contain at most eight entries")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, bool]] = set()
    for raw in value:
        binding = _mapping(raw, "authority binding")
        if not set(binding) <= {"host", "source_type", "include_subdomains"}:
            raise ValueError("authority binding contains unsupported fields")
        host = _normalize_authority_host(binding.get("host"))
        source_type = str(binding.get("source_type") or "").strip().lower()
        if source_type not in _AUTHORITY_SOURCE_TYPES:
            raise ValueError("authority binding source_type must be official or primary")
        include_subdomains = binding.get("include_subdomains", False)
        if not isinstance(include_subdomains, bool):
            raise ValueError("authority binding include_subdomains must be boolean")
        signature = (host, source_type, include_subdomains)
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(
            {
                "host": host,
                "source_type": source_type,
                "include_subdomains": include_subdomains,
            }
        )
    normalized.sort(
        key=lambda item: (
            str(item["host"]),
            str(item["source_type"]),
            bool(item["include_subdomains"]),
        )
    )
    return normalized


def authority_binding_fingerprint(value: Any) -> str:
    """Return the stable fingerprint carried through verification provenance."""

    return "sha256:" + compute_fingerprint(normalize_authority_bindings(value))


def canonical_source_type(
    source_url: str,
    proposed_type: str,
    authority_bindings: Sequence[Mapping[str, Any]],
) -> str:
    """Return a fail-closed source type under the confirmed authority policy."""

    host = _url_hostname(source_url)
    if any(_host_is_or_subdomain(host, capped) for capped in _SECONDARY_DOMAIN_CAPS):
        return "secondary"
    if proposed_type not in _AUTHORITY_SOURCE_TYPES:
        return proposed_type
    for binding in authority_bindings:
        binding_host = str(binding.get("host") or "")
        exact = host == binding_host
        subdomain = bool(binding.get("include_subdomains")) and host.endswith("." + binding_host)
        if (exact or subdomain) and proposed_type == binding.get("source_type"):
            return proposed_type
    if proposed_type == "official" and any(
        host == suffix.lstrip(".") or host.endswith(suffix) for suffix in _BUILTIN_OFFICIAL_SUFFIXES
    ):
        return proposed_type
    return "secondary"


def registrable_domain(source_url: str) -> str:
    """Return the conservative registrable domain used for source independence."""

    host = _url_hostname(source_url)
    labels = host.split(".") if host else []
    if len(labels) <= 2:
        return host
    suffix = ".".join(labels[-2:])
    if suffix in _MULTI_LABEL_PUBLIC_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def verification_method_satisfied(
    evidence: Sequence[Mapping[str, Any]],
    method: str,
) -> bool:
    """Evaluate a research verification method from canonical evidence only."""

    supporting = [item for item in evidence if item.get("stance") == "supporting"]
    independent_domains = {
        registrable_domain(str(item.get("source_url") or ""))
        for item in supporting
        if item.get("independence") == "independent"
    }
    independent_domains.discard("")
    authoritative = [
        item for item in supporting if item.get("source_type") in _AUTHORITY_SOURCE_TYPES
    ]
    return {
        "single_source_sufficient": bool(supporting),
        "dual_independent_required": len(independent_domains) >= 2,
        "official_primary_required": bool(authoritative),
        "contradiction_sensitive": len(independent_domains) >= 2,
    }.get(method, False)


def verification_coverage_gap(
    evidence: Sequence[Mapping[str, Any]],
    method: str,
) -> str | None:
    """Return the shared deterministic method gap used at both trust boundaries."""

    if verification_method_satisfied(evidence, method):
        return None
    return confidence_policy.coverage_gap_message(evidence, method)


def _url_hostname(value: str) -> str:
    hostname = urllib.parse.urlsplit(value).hostname or ""
    try:
        return hostname.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("source URL hostname is not valid IDNA") from exc


def _host_is_or_subdomain(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _normalize_authority_host(value: Any) -> str:
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw or "://" in raw or any(character in raw for character in "/@:*?#[]") or ".." in raw:
        raise ValueError("authority binding host must be a hostname without URL syntax")
    try:
        host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("authority binding host is not valid IDNA") from exc
    labels = host.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise ValueError("authority binding host is not a valid hostname")
    return host


def _is_authority_fingerprint(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else None


def _research_attestation_view(
    context: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if context is None:
        return None
    records = context.get("operation_attestations")
    if not isinstance(records, tuple | list):
        return {
            "attestation_valid": False,
            "verification_outputs": [],
            "search_current_time": {},
            "search_attestations": {},
            "research_operation_names": [],
        }
    verification_outputs: list[Mapping[str, Any]] = []
    time_by_token: dict[str, Mapping[str, Any]] = {}
    search_current_time: dict[str, Mapping[str, Any]] = {}
    search_attestations: dict[str, Mapping[str, Any]] = {}
    research_operation_names: list[str] = []
    shared_context = context.get("shared_research_context")
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            continue
        argument_scalars = raw_record.get("argument_scalars")
        result_scalars = raw_record.get("result_scalars")
        if not isinstance(argument_scalars, Mapping) or not isinstance(result_scalars, Mapping):
            continue
        tool_name = str(raw_record.get("tool_name") or "")
        if tool_name in {
            "get_current_time",
            "public_web_search",
            "verify_claim_evidence",
        }:
            research_operation_names.append(tool_name)
        if tool_name == "get_current_time":
            token = str(result_scalars.get("time_token") or "").strip()
            if token:
                time_by_token[token] = result_scalars
        elif tool_name == "verify_claim_evidence":
            verification_outputs.append(
                {
                    "verification_id": str(result_scalars.get("verification_id") or ""),
                    "result_fingerprint": str(raw_record.get("result_fingerprint") or ""),
                }
            )
        elif tool_name == "public_web_search":
            search_id = str(result_scalars.get("search_id") or "").strip()
            time_token = str(argument_scalars.get("time_token") or "").strip()
            argument_fingerprints = raw_record.get("argument_fingerprints")
            searches_fingerprint = (
                str(argument_fingerprints.get("searches") or "")
                if isinstance(argument_fingerprints, Mapping)
                else ""
            )
            if (
                not search_id
                and str(raw_record.get("outcome") or "") == "error"
                and str(raw_record.get("error_message") or "").strip()
                and searches_fingerprint
                and time_token
            ):
                search_id = _failed_search_attestation_id(
                    task_id=str(argument_scalars.get("task_id") or ""),
                    searches_fingerprint=searches_fingerprint,
                    time_token=time_token,
                )
            current_time = time_by_token.get(time_token)
            if search_id and current_time is not None:
                search_current_time[search_id] = current_time
            operation_summary = raw_record.get("operation_summary")
            usable_sources = (
                operation_summary.get("usable_sources")
                if isinstance(operation_summary, Mapping)
                else ()
            )
            usable_urls = [
                str(item.get("url") or "").strip()
                for item in usable_sources or ()
                if isinstance(item, Mapping) and str(item.get("url") or "").strip()
            ]
            if search_id:
                search_attestations[search_id] = {
                    "searches_fingerprint": (
                        str(argument_fingerprints.get("searches") or "")
                        if isinstance(argument_fingerprints, Mapping)
                        else ""
                    ),
                    "usable_urls": usable_urls,
                    "current_time": current_time or {},
                }
    shared_sources, shared_time = _shared_attestation_sources(shared_context)
    if shared_sources:
        shared_id = "shared:" + compute_fingerprint(sorted(shared_sources))[:24]
        search_current_time[shared_id] = shared_time
        search_attestations[shared_id] = {
            "searches_fingerprint": compute_fingerprint(
                [
                    {
                        "query": "reuse shared research context",
                        "entity": "shared-context",
                        "aliases": [],
                        "dimension": "shared context reuse",
                    }
                ]
            ),
            "usable_urls": list(shared_sources),
            "current_time": shared_time,
        }
    return {
        "attestation_valid": True,
        "verification_outputs": verification_outputs,
        "search_current_time": search_current_time,
        "search_attestations": search_attestations,
        "research_operation_names": research_operation_names,
    }


def _failed_search_attestation_id(
    *,
    task_id: str,
    searches_fingerprint: str,
    time_token: str,
) -> str:
    return "failed:" + compute_fingerprint(
        {
            "task_id": task_id,
            "searches_fingerprint": searches_fingerprint,
            "time_token": time_token,
        }
    )[:24]


def _shared_attestation_sources(
    value: Any,
) -> tuple[dict[str, Mapping[str, Any]], Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return {}, {}
    sources: dict[str, Mapping[str, Any]] = {}
    for raw in value.get("exploration_sources") or ():
        if not isinstance(raw, Mapping):
            continue
        url = str(raw.get("url") or "").strip()
        if _is_http_url(url):
            sources[url] = raw
    for raw in value.get("committed_results") or ():
        result = raw.get("result") if isinstance(raw, Mapping) else None
        if not isinstance(result, Mapping):
            continue
        for evidence in result.get("evidence") or ():
            if not isinstance(evidence, Mapping):
                continue
            url = str(evidence.get("source_url") or "").strip()
            if _is_http_url(url):
                sources[url] = evidence
    raw_time = value.get("exploration_time")
    current_time = {
        key: str(raw_time.get(key) or "")
        for key in ("issued_at", "current_date", "timezone")
    } if isinstance(raw_time, Mapping) else {}
    if sources and not all(current_time.values()):
        return {}, {}
    return sources, current_time


def _verification_attestation_rejection_reason(
    context: Mapping[str, Any],
    *,
    task_id: str,
    conclusion: str,
    verification_id: str,
    evidence: Sequence[Mapping[str, Any]],
    provenance: Mapping[str, Any],
    authority_fingerprint: str,
) -> str | None:
    raw_outputs = context.get("verification_outputs")
    if not isinstance(raw_outputs, tuple | list):
        return "canonical Finding requires a trusted verification attestation"
    actual = next(
        (
            item
            for item in raw_outputs
            if isinstance(item, Mapping)
            and str(item.get("verification_id") or "") == verification_id
        ),
        None,
    )
    search_grounded = verification_id.startswith("search:")
    if not isinstance(actual, Mapping) and not search_grounded:
        return "canonical Finding verification_id has no trusted verification output"
    search_ids = list(cast(Sequence[Any], provenance.get("search_ids") or ()))
    evaluated_urls = list(cast(Sequence[Any], provenance.get("evaluated_urls") or ()))
    evaluations = [
        dict(item)
        for item in cast(Sequence[Any], provenance.get("evaluations") or ())
        if isinstance(item, Mapping)
    ]
    evidence_values = [dict(item) for item in evidence]
    if not search_grounded:
        assert isinstance(actual, Mapping)
        expected = {
            "verification_id": verification_id,
            "task_id": task_id,
            "claim": conclusion,
            "search_ids": search_ids,
            "evaluated_urls": evaluated_urls,
            "evaluations": evaluations,
            "evidence": evidence_values,
            "authority_binding_fingerprint": authority_fingerprint,
            "operation_summary": {
                "verification_id": verification_id,
                "task_id": task_id,
                "search_ids": search_ids,
                "evaluated_url_count": len(evaluated_urls),
                "evidence_count": len(evidence_values),
                "authority_binding_fingerprint": authority_fingerprint,
            },
        }
        if str(actual.get("result_fingerprint") or "") != compute_fingerprint(expected):
            return "canonical Finding does not match the trusted verification output"
    raw_search_attestations = context.get("search_attestations")
    if not isinstance(raw_search_attestations, Mapping):
        return "canonical Finding requires trusted search attestations"
    searches = provenance.get("searches")
    if not isinstance(searches, tuple | list):
        return "canonical Finding provenance searches must be an array"
    if not set(str(item) for item in search_ids) <= set(raw_search_attestations):
        return "canonical Finding search attestations do not match verification search_ids"
    for raw_search in searches:
        if not isinstance(raw_search, Mapping):
            return "canonical Finding search provenance items must be mappings"
        search_id = str(raw_search.get("search_id") or "")
        attestation = raw_search_attestations.get(search_id)
        if not isinstance(attestation, Mapping):
            return "canonical Finding search provenance lacks a trusted attestation"
        if str(attestation.get("searches_fingerprint") or "") != compute_fingerprint(
            raw_search.get("structured_searches")
        ):
            return "canonical Finding structured search provenance is forged"
        if list(cast(Sequence[Any], attestation.get("usable_urls") or ())) != list(
            cast(Sequence[Any], raw_search.get("usable_urls") or ())
        ):
            return "canonical Finding usable URL provenance is forged"
        current_time = raw_search.get("current_time")
        trusted_time = attestation.get("current_time")
        expected_time = (
            {
                key: str(trusted_time.get(key) or "")
                for key in ("issued_at", "current_date", "timezone")
            }
            if isinstance(trusted_time, Mapping)
            else {}
        )
        if not isinstance(current_time, Mapping) or dict(current_time) != expected_time:
            return "canonical Finding current-time provenance is forged"
    return None


def _verification_reference_date(
    context: Mapping[str, Any] | None,
    provenance: Mapping[str, Any],
) -> date | None:
    current_times: Mapping[str, Any] = {}
    if context is not None and isinstance(context.get("search_current_time"), Mapping):
        current_times = cast(Mapping[str, Any], context["search_current_time"])
    search_ids = [str(item) for item in provenance.get("search_ids") or ()]
    for search_id in reversed(search_ids):
        current = current_times.get(search_id)
        if isinstance(current, Mapping):
            value = str(current.get("current_date") or "").strip()
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    searches = provenance.get("searches")
    if isinstance(searches, tuple | list):
        for raw_search in reversed(searches):
            if not isinstance(raw_search, Mapping):
                continue
            current = raw_search.get("current_time")
            if not isinstance(current, Mapping):
                continue
            value = str(current.get("current_date") or "").strip()
            try:
                return date.fromisoformat(value)
            except ValueError:
                continue
    return None


def _dimension_supports(
    dimension: Mapping[str, Any],
    criteria: tuple[Mapping[str, Any], ...],
    *,
    dimension_index: int,
    dimension_count: int,
) -> tuple[str, ...]:
    criterion_ids = [str(item["id"]) for item in criteria]
    criterion_id = dimension.get("criterion_id")
    explicit: tuple[str, ...]
    if criterion_id is not None:
        explicit = (str(criterion_id).strip(),)
    else:
        raw = dimension.get("supports", dimension.get("criteria", ()))
        explicit = _string_items(raw, "candidate dimension supports") if raw else ()
    if explicit:
        unknown = sorted(set(explicit) - set(criterion_ids))
        if unknown:
            raise ValueError("candidate dimension supports unknown criteria: " + ", ".join(unknown))
        return explicit
    dimension_id = str(dimension["id"])
    if dimension_id in criterion_ids:
        return (dimension_id,)
    if len(criteria) == dimension_count:
        return (criterion_ids[dimension_index],)
    return tuple(criterion_ids)


def _criteria(intent: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = intent.get("success_criteria", intent.get("criteria", ()))
    if not isinstance(raw, tuple | list) or not raw:
        raise ValueError("confirmed research Intent requires criteria")
    criteria = tuple(_mapping(item, "Intent criterion") for item in raw)
    if any(not str(item.get("id") or "").strip() for item in criteria):
        raise ValueError("Intent criteria require ids")
    return criteria


def _criterion_method(criterion: Mapping[str, Any]) -> str:
    value = str(criterion.get("verification_method") or "").strip()
    if value in _VERIFICATION_METHODS:
        return value
    return "single_source_sufficient"


def _entities(raw: Any, aliases: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        values: Sequence[Any] = [raw]
    elif isinstance(raw, tuple | list):
        values = raw
    else:
        raise ValueError("candidate dimension entities must be a string or array")
    alias_mapping = aliases if isinstance(aliases, Mapping) else {}
    shared_aliases = aliases if isinstance(aliases, tuple | list) else ()
    result: list[dict[str, Any]] = []
    for raw_entity in values:
        if isinstance(raw_entity, Mapping):
            name = str(raw_entity.get("name") or raw_entity.get("entity") or "").strip()
            entity_aliases = raw_entity.get("aliases", ())
        else:
            name = str(raw_entity or "").strip()
            entity_aliases = alias_mapping.get(name, shared_aliases)
        if not name:
            raise ValueError("candidate dimension entity name cannot be blank")
        result.append(
            {
                "name": name,
                "aliases": list(_string_items(entity_aliases, "entity aliases")),
            }
        )
    return result


def _research_template(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, tuple | list):
        raise ValueError("allowed_child_templates must be an array")
    matches = [
        item
        for item in raw
        if isinstance(item, Mapping) and item.get("id") == RESEARCH_CHILD_TEMPLATE_ID
    ]
    if len(matches) != 1:
        raise ValueError("Research Planner requires one pinned research-dimension template")
    return cast(Mapping[str, Any], matches[0])


def _priority(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
        raise ValueError("candidate dimension priority must be an integer from 0 to 100")
    return value


def _valid_task_graph_result(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("goal_verified") is True
        and isinstance(value.get("committed_results"), tuple | list)
    )


def _schema_definitions() -> tuple[SchemaDefinition, ...]:
    object_schema: dict[str, Any] = {"type": "object"}
    outcome_schema = {
        "type": "object",
        "required": ["outcome"],
        "properties": {"outcome": {"type": "string"}},
    }
    definitions = [
        SchemaDefinition(RESEARCH_INTENT_SCHEMA_ID, "1", _research_intent_schema()),
        SchemaDefinition(RESEARCH_FINDING_SCHEMA_ID, "1", _research_finding_schema()),
        SchemaDefinition(
            RESEARCH_TASK_GRAPH_RESULT_SCHEMA_ID,
            "1",
            {
                "type": "object",
                "required": ["goal_verified", "committed_results"],
                "properties": {
                    "goal_verified": {"const": True},
                    "committed_results": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
            },
        ),
    ]
    for component_id in (
        RESEARCH_PLANNER_ID,
        RESEARCH_GRAPH_POLICY_ID,
        RESEARCH_CONTEXT_BUILDER_ID,
        RESEARCH_TASK_VERIFIER_ID,
        RESEARCH_CRITERION_VERIFIER_ID,
        RESEARCH_GOAL_VERIFIER_ID,
    ):
        definitions.append(SchemaDefinition(f"{component_id}-input-v1", "1", object_schema))
        definitions.append(
            SchemaDefinition(
                f"{component_id}-output-v1",
                "1",
                object_schema
                if component_id in {RESEARCH_PLANNER_ID, RESEARCH_CONTEXT_BUILDER_ID}
                else outcome_schema,
            )
        )
    return tuple(definitions)


def _research_intent_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "intent_id",
            "version",
            "status",
            "goal",
            "desired_outcome",
            "success_criteria",
            "planning_context",
        ],
        "properties": {
            "intent_id": {"type": "string", "minLength": 1},
            "version": {"type": "integer", "minimum": 1},
            "status": {"enum": ["draft", "confirmed"]},
            "goal": {"type": "string", "minLength": 1},
            "desired_outcome": {"type": "string", "minLength": 1},
            "success_criteria": {"type": "array", "minItems": 1},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "non_goals": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "authority_hash": {"type": "string"},
            "planning_context": {
                "type": "object",
                "required": ["subject", "candidate_dimensions"],
                "properties": {
                    "subject": {"type": "string", "minLength": 1},
                    "research_question": {"type": "string"},
                    "research_brief": {"type": "object"},
                    "landscape_map": {"type": "object"},
                    "coverage_map": {"type": "object"},
                    "exploration_queries": {"type": "array"},
                    "candidate_dimensions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": [
                                "id",
                                "title",
                                "question",
                                "dimension",
                                "verification_method",
                                "authority_bindings",
                            ],
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "title": {"type": "string", "minLength": 1},
                                "question": {"type": "string", "minLength": 1},
                                "dimension": {"type": "string", "minLength": 1},
                                "rationale": {"type": "string"},
                                "information_gap": {"type": "string"},
                                "coverage_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "verification_method": {"enum": sorted(_VERIFICATION_METHODS)},
                                "authority_bindings": _authority_bindings_schema(),
                            },
                        },
                    },
                },
            },
        },
    }


def _research_finding_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": sorted(_FINDING_FIELDS),
        "additionalProperties": False,
        "properties": {
            "task_id": {"type": "string", "minLength": 1},
            "question": {"type": "string", "minLength": 1},
            "conclusion": {"type": "string", "minLength": 1},
            "implications": {"type": "string", "minLength": 1},
            "confidence": {"enum": ["low", "medium", "high"]},
            "verification_method": {"enum": sorted(_VERIFICATION_METHODS)},
            "verification_id": {"type": "string"},
            "status": {"enum": ["sourced", "blocked"]},
            "evidence": {"type": "array"},
            "citations": {"type": "array"},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "task_resolution": {"enum": ["completed", "blocked"]},
            "operation_summary": {"type": "object"},
            "provenance": {"type": "object"},
        },
    }


def _authority_bindings_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": _MAX_AUTHORITY_BINDINGS,
        "items": {
            "type": "object",
            "required": ["host", "source_type"],
            "additionalProperties": False,
            "properties": {
                "host": {"type": "string", "minLength": 1},
                "source_type": {"enum": sorted(_AUTHORITY_SOURCE_TYPES)},
                "include_subdomains": {"type": "boolean"},
            },
        },
    }


def _mapping(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{source} must be a mapping")
    return cast(Mapping[str, Any], value)


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _string_items(value: Any, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, tuple | list):
        raise ValueError(f"{source} must be an array")
    result = tuple(str(item or "").strip() for item in value)
    if any(not item for item in result):
        raise ValueError(f"{source} cannot contain blank values")
    return tuple(dict.fromkeys(result))


def _string_array(value: Any) -> bool:
    return isinstance(value, tuple | list) and all(isinstance(item, str) for item in value)


def _nonempty_string_array(value: Any, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, tuple | list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


__all__ = [
    "RESEARCH_CHILD_TEMPLATE_ID",
    "RESEARCH_CONTEXT_BUILDER_ID",
    "RESEARCH_CRITERION_VERIFIER_ID",
    "RESEARCH_FINDING_SCHEMA_ID",
    "RESEARCH_GOAL_VERIFIER_ID",
    "RESEARCH_GRAPH_POLICY_ID",
    "RESEARCH_INTENT_SCHEMA_ID",
    "RESEARCH_PLANNER_ID",
    "RESEARCH_TASK_GRAPH_RESULT_SCHEMA_ID",
    "RESEARCH_TASK_GRAPH_RESULT_VALIDATOR_ID",
    "RESEARCH_TASK_VERIFIER_ID",
    "build_research_completion_validators",
    "build_research_components",
    "build_research_schema_registry",
]
