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
_RUN_PY = Path(__file__).resolve().parents[2] / "examples" / "research_assistant" / "run.py"


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
    assert "用户给定的每个 URL 最多 fetch 一次" in body
    assert "直接读取 fetch_url 返回的 title 和 content" in body
    assert "EVIDENCE 阶段不调用 source_extract" in body
    assert "不要向用户展示证据稿、来源评分表或资料充分性清单" in body
    assert "只允许调用 submit_output" in body
    assert "最终必须通过 submit_output 提交" in body
    assert "不写报告体" in body
    assert "资料充分" in body
    assert "即使部分维度缺证，也必须基于已证实内容进入 SUBMIT" in body


def test_prompt_restricts_default_tool_use():
    body = _body()
    assert "不调用 list_workspace_dir 或 workspace/list 工具" in body
    assert "不主动调用 recall_memory" in body
    assert "不保存中间产物" in body


def test_prompt_requires_agent_managed_dynamic_task_list():
    body = _body()
    assert "动态拆分 3-4 个产出导向任务" in body
    assert "收到 [interactive_startup]" in body
    assert "使用 url_list 收集 source_urls" in body
    assert "input_type 使用 confirm" in body
    assert "用户直接回车时采用 default" in body
    assert "调用 create_task_plan 后直接执行" in body
    assert "第一轮禁止同时调用 fetch_url" in body
    assert "创建计划的轮次不要输出任何助手文本" in body
    assert "任务计划不需要人工确认或修改" in body
    assert "create_task_plan 成功后的下一轮立即调用 start_task" in body
    assert "不得照抄固定阶段名称" in body
    assert "调用 start_task" in body
    assert "调用 complete_task" in body
    assert "不要在普通文本中重复输出任务列表" in body
    assert "不能批量补勾" in body
    assert "内部分析任务也必须逐项推进" in body
    assert "最后一项 complete_task 成功后的下一轮" in body
    assert "current_action 描述正在执行的具体动作" in body
    assert "current_action 必须描述 next_task_id 对应的下一任务" in body
    assert "不得把页面字段机械拆成互不关联的任务" in body
    assert "禁止使用“提取数据”" in body
    assert "任务应形成依赖链" in body
    assert "summary 只写一句最重要的新结果" in body
    assert "不能重复扫描同一来源并换一种说法" in body
    assert "最多 18 个中文字符" in body
    assert "不得自行发明综合评分、权重或硬阈值" in body
    assert "不得添加“国内”“主流”“行业最佳”" in body
    assert "不得声称“性价比高”" in body


def test_source_evaluation_outputs_structured_evidence_draft():
    text = _SOURCE_EVALUATION_MD.read_text(encoding="utf-8")
    for key in (
        "comparison_dimensions",
        "claims",
        "evidence",
        "source_coverage",
        "open_questions",
        "task_results",
    ):
        assert key in text
    assert "Do not call `fetch_url` for a URL that has already been fetched" in text
    assert "Do not produce the final briefing or call `submit_output`" in text
    assert "Do not call `source_extract` in the default path" in text
    assert "Read the `title` and `content` returned by `fetch_url`" in text
    assert "Select only evidence that is relevant to the research question" in text
    assert "do not display markdown tables, JSON code blocks, or sufficiency checklists" in text
    assert "Every evidence entry must include `source_url` or `source_id`" in text
    assert "Extract no more than 5 evidence entries per source" in text
    assert "Keep total evidence entries at 8 or fewer" in text
    assert "Each evidence entry should be a complete checkable fact" in text
    assert "Do not generate explanatory long paragraphs" in text
    assert "Do not generate background introductions or complete paragraphs" in text


def test_briefing_structure_submit_stage_is_terminal():
    text = _BRIEFING_STRUCTURE_MD.read_text(encoding="utf-8")
    assert "This skill runs only in the SUBMIT stage." in text
    assert "Only call `submit_output`" in text
    assert "Do not call `recall_memory` when harness memory is present and sufficient" in text
    assert "Use only extracted evidence from the evidence draft" in text
    assert "Do not write a report body" in text
    assert "Partial coverage is acceptable" in text
    assert "Avoid generic phrases such as" in text
    assert "completed task summaries" in text
    assert "Every completed task must appear exactly once" in text


def test_briefing_structure_maps_completed_tasks_to_output():
    text = _BRIEFING_STRUCTURE_MD.read_text(encoding="utf-8")
    assert "one `task_results` entry for every completed task" in text
    assert "preserving plan order" in text
    assert "Every completed task must appear exactly once" in text
    assert "Do not repeat task results" in text
    assert "label it as an inference" in text


def test_interactive_question_generation_stays_source_scoped():
    text = _RUN_PY.read_text(encoding="utf-8")
    assert "单个 URL 时，只问这个页面本身能支撑的问题" in text
    assert "不要只问原始数字清单" in text
    assert "不要主动加入竞品对比" in text
    assert "只有多个 URL 明确来自不同对象" in text
    assert "不要像论文题目" in text
    assert "不超过 60 个中文字符" in text


def test_interactive_question_confirmation_uses_single_prompt_loop():
    text = _RUN_PY.read_text(encoding="utf-8")
    assert "直接回车开始研究" in text
    assert "输入修改意见或完整问题继续调整" in text
    assert "输入 /cancel 退出" in text
    assert "选择 (1/2/3)" not in text
    assert "确认使用这个问题？(y/n)" not in text
