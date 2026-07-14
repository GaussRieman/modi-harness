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
    REJECT_RESEARCH_REQUEST_SPEC,
    public_web_research,
    public_web_search,
    reject_research_request,
)

PACKAGE_DIR = Path(__file__).parent

_TOOL_DEFINITIONS = (
    (PUBLIC_WEB_RESEARCH_SPEC, public_web_research),
    (PUBLIC_WEB_SEARCH_SPEC, public_web_search),
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
            "只依据 public_web_research 或 public_web_search 返回的 usable 来源陈述事实, "
            "引用真实 URL; 证据缺口由 Harness 暂停并交给用户决定。不得推断主体不存在。"
            "不要复述内部计划。最终通过 complete_node 返回"
            "当前节点 Schema 要求的最小结果。"
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
