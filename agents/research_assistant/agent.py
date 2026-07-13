"""Composition root for the Workflow-governed Research Assistant."""

from __future__ import annotations

from pathlib import Path

from modi_harness import ModiAgent, ToolBinding
from modi_harness.skills import SkillLoader
from modi_harness.types import PermissionProfile, Skill
from modi_harness.workflow import parse_workflow_yaml

from .tools import (
    FETCH_URL_SPEC,
    GENERATE_RESEARCH_DIGEST_SPEC,
    JUDGE_RESEARCH_DIGEST_SPEC,
    SOURCE_EXTRACT_SPEC,
    WEB_SEARCH_SPEC,
    fetch_url,
    generate_research_digest,
    judge_research_digest,
    source_extract,
    web_search,
)
from .validators import RESEARCH_VALIDATORS

PACKAGE_DIR = Path(__file__).parent

_TOOL_DEFINITIONS = (
    (WEB_SEARCH_SPEC, web_search),
    (FETCH_URL_SPEC, fetch_url),
    (SOURCE_EXTRACT_SPEC, source_extract),
    (GENERATE_RESEARCH_DIGEST_SPEC, generate_research_digest),
    (JUDGE_RESEARCH_DIGEST_SPEC, judge_research_digest),
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
        for name in ("source-evaluation", "briefing-structure")
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
            "只依据可追溯来源完成当前 Workflow 节点目标; 把来源未覆盖的内容明确写入限制, "
            "不得补猜。只在研究问题本身不明确时通过 request_user_input 简短询问一次; "
            "不要复述或要求确认研究计划。已知来源 URL 是可选的, 没有时使用 web_search。"
            "自主规划只限当前节点, 满足完成契约后通过 complete_node 返回。"
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
