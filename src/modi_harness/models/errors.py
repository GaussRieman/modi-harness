"""Model-layer exceptions."""

from __future__ import annotations

import re
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


# Patterns for classify_error
_HTTP_STATUS_RE = re.compile(r"\b(4\d{2}|5\d{2})\b")
_MAX_TOKEN_RE = re.compile(r"max\s+\w*\s*token", re.IGNORECASE)


def classify_error(exc: Exception) -> ModelErrorCode:
    """Classify an exception into a normalized ModelErrorCode.

    Pattern-matches on exception type and message string (case-insensitive).
    """
    # Type-based checks first
    if isinstance(exc, TimeoutError):
        return ModelErrorCode.TIMEOUT
    if isinstance(exc, ConnectionError):
        return ModelErrorCode.SERVER_ERROR

    msg = str(exc).lower()

    # TIMEOUT
    if "timeout" in msg:
        return ModelErrorCode.TIMEOUT

    # RATE_LIMITED
    if "429" in msg or "rate limit" in msg:
        return ModelErrorCode.RATE_LIMITED

    # AUTH_FAILED
    if any(pattern in msg for pattern in ("401", "403", "auth", "permission", "api key")):
        return ModelErrorCode.AUTH_FAILED

    # CONTENT_FILTERED
    if any(pattern in msg for pattern in ("content filter", "safety", "blocked")):
        return ModelErrorCode.CONTENT_FILTERED

    # CONTEXT_LENGTH_EXCEEDED
    if "context length" in msg or "token limit" in msg or _MAX_TOKEN_RE.search(msg):
        return ModelErrorCode.CONTEXT_LENGTH_EXCEEDED

    # SERVER_ERROR — HTTP 5xx or "server error" in message
    status_match = _HTTP_STATUS_RE.search(str(exc))
    if status_match:
        code = int(status_match.group(1))
        if 500 <= code <= 599:
            return ModelErrorCode.SERVER_ERROR
    if "server error" in msg:
        return ModelErrorCode.SERVER_ERROR

    return ModelErrorCode.UNKNOWN
