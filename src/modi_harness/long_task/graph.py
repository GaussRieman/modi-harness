"""Pure Task Graph validation, readiness, and patch application."""

from __future__ import annotations

from dataclasses import replace

from .types import (
    DependencyRef,
    GraphPatch,
    GroupRun,
    TaskGraphRun,
    TaskRun,
)


class GraphValidationError(ValueError):
    """A Task Graph or incremental patch violates a runtime invariant."""


def validate_graph(graph: TaskGraphRun) -> None:
    task_map = _task_map(graph)
    group_map = _group_map(graph)
    active_tasks = _resolve_active_tasks(graph, task_map)
    active_groups = _resolve_active_groups(graph, group_map)

    if len(active_tasks) > graph.limits.max_tasks:
        raise GraphValidationError("active Task count exceeds max_tasks")
    if graph.replan_count > graph.limits.max_replans:
        raise GraphValidationError("replan count exceeds max_replans")
    for task in graph.tasks:
        _validate_task(task, graph, task_map, group_map)
    for group in graph.groups:
        _validate_group(group, graph, task_map, group_map)
    _validate_coverage(graph, active_tasks, active_groups)
    _validate_acyclic(graph, active_tasks, active_groups)


def ready_tasks(graph: TaskGraphRun) -> tuple[TaskRun, ...]:
    validate_graph(graph)
    task_map = _task_map(graph)
    group_map = _group_map(graph)
    ready = [
        task
        for task in _resolve_active_tasks(graph, task_map)
        if task.kind == "executable"
        and task.status == "pending"
        and task.active_attempt_id is None
        and task.intent_binding_state in {"current", "retained"}
        and all(_dependency_satisfied(ref, task_map, group_map) for ref in task.depends_on)
    ]
    return tuple(sorted(ready, key=lambda item: (-item.priority, not item.required, item.task_id)))


def apply_graph_patch(graph: TaskGraphRun, patch: GraphPatch) -> TaskGraphRun:
    if patch.base_revision != graph.revision:
        raise GraphValidationError(
            f"stale graph revision {patch.base_revision}; current is {graph.revision}"
        )
    tasks = list(graph.tasks)
    groups = list(graph.groups)
    active_tasks = list(graph.active_task_refs)
    active_groups = list(graph.active_group_refs)

    for operation in patch.operations:
        if operation.op == "add_task":
            task = _required_task(operation.task, "add_task")
            _reject_existing_task(tasks, task.ref)
            tasks.append(task)
            active_tasks.append(task.ref)
        elif operation.op == "add_group":
            group = _required_group(operation.group, "add_group")
            _reject_existing_group(groups, group.ref)
            groups.append(group)
            active_groups.append(group.ref)
        elif operation.op in {"replace_dependencies", "set_priority"}:
            task = _active_task_by_id(tasks, active_tasks, operation.task_id)
            _expected_revision(operation.expected_revision, task.task_revision)
            if task.status != "pending":
                raise GraphValidationError(f"cannot modify non-pending Task {task.task_id!r}")
            replacement = replace(
                task,
                task_revision=task.task_revision + 1,
                depends_on=(
                    operation.dependencies
                    if operation.op == "replace_dependencies"
                    else task.depends_on
                ),
                priority=(
                    _required_priority(operation.priority)
                    if operation.op == "set_priority"
                    else task.priority
                ),
            )
            tasks.append(replacement)
            active_tasks = _replace_ref(active_tasks, task.ref, replacement.ref)
        elif operation.op == "cancel_pending_task":
            task = _active_task_by_id(tasks, active_tasks, operation.task_id)
            _expected_revision(operation.expected_revision, task.task_revision)
            if task.status != "pending":
                raise GraphValidationError(f"cannot cancel non-pending Task {task.task_id!r}")
            replacement = replace(task, status="cancelled")
            tasks = [replacement if item.ref == task.ref else item for item in tasks]
            active_tasks = [ref for ref in active_tasks if ref != task.ref]
        elif operation.op == "expand_task":
            task = _active_task_by_id(tasks, active_tasks, operation.task_id)
            _expected_revision(operation.expected_revision, task.task_revision)
            if task.status != "pending" or task.kind != "expandable":
                raise GraphValidationError(f"Task {task.task_id!r} is not expandable")
            group = _required_group(operation.group, "expand_task")
            if group.depends_on != task.depends_on:
                raise GraphValidationError("expanded Group must inherit Task dependencies")
            if group.supports != task.supports or group.required != task.required:
                raise GraphValidationError("expanded Group must inherit Task criterion coverage")
            if group.intent_binding_hash != task.intent_binding_hash:
                raise GraphValidationError("expanded Group must inherit Task Intent binding")
            child_tasks = operation.child_tasks
            child_refs = {child.task_ref for child in group.children}
            if child_refs != {child.ref for child in child_tasks}:
                raise GraphValidationError("expanded Group children must match child Tasks exactly")
            cancelled = replace(task, status="cancelled")
            tasks = [cancelled if item.ref == task.ref else item for item in tasks]
            active_tasks = [ref for ref in active_tasks if ref != task.ref]
            _reject_existing_group(groups, group.ref)
            groups.append(group)
            active_groups.append(group.ref)
            for child in child_tasks:
                _reject_existing_task(tasks, child.ref)
                tasks.append(child)
                active_tasks.append(child.ref)
            tasks, active_tasks = _rewrite_pending_dependents(
                tasks, active_tasks, task.ref, group.ref
            )
        else:
            raise GraphValidationError(f"unsupported GraphPatch operation {operation.op!r}")

    replan_count = graph.replan_count + (0 if graph.revision == 0 else 1)
    updated = replace(
        graph,
        revision=graph.revision + 1,
        status="active" if graph.status == "planning" else graph.status,
        tasks=tuple(tasks),
        groups=tuple(groups),
        active_task_refs=tuple(active_tasks),
        active_group_refs=tuple(active_groups),
        replan_count=replan_count,
    )
    validate_graph(updated)
    if graph.revision == 0 and not ready_tasks(updated):
        raise GraphValidationError("initial seed must contain at least one ready executable Task")
    return updated


def _validate_task(
    task: TaskRun,
    graph: TaskGraphRun,
    task_map: dict[tuple[str, int], TaskRun],
    group_map: dict[tuple[str, int], GroupRun],
) -> None:
    if task.graph_id != graph.graph_id:
        raise GraphValidationError(f"Task {task.task_id!r} belongs to another graph")
    if task.required and not task.supports:
        raise GraphValidationError(f"required Task {task.task_id!r} supports no criterion")
    if not 0 <= task.priority <= 100:
        raise GraphValidationError(f"Task {task.task_id!r} priority must be 0..100")
    if task.executor_policy.preferred_binding not in task.executor_policy.allowed_bindings:
        raise GraphValidationError(f"Task {task.task_id!r} preferred executor is not allowed")
    for ref in task.depends_on:
        _resolve_ref(ref, task_map, group_map)


def _validate_group(
    group: GroupRun,
    graph: TaskGraphRun,
    task_map: dict[tuple[str, int], TaskRun],
    group_map: dict[tuple[str, int], GroupRun],
) -> None:
    if group.graph_id != graph.graph_id:
        raise GraphValidationError(f"Group {group.group_id!r} belongs to another graph")
    if group.required and not group.supports:
        raise GraphValidationError(f"required Group {group.group_id!r} supports no criterion")
    if not group.children:
        raise GraphValidationError(f"Group {group.group_id!r} has no children")
    for ref in group.depends_on:
        _resolve_ref(ref, task_map, group_map)
    for child in group.children:
        if child.task_ref.kind != "task":
            raise GraphValidationError("Group children must reference Tasks")
        _resolve_ref(child.task_ref, task_map, group_map)


def _validate_coverage(
    graph: TaskGraphRun,
    tasks: tuple[TaskRun, ...],
    groups: tuple[GroupRun, ...],
) -> None:
    covered = {
        criterion
        for item in (*tasks, *groups)
        if item.required and item.intent_binding_state in {"current", "retained"}
        for criterion in item.supports
    }
    missing = sorted(set(graph.required_criteria) - covered)
    if missing:
        raise GraphValidationError(f"required criterion coverage missing: {', '.join(missing)}")


def _validate_acyclic(
    graph: TaskGraphRun,
    tasks: tuple[TaskRun, ...],
    groups: tuple[GroupRun, ...],
) -> None:
    active_keys = {item.ref.key for item in (*tasks, *groups)}
    edges: dict[tuple[str, str, int], tuple[tuple[str, str, int], ...]] = {}
    for task in tasks:
        edges[task.ref.key] = tuple(ref.key for ref in task.depends_on if ref.key in active_keys)
    for group in groups:
        dependencies = [ref.key for ref in group.depends_on if ref.key in active_keys]
        dependencies.extend(
            child.task_ref.key for child in group.children if child.task_ref.key in active_keys
        )
        edges[group.ref.key] = tuple(dependencies)

    visiting: set[tuple[str, str, int]] = set()
    visited: set[tuple[str, str, int]] = set()

    def visit(key: tuple[str, str, int]) -> int:
        if key in visiting:
            raise GraphValidationError("Task Graph contains a cycle")
        if key in visited:
            return 0
        visiting.add(key)
        depth = 1 + max((visit(dep) for dep in edges.get(key, ())), default=0)
        visiting.remove(key)
        visited.add(key)
        if depth > graph.limits.max_graph_depth:
            raise GraphValidationError("Task Graph exceeds max_graph_depth")
        return depth

    for key in edges:
        visit(key)


def _dependency_satisfied(
    ref: DependencyRef,
    task_map: dict[tuple[str, int], TaskRun],
    group_map: dict[tuple[str, int], GroupRun],
) -> bool:
    item = _resolve_ref(ref, task_map, group_map)
    return item.status == "completed"


def _resolve_ref(
    ref: DependencyRef,
    task_map: dict[tuple[str, int], TaskRun],
    group_map: dict[tuple[str, int], GroupRun],
) -> TaskRun | GroupRun:
    mapping = task_map if ref.kind == "task" else group_map
    try:
        return mapping[(ref.id, ref.revision)]
    except KeyError as exc:
        raise GraphValidationError(f"dependency references unknown {ref.kind} {ref.id!r}") from exc


def _task_map(graph: TaskGraphRun) -> dict[tuple[str, int], TaskRun]:
    result: dict[tuple[str, int], TaskRun] = {}
    for task in graph.tasks:
        key = (task.task_id, task.task_revision)
        if key in result:
            raise GraphValidationError(f"duplicate Task revision {task.task_id!r}:{task.task_revision}")
        result[key] = task
    return result


def _group_map(graph: TaskGraphRun) -> dict[tuple[str, int], GroupRun]:
    result: dict[tuple[str, int], GroupRun] = {}
    for group in graph.groups:
        key = (group.group_id, group.group_revision)
        if key in result:
            raise GraphValidationError(
                f"duplicate Group revision {group.group_id!r}:{group.group_revision}"
            )
        result[key] = group
    return result


def _resolve_active_tasks(
    graph: TaskGraphRun, task_map: dict[tuple[str, int], TaskRun]
) -> tuple[TaskRun, ...]:
    result: list[TaskRun] = []
    seen: set[tuple[str, int]] = set()
    for ref in graph.active_task_refs:
        if ref.kind != "task" or (ref.id, ref.revision) not in task_map:
            raise GraphValidationError(f"active Task ref {ref.id!r} is invalid")
        key = (ref.id, ref.revision)
        if key in seen:
            raise GraphValidationError(f"active Task ref {ref.id!r} is duplicated")
        seen.add(key)
        result.append(task_map[key])
    return tuple(result)


def _resolve_active_groups(
    graph: TaskGraphRun, group_map: dict[tuple[str, int], GroupRun]
) -> tuple[GroupRun, ...]:
    result: list[GroupRun] = []
    seen: set[tuple[str, int]] = set()
    for ref in graph.active_group_refs:
        if ref.kind != "group" or (ref.id, ref.revision) not in group_map:
            raise GraphValidationError(f"active Group ref {ref.id!r} is invalid")
        key = (ref.id, ref.revision)
        if key in seen:
            raise GraphValidationError(f"active Group ref {ref.id!r} is duplicated")
        seen.add(key)
        result.append(group_map[key])
    return tuple(result)


def _required_task(task: TaskRun | None, operation: str) -> TaskRun:
    if task is None:
        raise GraphValidationError(f"{operation} requires task")
    return task


def _required_group(group: GroupRun | None, operation: str) -> GroupRun:
    if group is None:
        raise GraphValidationError(f"{operation} requires group")
    return group


def _required_priority(priority: int | None) -> int:
    if priority is None:
        raise GraphValidationError("set_priority requires priority")
    return priority


def _expected_revision(expected: int | None, current: int) -> None:
    if expected != current:
        raise GraphValidationError(f"stale target revision {expected}; current is {current}")


def _reject_existing_task(tasks: list[TaskRun], ref: DependencyRef) -> None:
    if any(item.ref == ref for item in tasks):
        raise GraphValidationError(f"duplicate Task revision {ref.id!r}:{ref.revision}")


def _reject_existing_group(groups: list[GroupRun], ref: DependencyRef) -> None:
    if any(item.ref == ref for item in groups):
        raise GraphValidationError(f"duplicate Group revision {ref.id!r}:{ref.revision}")


def _active_task_by_id(
    tasks: list[TaskRun], active_refs: list[DependencyRef], task_id: str | None
) -> TaskRun:
    if not task_id:
        raise GraphValidationError("Task operation requires task_id")
    active = {ref for ref in active_refs if ref.kind == "task" and ref.id == task_id}
    if len(active) != 1:
        raise GraphValidationError(f"Task {task_id!r} is not uniquely active")
    ref = next(iter(active))
    return next(item for item in tasks if item.ref == ref)


def _replace_ref(
    refs: list[DependencyRef], old: DependencyRef, new: DependencyRef
) -> list[DependencyRef]:
    return [new if ref == old else ref for ref in refs]


def _rewrite_pending_dependents(
    tasks: list[TaskRun],
    active_refs: list[DependencyRef],
    old: DependencyRef,
    new: DependencyRef,
) -> tuple[list[TaskRun], list[DependencyRef]]:
    active = set(active_refs)
    appended: list[TaskRun] = []
    replacements: dict[DependencyRef, DependencyRef] = {}
    for task in tasks:
        if task.ref not in active or task.status != "pending" or old not in task.depends_on:
            continue
        replacement = replace(
            task,
            task_revision=task.task_revision + 1,
            depends_on=tuple(new if ref == old else ref for ref in task.depends_on),
        )
        appended.append(replacement)
        replacements[task.ref] = replacement.ref
    return [*tasks, *appended], [replacements.get(ref, ref) for ref in active_refs]


__all__ = ["GraphValidationError", "apply_graph_patch", "ready_tasks", "validate_graph"]
