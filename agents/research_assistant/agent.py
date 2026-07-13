"""Composition root for the Workflow-governed Research Assistant."""

from __future__ import annotations

from pathlib import Path

from modi_harness import ModiAgent, ToolBinding
from modi_harness.skills import SkillLoader
from modi_harness.types import PermissionProfile, Skill
from modi_harness.workflow import parse_workflow_yaml

from .tools import PUBLIC_WEB_RESEARCH_SPEC, public_web_research
from .validators import RESEARCH_VALIDATORS

PACKAGE_DIR = Path(__file__).parent

_TOOL_DEFINITIONS = ((PUBLIC_WEB_RESEARCH_SPEC, public_web_research),)


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
    validator_ids = {validator.id for validator in RESEARCH_VALIDATORS}
    tool_ids = {binding.spec["name"] for binding in tools}
    workflows = tuple(
        parse_workflow_yaml(
            path.read_text(encoding="utf-8"),
            source=str(path),
            known_validators=validator_ids,
            agent_tools=tool_ids,
        )
        for path in sorted((PACKAGE_DIR / "workflows").glob("*.yaml"))
    )
    return ModiAgent(
        name="research-assistant",
        description="Source-grounded autonomous research and briefing Agent.",
        instruction=(
            "清晰的研究主题直接调用一次 public_web_research, 然后基于其来源记录完成回答; "
            "不要复述计划, 不要把研究拆成内部阶段。只在研究主体本身无法识别时通过 "
            "request_user_input 问一个简短问题。只依据 usable 来源陈述事实, 未命中时必须限定为"
            "本次公开检索未建立可靠匹配, 不得推断主体不存在。最终通过 complete_node 返回。"
        ),
        workflows=workflows,
        completion_validators=RESEARCH_VALIDATORS,
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
