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


def validate_graph(
    graph: TaskGraphRun,
    *,
    allow_incomplete_coverage: bool = False,
) -> None:
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
    if not allow_incomplete_coverage:
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
    if not patch.trigger.strip() or not patch.reason.strip():
        raise GraphValidationError("GraphPatch trigger and reason must be non-empty")
    if not patch.operations:
        raise GraphValidationError("GraphPatch must contain at least one operation")
    tasks = list(graph.tasks)
    groups = list(graph.groups)
    active_tasks = list(graph.active_task_refs)
    active_groups = list(graph.active_group_refs)

    for operation in patch.operations:
        if operation.op in {"add_task", "add_repair_task", "add_verification_task"}:
            task = _required_task(operation.task, operation.op)
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
            task_replacement = replace(
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
            tasks.append(task_replacement)
            active_tasks = _replace_ref(
                active_tasks,
                task.ref,
                task_replacement.ref,
            )
            tasks, active_tasks, groups, active_groups = _rewrite_pending_references(
                tasks,
                active_tasks,
                groups,
                active_groups,
                task.ref,
                task_replacement.ref,
            )
        elif operation.op in {
            "replace_pending_task",
            "set_executor_policy",
            "supersede_completed_task",
        }:
            task = _active_task_by_id(tasks, active_tasks, operation.task_id)
            _expected_revision(operation.expected_revision, task.task_revision)
            expected_status = (
                "completed"
                if operation.op == "supersede_completed_task"
                else "pending"
            )
            if task.status != expected_status:
                raise GraphValidationError(
                    f"{operation.op} requires a {expected_status} Task"
                )
            if operation.op == "set_executor_policy":
                if operation.executor_policy is None:
                    raise GraphValidationError("set_executor_policy requires executor_policy")
                replacement = replace(
                    task,
                    task_revision=task.task_revision + 1,
                    executor_policy=operation.executor_policy,
                )
            else:
                replacement = _required_task(operation.task, operation.op)
                _validate_task_replacement(task, replacement)
            _reject_existing_task(tasks, replacement.ref)
            tasks.append(replacement)
            active_tasks = _replace_ref(active_tasks, task.ref, replacement.ref)
            tasks, active_tasks, groups, active_groups = _rewrite_pending_references(
                tasks,
                active_tasks,
                groups,
                active_groups,
                task.ref,
                replacement.ref,
            )
        elif operation.op == "replace_pending_group":
            group = _active_group_by_id(groups, active_groups, operation.group_id)
            _expected_revision(operation.expected_revision, group.group_revision)
            if group.status != "pending":
                raise GraphValidationError("replace_pending_group requires a pending Group")
            group_replacement = _required_group(operation.group, operation.op)
            _validate_group_replacement(group, group_replacement)
            _reject_existing_group(groups, group_replacement.ref)
            groups.append(group_replacement)
            active_groups = _replace_ref(
                active_groups,
                group.ref,
                group_replacement.ref,
            )
            tasks, active_tasks, groups, active_groups = _rewrite_pending_references(
                tasks,
                active_tasks,
                groups,
                active_groups,
                group.ref,
                group_replacement.ref,
            )
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
            tasks, active_tasks, groups, active_groups = _rewrite_pending_references(
                tasks,
                active_tasks,
                groups,
                active_groups,
                task.ref,
                group.ref,
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
    _validate_live_dependency_targets(updated)
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
        for item in tasks
        if item.required and item.intent_binding_state in {"current", "retained"}
        for criterion in item.supports
    } | {
        criterion
        for item in groups
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
    active_keys = {item.ref.key for item in tasks} | {
        item.ref.key for item in groups
    }
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


def _active_group_by_id(
    groups: list[GroupRun],
    active_refs: list[DependencyRef],
    group_id: str | None,
) -> GroupRun:
    if not group_id:
        raise GraphValidationError("Group operation requires group_id")
    active = {ref for ref in active_refs if ref.kind == "group" and ref.id == group_id}
    if len(active) != 1:
        raise GraphValidationError(f"Group {group_id!r} is not uniquely active")
    ref = next(iter(active))
    return next(item for item in groups if item.ref == ref)


def _validate_task_replacement(current: TaskRun, replacement: TaskRun) -> None:
    if (
        replacement.task_id != current.task_id
        or replacement.task_revision != current.task_revision + 1
    ):
        raise GraphValidationError(
            "replacement Task must keep its logical ID and advance revision once"
        )
    if (
        replacement.status != "pending"
        or replacement.active_attempt_id is not None
        or replacement.output_refs
        or replacement.failure is not None
    ):
        raise GraphValidationError("replacement Task must be clean pending work")


def _validate_group_replacement(current: GroupRun, replacement: GroupRun) -> None:
    if (
        replacement.group_id != current.group_id
        or replacement.group_revision != current.group_revision + 1
    ):
        raise GraphValidationError(
            "replacement Group must keep its logical ID and advance revision once"
        )
    if (
        replacement.status != "pending"
        or replacement.winner_task_ref is not None
        or replacement.verification_record_ref is not None
    ):
        raise GraphValidationError("replacement Group must be clean pending work")


def _replace_ref(
    refs: list[DependencyRef], old: DependencyRef, new: DependencyRef
) -> list[DependencyRef]:
    return [new if ref == old else ref for ref in refs]


def _rewrite_pending_references(
    tasks: list[TaskRun],
    active_task_refs: list[DependencyRef],
    groups: list[GroupRun],
    active_group_refs: list[DependencyRef],
    old: DependencyRef,
    new: DependencyRef,
) -> tuple[
    list[TaskRun],
    list[DependencyRef],
    list[GroupRun],
    list[DependencyRef],
]:
    queue: list[tuple[DependencyRef, DependencyRef]] = [(old, new)]
    while queue:
        source, target = queue.pop(0)
        active_tasks = set(active_task_refs)
        for task in tuple(tasks):
            if (
                task.ref not in active_tasks
                or task.status != "pending"
                or source not in task.depends_on
            ):
                continue
            task_replacement = replace(
                task,
                task_revision=task.task_revision + 1,
                depends_on=tuple(
                    target if ref == source else ref for ref in task.depends_on
                ),
            )
            _reject_existing_task(tasks, task_replacement.ref)
            tasks.append(task_replacement)
            active_task_refs = _replace_ref(
                active_task_refs,
                task.ref,
                task_replacement.ref,
            )
            queue.append((task.ref, task_replacement.ref))

        active_groups = set(active_group_refs)
        for group in tuple(groups):
            if group.ref not in active_groups or group.status != "pending":
                continue
            dependency_match = source in group.depends_on
            child_match = source.kind == "task" and any(
                child.task_ref == source for child in group.children
            )
            if not dependency_match and not child_match:
                continue
            if child_match and target.kind != "task":
                raise GraphValidationError(
                    "Group child Task cannot be rewritten to a non-Task reference"
                )
            group_replacement = replace(
                group,
                group_revision=group.group_revision + 1,
                depends_on=tuple(
                    target if ref == source else ref for ref in group.depends_on
                ),
                children=tuple(
                    replace(child, task_ref=target)
                    if child.task_ref == source
                    else child
                    for child in group.children
                ),
            )
            _reject_existing_group(groups, group_replacement.ref)
            groups.append(group_replacement)
            active_group_refs = _replace_ref(
                active_group_refs,
                group.ref,
                group_replacement.ref,
            )
            queue.append((group.ref, group_replacement.ref))
    return tasks, active_task_refs, groups, active_group_refs


def _validate_live_dependency_targets(graph: TaskGraphRun) -> None:
    task_map = _task_map(graph)
    group_map = _group_map(graph)
    active_tasks = _resolve_active_tasks(graph, task_map)
    active_groups = _resolve_active_groups(graph, group_map)
    active_refs = {item.ref for item in active_tasks} | {
        item.ref for item in active_groups
    }
    for item in active_tasks:
        for ref in item.depends_on:
            target = _resolve_ref(ref, task_map, group_map)
            if ref not in active_refs and target.status != "completed":
                raise GraphValidationError(
                    f"live dependency references inactive incomplete {ref.kind} {ref.id!r}"
                )
    for group in active_groups:
        for ref in group.depends_on:
            target = _resolve_ref(ref, task_map, group_map)
            if ref not in active_refs and target.status != "completed":
                raise GraphValidationError(
                    f"live dependency references inactive incomplete {ref.kind} {ref.id!r}"
                )
        for child in group.children:
            target = _resolve_ref(child.task_ref, task_map, group_map)
            if child.task_ref not in active_refs and target.status != "completed":
                raise GraphValidationError(
                    f"Group child references inactive incomplete Task {child.task_ref.id!r}"
                )


__all__ = ["GraphValidationError", "apply_graph_patch", "ready_tasks", "validate_graph"]
