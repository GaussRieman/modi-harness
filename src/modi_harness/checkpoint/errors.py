"""Checkpoint-related exception types."""

from __future__ import annotations


class CheckpointError(Exception):
    """Base class for checkpointer failures surfaced from Modi."""


class CheckpointConfigError(CheckpointError):
    """Configuration is incomplete or contradictory."""
