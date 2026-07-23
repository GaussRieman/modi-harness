"""Unit tests for the optional Doubao Global Search provider."""

from __future__ import annotations

import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents"))

import research_assistant.tools.research as research  # noqa: E402
from research_assistant.tools.doubao import (  # noqa: E402
    DoubaoSearchConfig,
    config_from_tool_settings,
    search_doubao,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode()


def _config() -> DoubaoSearchConfig:
    return DoubaoSearchConfig(api_key="secret-key")


def test_legacy_ark_base_url_is_migrated_to_global_search() -> None:
    class Settings:
        doubao_search_api_key = "key"
        doubao_search_base_url = "https://ark.cn-beijing.volces.com/api/v3/responses"
        doubao_search_timeout = 20
        doubao_search_limit = 10
        doubao_search_max_snippet_length = 1000

    assert config_from_tool_settings(Settings).base_url == (
        "https://open.feedcoopapi.com/search_api/global_search"
    )


def test_global_search_request_and_document_normalization() -> None:
    payload = {
        "ResponseMetadata": {"RequestId": "request-1"},
        "Result": {
            "TotalDocCount": 2,
            "Documents": [
                {
                    "Rank": 0,
                    "Url": "https://example.test/source",
                    "Title": "Example source",
                    "Snippet": [
                        {"Type": "text", "Text": "A useful public excerpt."},
                        {"Type": "image", "Image": {"ImageUrl": "https://image.test/x"}},
                    ],
                },
                {"Url": "https://example.test/source", "Title": "duplicate"},
                {"Url": "not-a-url", "Title": "invalid"},
            ],
        },
    }
    seen: dict[str, object] = {}

    def open_request(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(payload)

    with patch("research_assistant.tools.doubao.urllib.request.urlopen", side_effect=open_request):
        result = search_doubao("  向亚运  ", _config())

    request = seen["request"]
    body = json.loads(request.data)
    assert request.full_url == "https://open.feedcoopapi.com/search_api/global_search"
    assert request.get_header("Authorization") == "Bearer secret-key"
    assert body == {
        "Query": "向亚运",
        "DocCount": 6,
        "MaxSnippetLength": 1000,
        "MaxImageCountPerDoc": 3,
    }
    assert seen["timeout"] == 20.0
    assert result["status"] == "ok"
    assert result["results"] == [
        {
            "title": "Example source",
            "url": "https://example.test/source",
            "snippet": "A useful public excerpt.",
        }
    ]
    assert "secret-key" not in json.dumps(result)


def test_global_search_response_error_is_reported_without_secret() -> None:
    payload = {
        "ResponseMetadata": {"Error": {"Code": "InvalidApiKey", "Message": "bad key"}},
        "Result": None,
    }
    with patch(
        "research_assistant.tools.doubao.urllib.request.urlopen", return_value=_Response(payload)
    ):
        result = search_doubao("query", _config())

    assert result["status"] == "failed"
    assert result["error"] == "InvalidApiKey: bad key"


def test_global_search_http_failure_and_unconfigured_provider_are_isolated() -> None:
    with patch("research_assistant.tools.doubao.urllib.request.urlopen") as open_request:
        open_request.side_effect = urllib.error.HTTPError(
            "https://open.feedcoopapi.com/search_api/global_search",
            401,
            "unauthorized",
            {},
            BytesIO(b'{"ResponseMetadata":{"Error":{"Code":"Unauthorized"}}}'),
        )
        failed = search_doubao("query", _config())

    assert failed["status"] == "blocked"
    assert failed["error"] == "HTTP 401 (Unauthorized)"
    assert search_doubao("query", DoubaoSearchConfig())["status"] == "empty"


def test_doubao_is_added_to_search_fanout_only_when_enabled() -> None:
    config = _config()
    with patch.object(
        research,
        "_search_provider",
        return_value={"provider": "x", "query": "q", "status": "empty", "results": [], "error": None},
    ) as search:
        research._run_searches(["q"])
        assert {call.args[0] for call in search.call_args_list} == {"bing_rss", "baidu", "duckduckgo"}
        search.reset_mock()
        research._run_searches(["q"], doubao_config=config)
        assert {call.args[0] for call in search.call_args_list} == {
            "bing_rss",
            "baidu",
            "duckduckgo",
            "doubao",
        }
