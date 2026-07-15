"""Composition root for the Workflow-governed Research Assistant."""

from __future__ import annotations

from pathlib import Path

from modi_harness import ModiAgent, ToolBinding
from modi_harness.skills import SkillLoader
from modi_harness.types import PermissionProfile, Skill
from modi_harness.workflow import parse_workflow_yaml

from .tools import (
    PUBLIC_WEB_RESEARCH_SPEC,
    PUBLIC_WEB_SEARCH_SPEC,
    RECORD_RESEARCH_FINDING_SPEC,
    REJECT_RESEARCH_REQUEST_SPEC,
    public_web_research,
    public_web_search,
    record_research_finding,
    reject_research_request,
)

PACKAGE_DIR = Path(__file__).parent

_TOOL_DEFINITIONS = (
    (PUBLIC_WEB_RESEARCH_SPEC, public_web_research),
    (PUBLIC_WEB_SEARCH_SPEC, public_web_search),
    (RECORD_RESEARCH_FINDING_SPEC, record_research_finding),
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
        for name in ("web-research",)
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
            "research_result 直接回答; 深度研究先确认必要范围, 再按研究问题进行多轮检索和综合。"
            "每个深度研究问题可以进行多次不同查询; 搜索只收集证据, 只有调用"
            "record_research_finding 才表示该问题已经解决或确实需要用户帮助。"
            "只依据 public_web_research 或 public_web_search 返回的 usable 来源陈述事实, "
            "引用真实 URL; 证据缺口由 Harness 暂停并交给用户决定。不得推断主体不存在。"
            "研究计划必须围绕用户真正要判断的问题, 不要用产业规模、政策或高校背景制造"
            "虚假的全面性。区分官方/一手来源、行业报告、招聘样本和二手媒体; 精确数字必须"
            "说明时间与口径, 不同质量来源不能用相同确定性表达。最终先直接回答用户, 再呈现"
            "关键发现、实际意义、编号证据、置信度和必要限制; 不要复述内部计划或堆砌数字。"
            "范围确认由 Harness 的 Node review 负责; 生成范围草案后直接 complete_node, 不得"
            "再调用 request_user_input 要求用户确认同一份草案。"
            "深度研究的关键发现和引用由 Harness 从 record_research_finding 自动组装; 最终"
            "complete_node 只写 direct_answer 和总体 limitations, 不得重新复制 Finding。"
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
