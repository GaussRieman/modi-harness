"""Tests for shared utilities: frontmatter, ULID, time, canonical-json, fingerprint, context-hash."""

from __future__ import annotations

import re

import pytest

from modi_harness._utils import (
    canonical_json,
    compute_context_hash,
    compute_fingerprint,
    new_ulid,
    now_iso,
    parse_frontmatter,
)


# --- frontmatter ---


def test_frontmatter_basic() -> None:
    text = """---
name: support-bot
description: hi
---

You are a bot.
"""
    fm, body = parse_frontmatter(text)
    assert fm == {"name": "support-bot", "description": "hi"}
    assert body.strip() == "You are a bot."


def test_frontmatter_hyphen_normalized_to_underscore() -> None:
    text = """---
allowed-tools: [a, b]
risk-notes:
  - one
---
body
"""
    fm, _ = parse_frontmatter(text)
    assert fm["allowed_tools"] == ["a", "b"]
    assert fm["risk_notes"] == ["one"]
    assert "allowed-tools" not in fm
    assert "risk-notes" not in fm


def test_frontmatter_underscore_passes_through() -> None:
    text = """---
allowed_tools: []
---
"""
    fm, _ = parse_frontmatter(text)
    assert fm["allowed_tools"] == []


def test_frontmatter_no_frontmatter() -> None:
    text = "Just a body\nwith lines."
    fm, body = parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_frontmatter_missing_closing_fence_raises() -> None:
    text = "---\nname: x\nbody without close"
    with pytest.raises(ValueError, match="closing"):
        parse_frontmatter(text)


def test_frontmatter_empty_block() -> None:
    text = "---\n---\nbody"
    fm, body = parse_frontmatter(text)
    assert fm == {}
    assert body.strip() == "body"


def test_frontmatter_unknown_keys_preserved() -> None:
    text = """---
name: x
description: y
custom_key: keep_me
---
"""
    fm, _ = parse_frontmatter(text)
    assert fm["custom_key"] == "keep_me"


def test_frontmatter_collision_prefers_underscore() -> None:
    # Both spellings present: underscore wins, hyphen dropped.
    text = """---
allowed_tools: [a]
allowed-tools: [b, c]
---
"""
    fm, _ = parse_frontmatter(text)
    assert fm["allowed_tools"] == [a for a in ["a"]]


# --- ULID ---


_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_new_ulid_format() -> None:
    u = new_ulid()
    assert _ULID_PATTERN.match(u), u


def test_new_ulid_unique_and_monotonic_per_ms() -> None:
    seen = [new_ulid() for _ in range(50)]
    assert len(set(seen)) == 50
    # ULIDs encode timestamp lexicographically; sorted seen should be sorted in produce order.
    assert seen == sorted(seen)


# --- time ---


_ISO_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def test_now_iso_format() -> None:
    t = now_iso()
    assert _ISO_PATTERN.match(t), t


# --- canonical_json ---


def test_canonical_json_sorts_keys() -> None:
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b
    assert a == b'{"a":2,"b":1}'


def test_canonical_json_no_whitespace() -> None:
    out = canonical_json({"a": [1, 2, 3]})
    assert b" " not in out


def test_canonical_json_recurses_into_lists_and_dicts() -> None:
    out = canonical_json({"x": [{"b": 1, "a": 2}]})
    assert out == b'{"x":[{"a":2,"b":1}]}'


def test_canonical_json_unicode_preserved() -> None:
    out = canonical_json({"k": "中文"})
    assert "中文".encode() in out


# --- fingerprint ---


def test_fingerprint_stable_across_key_order() -> None:
    a = compute_fingerprint({"tool": "x", "args": {"a": 1, "b": 2}})
    b = compute_fingerprint({"args": {"b": 2, "a": 1}, "tool": "x"})
    assert a == b


def test_fingerprint_different_for_different_payload() -> None:
    a = compute_fingerprint({"tool": "x"})
    b = compute_fingerprint({"tool": "y"})
    assert a != b


def test_fingerprint_is_hex_sha256() -> None:
    f = compute_fingerprint({"x": 1})
    assert re.match(r"^[0-9a-f]{64}$", f)


# --- context_hash ---


def test_context_hash_excludes_volatile_fields() -> None:
    pack_a = {"system_instruction": "s", "context_hash": "OLD", "raw": object()}
    pack_b = {"system_instruction": "s", "context_hash": "DIFFERENT", "raw": object()}
    # context_hash and raw are excluded from the hash input
    assert compute_context_hash(pack_a) == compute_context_hash(pack_b)


def test_context_hash_changes_with_meaningful_field() -> None:
    a = compute_context_hash({"system_instruction": "s1"})
    b = compute_context_hash({"system_instruction": "s2"})
    assert a != b
