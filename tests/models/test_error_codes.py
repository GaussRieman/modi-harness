"""Tests for classify_error and ModelErrorCode."""

from __future__ import annotations

import pytest

from modi_harness.models.errors import ModelErrorCode, classify_error


class TestClassifyError:
    """One test per error code, plus edge cases."""

    def test_timeout_from_exception_type(self) -> None:
        exc = TimeoutError("request timed out")
        assert classify_error(exc) == ModelErrorCode.TIMEOUT

    def test_timeout_from_message(self) -> None:
        exc = Exception("The operation hit a timeout")
        assert classify_error(exc) == ModelErrorCode.TIMEOUT

    def test_rate_limited_429(self) -> None:
        exc = Exception("HTTP 429 Too Many Requests")
        assert classify_error(exc) == ModelErrorCode.RATE_LIMITED

    def test_rate_limited_message(self) -> None:
        exc = Exception("Rate limit exceeded, please retry later")
        assert classify_error(exc) == ModelErrorCode.RATE_LIMITED

    def test_auth_failed_401(self) -> None:
        exc = Exception("HTTP 401 Unauthorized")
        assert classify_error(exc) == ModelErrorCode.AUTH_FAILED

    def test_auth_failed_403(self) -> None:
        exc = Exception("HTTP 403 Forbidden")
        assert classify_error(exc) == ModelErrorCode.AUTH_FAILED

    def test_auth_failed_api_key(self) -> None:
        exc = Exception("Invalid API key provided")
        assert classify_error(exc) == ModelErrorCode.AUTH_FAILED

    def test_auth_failed_permission(self) -> None:
        exc = Exception("You do not have permission to access this model")
        assert classify_error(exc) == ModelErrorCode.AUTH_FAILED

    def test_content_filtered(self) -> None:
        exc = Exception("Content filter triggered: safety violation")
        assert classify_error(exc) == ModelErrorCode.CONTENT_FILTERED

    def test_content_filtered_blocked(self) -> None:
        exc = Exception("Request blocked by content policy")
        assert classify_error(exc) == ModelErrorCode.CONTENT_FILTERED

    def test_content_filtered_safety(self) -> None:
        exc = Exception("Output omitted due to safety concerns")
        assert classify_error(exc) == ModelErrorCode.CONTENT_FILTERED

    def test_context_length_exceeded(self) -> None:
        exc = Exception("This model's maximum context length is 4096 tokens")
        assert classify_error(exc) == ModelErrorCode.CONTEXT_LENGTH_EXCEEDED

    def test_context_length_token_limit(self) -> None:
        exc = Exception("Token limit exceeded for this request")
        assert classify_error(exc) == ModelErrorCode.CONTEXT_LENGTH_EXCEEDED

    def test_context_length_max_tokens(self) -> None:
        exc = Exception("max tokens exceeded")
        assert classify_error(exc) == ModelErrorCode.CONTEXT_LENGTH_EXCEEDED

    def test_server_error_connection(self) -> None:
        exc = ConnectionError("Connection reset by peer")
        assert classify_error(exc) == ModelErrorCode.SERVER_ERROR

    def test_server_error_500(self) -> None:
        exc = Exception("HTTP 500 Internal Server Error")
        assert classify_error(exc) == ModelErrorCode.SERVER_ERROR

    def test_server_error_502(self) -> None:
        exc = Exception("HTTP 502 Bad Gateway")
        assert classify_error(exc) == ModelErrorCode.SERVER_ERROR

    def test_server_error_message(self) -> None:
        exc = Exception("server error: upstream unavailable")
        assert classify_error(exc) == ModelErrorCode.SERVER_ERROR

    def test_unknown_fallback(self) -> None:
        exc = Exception("something completely unexpected happened")
        assert classify_error(exc) == ModelErrorCode.UNKNOWN

    def test_case_insensitive(self) -> None:
        exc = Exception("TIMEOUT occurred")
        assert classify_error(exc) == ModelErrorCode.TIMEOUT
