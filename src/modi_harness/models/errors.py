"""Model-layer exceptions."""

from __future__ import annotations


class ModelConfigError(Exception):
    """Raised when model configuration is invalid or a provider package is missing."""
