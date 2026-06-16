"""The research-assistant prompt should describe domain behavior, not Harness internals (spec §8)."""

from __future__ import annotations

from pathlib import Path

_AGENT_MD = (
    Path(__file__).resolve().parents[2]
    / "examples" / "research_assistant" / "agents" / "research-assistant.md"
)
_SOURCE_EVALUATION_MD = (
    Path(__file__).resolve().parents[2]
    / "examples" / "research_assistant" / "skills" / "source-evaluation" / "SKILL.md"
)
_BRIEFING_STRUCTURE_MD = (
    Path(__file__).resolve().parents[2]
    / "examples" / "research_assistant" / "skills" / "briefing-structure" / "SKILL.md"
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


def test_prompt_enforces_staged_research_flow():
    body = _body()
    assert "PLAN → FETCH → EVIDENCE → SUBMIT" in body
    assert "不输出长计划，直接进入 FETCH" in body
    assert "每个 URL 最多调用一次 fetch_url" in body
    assert "source_extract 阶段只允许调用工具" in body
    assert "参数必须短，只包含 source_id、url、extraction_profile" in body
    assert "进入 SUBMIT 阶段后，只允许调用 submit_output" in body
    assert "最终输出必须通过 submit_output 提交，不写报告体" in body
    assert "资料充分" in body


def test_prompt_restricts_default_tool_use():
    body = _body()
    assert "默认不要调用 list_workspace_dir" in body
    assert "默认不要主动 recall_memory" in body
    assert "不要默认保存中间产物" in body


def test_source_evaluation_outputs_structured_evidence_draft():
    text = _SOURCE_EVALUATION_MD.read_text(encoding="utf-8")
    for key in (
        "comparison_dimensions",
        "claims",
        "evidence",
        "source_coverage",
        "open_questions",
    ):
        assert key in text
    assert "Do not call `fetch_url` for a URL that has already been fetched" in text
    assert "Do not produce the final briefing or call `submit_output`" in text
    assert "After `source_extract`, carry forward only extracted evidence" in text
    assert "Every evidence entry must include `source_url` or `source_id`" in text
    assert "emit only the tool call" in text
    assert "pass only `source_id`, `url`, and `extraction_profile`" in text
    assert "Extract no more than 3 evidence entries per source" in text
    assert "Keep total evidence entries at 6 or fewer" in text
    assert "Each evidence entry must be no longer than 120 Chinese characters" in text
    assert "Do not generate explanatory long paragraphs" in text
    assert "Do not generate background introductions or complete paragraphs" in text


def test_briefing_structure_submit_stage_is_terminal():
    text = _BRIEFING_STRUCTURE_MD.read_text(encoding="utf-8")
    assert "This skill runs only in the SUBMIT stage." in text
    assert "Only call `submit_output`" in text
    assert "Do not call `recall_memory` when harness memory is present and sufficient" in text
    assert "submit the answer immediately" in text
    assert "Use only extracted evidence from the evidence draft" in text
    assert "Do not write a report body" in text


def test_briefing_structure_limits_final_output_size():
    text = _BRIEFING_STRUCTURE_MD.read_text(encoding="utf-8")
    assert "`key_findings` must contain no more than 5 entries" in text
    assert "`evidence` must contain no more than 6 entries" in text
    assert "`open_questions` must contain no more than 3 entries" in text
    assert "Each key finding must be no longer than 80 Chinese characters" in text
    assert "Each evidence entry must be no longer than 120 Chinese characters" in text
    assert "Final output is not a report body" in text
