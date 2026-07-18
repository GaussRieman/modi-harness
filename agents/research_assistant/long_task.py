"""Research-specific pinned components for the generic Task Graph runtime.

The generic runtime owns graph state, scheduling, durable invocations, and
parent/child fencing.  This module owns the application semantics: turning a
confirmed research Intent into dimension Tasks and deciding whether a child
returned a canonical, fully provenance-bound Finding.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import replace
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
            "implementation_revision": 1,
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
) -> GraphPatch:
    del idempotency_key
    intent = _mapping(inputs.get("intent"), "intent")
    graph = _mapping(inputs.get("graph"), "graph")
    if inputs.get("trigger") != "seed" or _integer(graph, "revision") != 0:
        raise ValueError("Research Planner only seeds a confirmed research Intent")
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
                "research candidate dimension has unknown dependency: "
                + ", ".join(unknown)
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
            goal=json.dumps(
                _task_goal(dimension, intent),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            supports=supports,
            depends_on=tuple(
                DependencyRef("task", dependency_id, 1)
                for dependency_id in dependency_ids
            ),
            priority=_priority(dimension.get("priority", 50)),
            required=bool(dimension.get("required", True)),
            kind="executable",
            completion_contract=CompletionContract(
                output_schema_id=RESEARCH_FINDING_SCHEMA_ID,
                validator_ids=(RESEARCH_TASK_VERIFIER_ID,),
                required_evidence=("verified-public-url-provenance",),
            ),
            executor_policy=ExecutorPolicy((binding,), binding),
        )
        tasks.append(task)

    required_criteria = {
        str(item["id"])
        for item in criteria
        if bool(item.get("required", True))
    }
    covered = {criterion_id for task in tasks if task.required for criterion_id in task.supports}
    missing = sorted(required_criteria - covered)
    if missing:
        tasks[0] = replace(
            tasks[0],
            supports=tuple(dict.fromkeys((*tasks[0].supports, *missing))),
            required=True,
        )

    return GraphPatch(
        base_revision=0,
        trigger="seed",
        reason="seed one isolated child Task per confirmed research dimension",
        operations=tuple(
            GraphPatchOperation("add_task", task=task) for task in tasks
        ),
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
    raw_goal = _string(task, "goal")
    try:
        research_task = json.loads(raw_goal)
    except json.JSONDecodeError:
        research_task = {
            "schema_version": "research-task-goal-v1",
            "id": _string(task, "task_id"),
            "question": raw_goal,
            "title": raw_goal,
            "entities": [],
            "dimension": raw_goal,
            "verification_method": "single_source_sufficient",
        }
    if not isinstance(research_task, Mapping):
        raise ValueError("research Task goal is not structured context")
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
    dependency_output_refs = [
        str(item).strip()
        for item in dependency_outputs
        if str(item).strip()
    ]
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
    task = inputs.get("task", {})
    expected_task_id = (
        str(task.get("task_id") or "").strip()
        if isinstance(task, Mapping)
        else ""
    )
    reason = _finding_rejection_reason(candidate, expected_task_id=expected_task_id)
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
            evidence_refs.extend(
                str(ref).strip() for ref in refs if str(ref).strip()
            )
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
            str(item).strip()
            for item in inputs.get("output_refs", ())
            if str(item).strip()
        )
    )
    if not missing and output_refs:
        return {"outcome": "passed", "evidence_refs": output_refs}
    gap = {
        "missing_criteria": missing,
        "missing_outputs": not bool(output_refs),
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
        _string(value, "question")
        _string(value, "conclusion")
        _string(value, "implications")
        status = _string(value, "status")
        method = _string(value, "verification_method")
        confidence = _string(value, "confidence")
    except ValueError as exc:
        return str(exc)
    if expected_task_id and task_id != expected_task_id:
        return "canonical Finding task_id does not match the exact Task"
    if status not in {"sourced", "blocked"}:
        return "canonical Finding status must be sourced or blocked"
    if method not in _VERIFICATION_METHODS:
        return "canonical Finding verification_method is unsupported"
    if confidence not in {"low", "medium", "high"}:
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
        allowed = required | {"as_of"}
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
        except ValueError as exc:
            return str(exc)
        if not _is_http_url(url):
            return "canonical Finding evidence source_url must be http(s)"
        if source_type not in _SOURCE_TYPES:
            return "canonical Finding evidence source_type is unsupported"
        if stance not in {"supporting", "contradicting"}:
            return "canonical Finding evidence stance is unsupported"
        if independence not in {"independent", "same_origin"}:
            return "canonical Finding evidence independence is unsupported"
        if directness not in {"direct", "indirect"}:
            return "canonical Finding evidence directness is unsupported"
        signature = (claim, url)
        if signature in seen_evidence:
            return "canonical Finding evidence must not contain duplicates"
        seen_evidence.add(signature)
        if url not in evidence_urls:
            evidence_urls.append(url)
    if status == "sourced" and not evidence_urls:
        return "a sourced canonical Finding requires verified evidence"
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
    return _provenance_rejection_reason(
        provenance,
        task_id=task_id,
        method=method,
        status=status,
        verification_id=str(value.get("verification_id") or ""),
        evidence_urls=evidence_urls,
    )


def _provenance_rejection_reason(
    raw: Any,
    *,
    task_id: str,
    method: str,
    status: str,
    verification_id: str,
    evidence_urls: Sequence[str],
) -> str | None:
    if not isinstance(raw, Mapping):
        return "canonical Finding requires complete provenance"
    required = {"verification_id", "search_ids", "evaluated_urls", "searches"}
    if set(raw) != required:
        return "canonical Finding provenance fields are incomplete"
    search_ids = raw.get("search_ids")
    evaluated_urls = raw.get("evaluated_urls")
    searches = raw.get("searches")
    if not _string_array(search_ids) or not _string_array(evaluated_urls):
        return "canonical Finding provenance IDs and URLs must be arrays"
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
        if verification_id or raw.get("verification_id") or search_ids or evaluated_urls or searches:
            return "unverifiable_flag Finding requires explicit empty provenance"
        return None
    if not verification_id or raw.get("verification_id") != verification_id:
        return "canonical Finding provenance verification_id does not match"
    if not search_id_values:
        return "researched canonical Finding requires at least one search provenance record"

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
                isinstance(raw_intent.get(field), str)
                and str(raw_intent[field]).strip()
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
            isinstance(current_time.get(field), str)
            and str(current_time[field]).strip()
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
        planning_context.get("subject")
        or intent.get("subject")
        or intent.get("goal")
        or ""
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
        dimensions.append(
            {
                **{str(key): _plain(value) for key, value in item.items()},
                "id": dimension_id,
                "title": title,
                "question": question,
                "entities": _entities(entities, item.get("aliases", ())),
                "dimension": dimension,
                "depends_on": list(
                    _string_items(item.get("depends_on", ()), "depends_on")
                ),
                "verification_method": method,
            }
        )
    return dimensions


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
        "verification_method": dimension["verification_method"],
        "constraints": _plain(intent.get("constraints", ())),
    }


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
        definitions.append(
            SchemaDefinition(f"{component_id}-input-v1", "1", object_schema)
        )
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
                    "candidate_dimensions": {"type": "array", "minItems": 1},
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
