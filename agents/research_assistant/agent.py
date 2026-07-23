"""Composition root for the Workflow-governed Research Assistant."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from modi_harness import ModiAgent, ToolBinding
from modi_harness.config import Settings
from modi_harness.long_task import ChildTemplateLimits, ChildTemplateRef
from modi_harness.skills import SkillLoader
from modi_harness.types import PermissionProfile, Skill
from modi_harness.workflow import parse_workflow_yaml

from .long_task import (
    build_research_completion_validators,
    build_research_components,
    build_research_schema_registry,
)
from .tools import (
    BUILD_EVIDENCE_GRAPH_SPEC,
    GET_CURRENT_TIME_SPEC,
    INITIALIZE_DEEP_RESEARCH_SPEC,
    PUBLIC_WEB_EXPLORE_SPEC,
    PUBLIC_WEB_RESEARCH_SPEC,
    PUBLIC_WEB_SEARCH_SPEC,
    RECORD_RESEARCH_FINDING_SPEC,
    REJECT_RESEARCH_REQUEST_SPEC,
    VERIFY_CLAIM_EVIDENCE_SPEC,
    build_evidence_graph,
    get_current_time,
    initialize_deep_research,
    public_web_explore,
    public_web_research,
    public_web_search,
    record_research_finding,
    reject_research_request,
    verify_claim_evidence,
)
from .tools.doubao import config_from_tool_settings

PACKAGE_DIR = Path(__file__).parent

_TOOL_DEFINITIONS = (
    (GET_CURRENT_TIME_SPEC, get_current_time),
    (PUBLIC_WEB_RESEARCH_SPEC, public_web_research),
    (PUBLIC_WEB_EXPLORE_SPEC, public_web_explore),
    (PUBLIC_WEB_SEARCH_SPEC, public_web_search),
    (INITIALIZE_DEEP_RESEARCH_SPEC, initialize_deep_research),
    (VERIFY_CLAIM_EVIDENCE_SPEC, verify_claim_evidence),
    (RECORD_RESEARCH_FINDING_SPEC, record_research_finding),
    (BUILD_EVIDENCE_GRAPH_SPEC, build_evidence_graph),
    (REJECT_RESEARCH_REQUEST_SPEC, reject_research_request),
)


def _bind_doubao_config(
    handler: Callable[..., Any],
    config: Any,
) -> Callable[..., Any]:
    @functools.wraps(handler)
    def bound(*args: Any, **kwargs: Any) -> Any:
        return handler(*args, **kwargs, _doubao_config=config)

    return bound


def build_agent() -> ModiAgent:
    """Build the complete trusted Agent definition for discovery and direct use."""

    project_root = PACKAGE_DIR.parent.parent
    settings = Settings(_env_file=project_root / ".env")
    doubao_config = config_from_tool_settings(settings.tools)
    tools = tuple(
        ToolBinding(
            spec=spec,
            handler=(
                _bind_doubao_config(handler, doubao_config)
                if spec["name"]
                in {"public_web_research", "public_web_explore", "public_web_search"}
                else handler
            ),
        )
        for spec, handler in _TOOL_DEFINITIONS
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
    schema_registry = build_research_schema_registry()
    completion_validators = build_research_completion_validators()
    workflows = tuple(
        parse_workflow_yaml(
            path.read_text(encoding="utf-8"),
            source=str(path),
            known_validators={item.id for item in completion_validators},
            agent_tools=tool_ids,
            schema_registry=schema_registry,
        )
        for path in sorted((PACKAGE_DIR / "workflows").glob("*.yaml"))
    )
    return ModiAgent(
        name="research-assistant",
        description="Source-grounded autonomous research and briefing Agent.",
        instruction=(
            "你只处理公开资料研究, 严格执行 Router 选择的 Workflow。简单查询依据已提供的"
            "research_result 直接回答; 深度研究先生成不扩张原意的 Research Brief, 再执行"
            "多条互补探索搜索并建立独立 Coverage Map, 然后从覆盖缺口生成少量高价值 Task"
            "并立即进入 Task Graph, 不要求用户预先确认 Scope。Task 表示尚未"
            "解决的信息缺口, 不是固定报告章节。child 只处理 ContextManifest 中当前 Task, "
            "先复用 research_context 中的探索来源和已提交发现; 已有来源足够时直接引用并"
            "完成, 不得为了形式重复搜索。仍有关键缺口时才进行定向搜索。"
            "每一次公开网络搜索前必须先调用 get_current_time, 并把它刚返回的 time_token"
            "原样传给紧随其后的搜索; 补搜前必须重新取时。使用 query-planning Skill 将实体、"
            "别名和单一研究维度组织成结构化 searches, 不得把多个实体塞进同一条长查询。"
            "只依据 public_web_research 或 public_web_search 返回的 usable 来源陈述事实, "
            "不得从 candidates、search_records 或 fetch_records 中提取事实。引用真实 URL; "
            "证据缺口必须记录为明确 limitation 并继续其余问题。不得推断主体不存在。"
            "深度研究 child 不执行独立 evidence 标注流程; 搜索后直接选择最相关的 usable URL, "
            "由 Runtime 自动绑定摘录和 provenance。verify_claim_evidence 仅保留给旧流程兼容, "
            "除非当前 Workflow 明确提供该能力, 否则不得调用。只有调用"
            "record_research_finding 才表示该问题已经解决或确实存在公开资料缺口。"
            "搜索返回 quality_gaps 时只按 follow_up_searches 补搜一次; official_primary_required "
            "比较必须为每个实体选择官方或一手来源, 未达到时记录 blocked, 不得用一方官网加"
            "另一方内容平台拼成确定结论。"
            "研究计划必须围绕用户真正要判断的问题, 不要用产业规模、政策或高校背景制造"
            "虚假的全面性。区分官方/一手来源、行业报告、招聘样本和二手媒体; 精确数字必须"
            "说明时间与口径, 不同质量来源不能用相同确定性表达。最终先直接回答用户, 再呈现"
            "少量来源摘录和必要限制; 不要复述内部计划、置信度标签或堆砌证据。"
            "child 只有在新问题可能显著改变最终答案时才填写 suggested_work; Planner 会将其"
            "去重并转换为下一波 Task。一般歧义自行采用合理解释继续; 只有同名主体或专业含义"
            "无法消歧且会实质改变方向时才调用 request_user_input。"
            "深度研究的关键发现、引用、provenance 和证据图谱由 Harness 只从 Parent 已接受的"
            "committed_results 自动组装; 综合节点的 complete_node 只写 direct_answer 和总体"
            "limitations, 不得重新复制 Finding、证据、URL 或自行生成图谱。"
            "最终通过 complete_node 返回当前节点 Schema 要求的最小结果。"
        ),
        workflows=workflows,
        completion_validators=completion_validators,
        task_graph_components=build_research_components(),
        child_templates=(
            ChildTemplateRef(
                id="research-dimension",
                agent_name="research-assistant",
                workflow_id="research_dimension",
                limits=ChildTemplateLimits(max_steps=8, timeout_seconds=600),
            ),
        ),
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
