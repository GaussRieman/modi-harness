"""Optional Doubao Global Search provider."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_RATE_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0
_MIN_REQUEST_INTERVAL_SECONDS = 0.21


@dataclass(frozen=True, slots=True)
class DoubaoSearchConfig:
    api_key: str = ""
    base_url: str = "https://open.feedcoopapi.com/search_api/global_search"
    timeout: float = 20.0
    limit: int = 6
    max_snippet_length: int = 1000

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.strip())


def config_from_tool_settings(settings: Any) -> DoubaoSearchConfig:
    base_url = str(settings.doubao_search_base_url).rstrip("/")
    # Migrate the earlier Ark Responses setting to the Global Search endpoint.
    if "ark.cn-beijing.volces.com" in base_url:
        base_url = "https://open.feedcoopapi.com/search_api/global_search"
    return DoubaoSearchConfig(
        api_key=str(settings.doubao_search_api_key or ""),
        base_url=base_url,
        timeout=float(settings.doubao_search_timeout),
        limit=int(settings.doubao_search_limit),
        max_snippet_length=int(settings.doubao_search_max_snippet_length),
    )


def search_doubao(query: str, config: DoubaoSearchConfig) -> dict[str, Any]:
    """Call Global Search once and normalize its documents into search results."""

    normalized_query = " ".join(str(query or "").split())[:100]
    if not config.enabled:
        return _record(normalized_query, config, "empty", [])
    if not normalized_query:
        return _record(normalized_query, config, "failed", [], "query is empty")

    body = {
        "Query": normalized_query,
        "DocCount": min(config.limit, 20),
        "MaxSnippetLength": min(config.max_snippet_length, 3000),
        "MaxImageCountPerDoc": 3,
    }
    request = urllib.request.Request(
        config.base_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        _wait_for_rate_slot()
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            payload = json.loads(response.read(4_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = "blocked" if exc.code in {401, 403, 429} else "failed"
        return _record(normalized_query, config, status, [], _http_error(exc, config.api_key))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _record(normalized_query, config, "failed", [], _safe_error(exc))
    except (UnicodeError, json.JSONDecodeError):
        return _record(normalized_query, config, "failed", [], "response was not valid JSON")

    if not isinstance(payload, Mapping):
        return _record(normalized_query, config, "failed", [], "response root was not an object")
    error = _response_error(payload)
    if error:
        return _record(normalized_query, config, "failed", [], error)
    results = _documents(payload, config.limit)
    return _record(normalized_query, config, "ok" if results else "empty", results)


def _wait_for_rate_slot() -> None:
    """Keep concurrent Task Graph children below the documented 5 QPS limit."""

    global _NEXT_REQUEST_AT
    with _RATE_LOCK:
        now = time.monotonic()
        delay = max(0.0, _NEXT_REQUEST_AT - now)
        _NEXT_REQUEST_AT = max(now, _NEXT_REQUEST_AT) + _MIN_REQUEST_INTERVAL_SECONDS
    if delay:
        time.sleep(delay)


def _record(
    query: str,
    config: DoubaoSearchConfig,
    status: str,
    results: list[dict[str, str]],
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": "doubao",
        "query": query,
        "search_url": config.base_url,
        "status": status,
        "results": results,
        "error": error,
    }


def _documents(payload: Mapping[str, Any], limit: int) -> list[dict[str, str]]:
    result = payload.get("Result")
    if not isinstance(result, Mapping):
        return []
    documents = result.get("Documents")
    if not isinstance(documents, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        url = str(document.get("Url") or "").strip()
        if not _is_http_url(url) or url in seen:
            continue
        seen.add(url)
        title = " ".join(str(document.get("Title") or "").split())[:180] or url
        snippets = document.get("Snippet")
        text_parts = []
        if isinstance(snippets, list):
            for snippet in snippets:
                if isinstance(snippet, Mapping) and str(snippet.get("Type") or "") == "text":
                    text = " ".join(str(snippet.get("Text") or "").split())
                    if text:
                        text_parts.append(text)
        normalized.append({"title": title, "url": url, "snippet": " ".join(text_parts)[:1000]})
        if len(normalized) >= limit:
            break
    return normalized


def _response_error(payload: Mapping[str, Any]) -> str | None:
    metadata = payload.get("ResponseMetadata")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("Error"), Mapping):
        error = metadata["Error"]
        code = " ".join(str(error.get("Code") or error.get("code") or "").split())
        message = " ".join(str(error.get("Message") or error.get("message") or "").split())
        return ": ".join(item for item in (code, message) if item) or "search request failed"
    result = payload.get("Result")
    if isinstance(result, Mapping):
        result_code = result.get("ErrorCode")
        message = " ".join(str(result.get("ErrorMsg") or "").split())
        if result_code not in (None, "", 0, "0") or message:
            return ": ".join(
                item
                for item in (
                    str(result_code) if result_code not in (None, "", 0, "0") else "",
                    message,
                )
                if item
            )
    return None


def _http_error(exc: urllib.error.HTTPError, api_key: str) -> str:
    try:
        payload = json.loads(exc.read(16_384).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = None
    detail = _response_error(payload) if isinstance(payload, Mapping) else None
    text = f"HTTP {exc.code}" + (f" ({detail})" if detail else "")
    return text.replace(api_key, "[redacted]") if api_key else text


def _safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    lowered = text.lower()
    if isinstance(exc, TimeoutError) or "timed out" in lowered or "timeout" in lowered:
        return "request timed out"
    if "nodename nor servname" in lowered or "name or service not known" in lowered:
        return "DNS lookup failed"
    return text[:240] or exc.__class__.__name__


def _is_http_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


__all__ = ["DoubaoSearchConfig", "config_from_tool_settings", "search_doubao"]
