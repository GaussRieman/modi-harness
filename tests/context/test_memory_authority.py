"""Tests for authority-aware memory blocks in ContextManager."""

from __future__ import annotations

from modi_harness.context.manager import _memory_blocks
from modi_harness.types import MemoryIndex


def test_memory_blocks_preserve_authority_score_and_reasons() -> None:
    idx: MemoryIndex = {
        "records": [
            {
                "id": "m1",
                "scope": "user",
                "type": "feedback",
                "name": "n",
                "description": "d",
                "body": "b",
                "tags": [],
                "source_run_id": None,
                "created_at": "",
                "updated_at": "",
                "expires_at": None,
                "metadata": {
                    "authority": "context",
                    "selection_score": 2.5,
                    "selection_reasons": ["query:body"],
                },
            }
        ],
        "by_scope": {},
        "by_type": {},
        "by_tag": {},
    }

    block = _memory_blocks(idx)[0]

    assert block["authority"] == "context"
    assert block["score"] == 2.5
    assert block["reasons"] == ["query:body"]
