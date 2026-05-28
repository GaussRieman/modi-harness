"""Workspace Manager exceptions."""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base class for workspace errors."""


class WorkspacePathError(WorkspaceError):
    """A write target resolved outside the run workspace."""


class WorkspaceRunMissingError(WorkspaceError):
    """The named run has no workspace yet."""
