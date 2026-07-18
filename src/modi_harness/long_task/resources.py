"""Pure exclusive-by-path resource lock derivation."""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass

from .types import TaskAttempt


class ResourcePathError(ValueError):
    """A declared exclusive resource path is not an absolute lexical path."""


@dataclass(frozen=True, slots=True)
class ResourceConflict:
    """One requested path overlaps a lock retained by an Attempt."""

    requested_key: str
    held_key: str
    holder_attempt_id: str
    holder_retiring: bool


def canonical_resource_path(path: str) -> str:
    """Return a stable lexical form for an absolute POSIX resource path."""

    if not isinstance(path, str) or not path.strip():
        raise ResourcePathError("resource path must be a non-empty string")
    value = path.strip()
    if not value.startswith("/"):
        raise ResourcePathError(f"resource path must be absolute: {path!r}")
    normalized = posixpath.normpath(value)
    if not normalized.startswith("/"):
        raise ResourcePathError(f"resource path escapes the absolute root: {path!r}")
    return normalized


def canonical_resource_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Canonicalize, deduplicate, and deterministically order resource paths."""

    return tuple(sorted({canonical_resource_path(path) for path in paths}))


def resource_paths_conflict(left: str, right: str) -> bool:
    """Return whether two exclusive paths are equal or ancestor-related."""

    first = canonical_resource_path(left)
    second = canonical_resource_path(right)
    return first == second or _is_ancestor(first, second) or _is_ancestor(second, first)


def resource_sets_conflict(left: Iterable[str], right: Iterable[str]) -> bool:
    """Return whether any exclusive path in two declarations overlaps."""

    left_paths = canonical_resource_paths(left)
    right_paths = canonical_resource_paths(right)
    return any(
        resource_paths_conflict(left_path, right_path)
        for left_path in left_paths
        for right_path in right_paths
    )


def attempt_holds_resources(attempt: TaskAttempt) -> bool:
    """Return whether an Attempt still owns its declared resource locks."""

    return attempt.lease.retiring or attempt.status in {
        "created",
        "leased",
        "running",
        "waiting",
        "submitted",
    }


def exclusive_path_conflicts(
    requested_paths: Iterable[str],
    attempts: Iterable[TaskAttempt],
) -> tuple[ResourceConflict, ...]:
    """Derive all deterministic conflicts with active or retiring Attempts."""

    requested = canonical_resource_paths(requested_paths)
    conflicts: list[ResourceConflict] = []
    for attempt in sorted(attempts, key=lambda item: item.attempt_id):
        if not attempt_holds_resources(attempt):
            continue
        held_paths = canonical_resource_paths(attempt.lease.resource_keys)
        for requested_key in requested:
            for held_key in held_paths:
                if resource_paths_conflict(requested_key, held_key):
                    conflicts.append(
                        ResourceConflict(
                            requested_key=requested_key,
                            held_key=held_key,
                            holder_attempt_id=attempt.attempt_id,
                            holder_retiring=attempt.lease.retiring,
                        )
                    )
    return tuple(conflicts)


def _is_ancestor(parent: str, child: str) -> bool:
    if parent == "/":
        return child != "/"
    return child.startswith(f"{parent}/")


__all__ = [
    "ResourceConflict",
    "ResourcePathError",
    "attempt_holds_resources",
    "canonical_resource_path",
    "canonical_resource_paths",
    "exclusive_path_conflicts",
    "resource_paths_conflict",
    "resource_sets_conflict",
]
