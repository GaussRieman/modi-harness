"""Composition root for the Workflow-governed Research Assistant."""

from __future__ import annotations

from pathlib import Path

from modi_harness import ModiAgent, ToolBinding
from modi_harness.skills import SkillLoader
from modi_harness.types import PermissionProfile, Skill
from modi_harness.workflow import parse_workflow_yaml

from .tools import (
    BUILD_EVIDENCE_GRAPH_SPEC,
    GET_CURRENT_TIME_SPEC,
    PUBLIC_WEB_RESEARCH_SPEC,
    PUBLIC_WEB_SEARCH_SPEC,
    RECORD_RESEARCH_FINDING_SPEC,
    REJECT_RESEARCH_REQUEST_SPEC,
    VERIFY_CLAIM_EVIDENCE_SPEC,
    build_evidence_graph,
    get_current_time,
    public_web_research,
    public_web_search,
    record_research_finding,
    reject_research_request,
    verify_claim_evidence,
)

PACKAGE_DIR = Path(__file__).parent

_TOOL_DEFINITIONS = (
    (GET_CURRENT_TIME_SPEC, get_current_time),
    (PUBLIC_WEB_RESEARCH_SPEC, public_web_research),
    (PUBLIC_WEB_SEARCH_SPEC, public_web_search),
    (VERIFY_CLAIM_EVIDENCE_SPEC, verify_claim_evidence),
    (RECORD_RESEARCH_FINDING_SPEC, record_research_finding),
    (BUILD_EVIDENCE_GRAPH_SPEC, build_evidence_graph),
    (REJECT_RESEARCH_REQUEST_SPEC, reject_research_request),
)


def build_agent() -> ModiAgent:
    """Build the complete trusted Agent definition for discovery and direct use."""

    tools = tuple(
        ToolBinding(spec=spec, handler=handler) for spec, handler in _TOOL_DEFINITIONS
    )
    skill_loader = SkillLoader(project_dir=PACKAGE_DIR / "skills")
    skills = tuple(
        Skill(
            name=name,
            profile=skill_loader.load_skill(name),
            source_path=PACKAGE_DIR / "skills" / name,
        )
        for name in ("query-planning", "web-research")
    )
    tool_ids = {binding.spec["name"] for binding in tools}
    workflows = tuple(
        parse_workflow_yaml(
            path.read_text(encoding="utf-8"),
            source=str(path),
            known_validators=set(),
            agent_tools=tool_ids,
        )
        for path in sorted((PACKAGE_DIR / "workflows").glob("*.yaml"))
    )
    return ModiAgent(
        name="research-assistant",
        description="Source-grounded autonomous research and briefing Agent.",
        instruction=(
            "你只处理公开资料研究, 严格执行 Router 选择的 Workflow。简单查询依据已提供的"
            "research_result 直接回答; 深度研究先确认必要研究范围和问题拆分, 再针对每个"
            "问题现场判断合适的 verification_method, 逐项检索、验证和综合。搜索只收集证据, "
            "不解决问题; 判定为 unverifiable_flag 的问题直接记录 blocked, 不得搜索。"
            "每一次公开网络搜索前必须先调用 get_current_time, 并把它刚返回的 time_token"
            "原样传给紧随其后的搜索; 补搜前必须重新取时。使用 query-planning Skill 将实体、"
            "别名和单一研究维度组织成结构化 searches, 不得把多个实体塞进同一条长查询。"
            "只依据 public_web_research 或 public_web_search 返回的 usable 来源陈述事实, "
            "引用真实 URL; 证据缺口必须记录为明确 limitation 并继续其余问题。不得推断主体不存在。"
            "收集到候选证据后必须先调用 verify_claim_evidence 逐条标注"
            "supporting/contradicting/unrelated、independent/same_origin、direct/indirect, "
            "每个 task 的所有 usable 来源都必须被标注, 包括 unrelated; verify 时传入该 task"
            "全部 search_id, record 时只传入最新 verification_id, 不要重复抄写 evidence, Runtime"
            "会自动绑定规范化验证结果。被拒绝的标注需修正后重试; "
            "只有调用 record_research_finding 才表示该问题已经"
            "解决或确实需要用户帮助, 且不得自行提供 confidence —— Harness 会依据已标注证据"
            "和 verification_method 自动计算。"
            "研究计划必须围绕用户真正要判断的问题, 不要用产业规模、政策或高校背景制造"
            "虚假的全面性。区分官方/一手来源、行业报告、招聘样本和二手媒体; 精确数字必须"
            "说明时间与口径, 不同质量来源不能用相同确定性表达。最终先直接回答用户, 再呈现"
            "关键发现、实际意义、编号证据、置信度和必要限制; 不要复述内部计划或堆砌数字。"
            "范围确认由 Harness 的 Node review 负责; 生成范围草案后直接 complete_node, 不得"
            "再调用 request_user_input 要求用户确认同一份草案。"
            "深度研究的关键发现、引用和证据图谱由 Harness 从 record_research_finding 自动"
            "组装; 最终 complete_node 只写 direct_answer 和总体 limitations, 不得重新复制"
            "Finding 或自行生成图谱。"
            "最终通过 complete_node 返回当前节点 Schema 要求的最小结果。"
        ),
        workflows=workflows,
        tools=tools,
        skills=skills,
        permission_profile=PermissionProfile(
            mode="auto",
            preauthorized=[],
            deny=["save_memory"],
            review_required=[],
        ),
    )


__all__ = ["build_agent"]
