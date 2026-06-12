"""The research-assistant prompt should describe domain behavior, not Harness internals (spec §8)."""

from __future__ import annotations

from pathlib import Path

_AGENT_MD = (
    Path(__file__).resolve().parents[2]
    / "examples" / "research_assistant" / "agents" / "research-assistant.md"
)


def _body() -> str:
    text = _AGENT_MD.read_text(encoding="utf-8")
    # Strip the YAML frontmatter (between the first two '---' lines).
    parts = text.split("---", 2)
    assert len(parts) == 3, "expected YAML frontmatter delimited by ---"
    return parts[2]


def test_prompt_body_has_no_harness_internals():
    body = _body()
    for term in ("scope", "trace", "artifact", "draft", "propose_memory", "save_memory"):
        assert term not in body, f"agent prompt should not mention Harness internal {term!r}"


def test_prompt_body_keeps_domain_behavior():
    body = _body()
    assert "研究" in body          # domain: research
    assert "证据" in body          # domain: evidence
    assert "source-evaluation" in body  # references its skills
