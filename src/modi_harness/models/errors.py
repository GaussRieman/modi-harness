"""Model-layer exceptions."""

from __future__ import annotations

from enum import Enum


class ModelConfigError(Exception):
    """Raised when model configuration is invalid or a provider package is missing."""


class ModelErrorCode(str, Enum):
    """Normalized error codes for model invocation failures."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    CONTENT_FILTERED = "content_filtered"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


class ModelError(Exception):
    """Structured model invocation error with a normalized code."""

    def __init__(
        self,
        *,
        code: ModelErrorCode,
        message: str,
        provider: str,
        original: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider = provider
        self.original = original
