from __future__ import annotations

# ruff: noqa: RUF001
import importlib.util
import inspect
from pathlib import Path

from modi_harness.discovery import discover_agents

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agents" / "modi-webagent"


def _load_runtime():
    spec = importlib.util.spec_from_file_location("modi_webagent_runtime", AGENT_DIR / "runtime.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_webagent_factory_is_discovered_with_police_intake_skill() -> None:
    result = discover_agents(cwd=REPO_ROOT, plugins=[])
    descriptor = result.registry.resolve("webagent")

    assert descriptor.executable_factory is True
    assert descriptor.agent.name == "webagent"
    assert descriptor.agent.interaction_protocol.startup == "agent"
    assert descriptor.agent.task_protocol.mode == "off"
    assert descriptor.agent.permission_profile is not None
    assert set(descriptor.agent.permission_profile["preauthorized"]) >= {
        "run_police_intake",
    }
    assert "transition_stage" in descriptor.agent.permission_profile["deny"]
    assert [skill.name for skill in descriptor.agent.skills] == ["police-intake"]
    assert {tool.spec["name"] for tool in descriptor.agent.tools} == {
        "parse_police_intake",
        "run_police_intake",
    }
    run_tool = next(tool for tool in descriptor.agent.tools if tool.spec["name"] == "run_police_intake")
    assert run_tool.spec["risk_level"] == "L1"
    assert run_tool.spec["side_effect"] is False


def test_parse_police_intake_reads_intro_file() -> None:
    runtime = _load_runtime()

    result = runtime.parse_police_intake(str(AGENT_DIR / "data" / "injection" / "intro.md"))

    assert result["ok"] is True
    assert result["url"] == "http://192.168.24.220:30101/"
    assert result["fields"] == {
        "报警人姓名": "李江",
        "报警人联系电话": "18199987774",
        "处警人员": "赵武，钱柳",
        "警情地址": "诚高大厦6楼",
        "报警内容描述": "我被我的同事周枫打了",
        "警情类别": "行政（治安）类警情",
        "警情类型": "侵犯人身权利",
    }
    assert result["_modi_pending_interaction"]["field"] == "draft_confirmation"
    assert result["_modi_pending_interaction"]["default"] == "go"
    assert result["_modi_pending_interaction"]["draft"]["fields"] == result["fields"]


def test_parse_police_intake_resolves_agent_relative_data_path() -> None:
    runtime = _load_runtime()

    result = runtime.parse_police_intake("data/injection/intro.md")

    assert result["ok"] is True
    assert result["intake_path"].endswith("agents/modi-webagent/data/injection/intro.md")


def test_parse_police_intake_resolves_repo_relative_agent_path() -> None:
    runtime = _load_runtime()

    result = runtime.parse_police_intake("agents/modi-webagent/data/injection/intro.md")

    assert result["ok"] is True
    assert result["intake_path"].endswith("agents/modi-webagent/data/injection/intro.md")


def test_parse_police_intake_reports_missing_fields(tmp_path: Path) -> None:
    runtime = _load_runtime()
    path = tmp_path / "intro.md"
    path.write_text(
        "# 警情录入网址\nhttp://example.test/\n\n# 填入的数据\n报警人姓名：李江\n",
        encoding="utf-8",
    )

    result = runtime.parse_police_intake(str(path))

    assert result["ok"] is False
    assert "报警人联系电话" in result["missing"]
    assert result["error"] == "missing required police intake fields"


def test_parse_police_intake_reads_agent_draft_markdown(tmp_path: Path) -> None:
    runtime = _load_runtime()
    path = tmp_path / "police_intake.md"
    path.write_text(
        "\n".join(
            [
                "# 警情录入信息",
                "",
                "## 警情录入网址",
                "http://192.168.24.220:30101/",
                "",
                "## 报警人姓名",
                "李江",
                "",
                "## 报警人联系电话",
                "18199987774",
                "",
                "## 处警人员",
                "赵武，钱柳",
                "",
                "## 警情地址",
                "诚高大厦6楼",
                "",
                "## 报警内容描述",
                "我被我的同事周枫打了",
                "",
                "## 警情类别",
                "行政治安",
                "",
                "## 警情类型",
                "人身权利",
            ]
        ),
        encoding="utf-8",
    )

    result = runtime.parse_police_intake(str(path))

    assert result["ok"] is True
    assert result["fields"]["警情类别"] == "行政（治安）类警情"
    assert result["fields"]["警情类型"] == "侵犯人身权利"


def test_script_entrypoint_exists() -> None:
    assert (AGENT_DIR / "scripts" / "submit_police_intake.py").is_file()


def test_runtime_exposes_worker_runner_for_uv_script() -> None:
    runtime = _load_runtime()

    assert callable(runtime._run_police_intake_in_process)
    signature = inspect.signature(runtime.run_police_intake)
    assert "fields" in signature.parameters
    assert runtime.RUN_POLICE_INTAKE_SPEC["input_schema"]["properties"]["fields"] == {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }


def test_police_intake_field_overrides_use_aliases() -> None:
    runtime = _load_runtime()

    merged = runtime._apply_police_field_overrides(
        {
            "报警人姓名": "李江",
            "报警人联系电话": "18199987774",
            "处警人员": "赵武，钱柳",
            "警情地址": "诚高大厦6楼",
            "报警内容描述": "我被我的同事周枫打了",
            "警情类别": "行政（治安）类警情",
            "警情类型": "侵犯人身权利",
        },
        {"内容": "我被同事周枫打了,我要报警"},
    )

    assert merged["报警内容描述"] == "我被同事周枫打了,我要报警"


def test_select_option_accepts_timeout_for_dependent_dropdowns() -> None:
    runtime = _load_runtime()

    signature = inspect.signature(runtime.browser_select_option)

    assert "timeout_ms" in signature.parameters
    assert runtime.BROWSER_SELECT_OPTION_SPEC["input_schema"]["properties"]["timeout_ms"] == {
        "type": "integer",
        "minimum": 1,
    }
    source = inspect.getsource(runtime.browser_select_option)
    assert "dispatchEvent" in source


def test_police_intake_uses_short_timeout_for_dependent_dropdown() -> None:
    runtime = _load_runtime()

    source = inspect.getsource(runtime._run_police_intake_in_process)

    assert "DEPENDENT_SELECT_TIMEOUT_MS" in source
    assert "dependent_timeout_ms = min(timeout_ms, DEPENDENT_SELECT_TIMEOUT_MS)" in source
    assert 'timeout_ms": dependent_timeout_ms' in source


def test_trace_steps_record_elapsed_timing_source() -> None:
    runtime = _load_runtime()

    source = inspect.getsource(runtime._append_trace_step)

    assert '"started_at_ms": started_at_ms' in source
    assert '"finished_at_ms": finished_at_ms' in source
    assert '"elapsed_ms": finished_at_ms - started_at_ms' in source


def test_fill_by_placeholder_records_entered_value_in_trace_source() -> None:
    runtime = _load_runtime()

    source = inspect.getsource(runtime.browser_fill_by_placeholder)

    assert '"value": value' in source


def test_agent_prompt_requires_visible_default_intake_path() -> None:
    agent_text = (AGENT_DIR / "agent.md").read_text(encoding="utf-8")

    assert "`input_type` 必须使用 `confirm`" in agent_text
    assert "`default` 必须使用 `agents/modi-webagent/data/injection/intro.md`" in agent_text
    assert "默认路径：agents/modi-webagent/data/injection/intro.md" in agent_text


def test_agent_prompt_requires_draft_confirmation_before_submission() -> None:
    agent_text = (AGENT_DIR / "agent.md").read_text(encoding="utf-8")

    assert "先根据上下文拟合一版合理草稿" in agent_text
    assert "草稿完整且用户已经看到总览后" in agent_text
    assert "`go`、回车" in agent_text
    assert "都表示确认执行" in agent_text
    assert "草稿阶段只做对话收敛，不调用 `run_police_intake`" in agent_text
    assert "不要反复问“是否正确”“是否全部正确”" in agent_text
    assert "应用、流程、数据源、目标网页、草稿字段、下一步输入" in agent_text
    assert "`selected_app: police-intake`" in agent_text
    assert "请选择应用" in agent_text
    assert "不要把 `go` 解释成弱确认" in agent_text
    assert "先只调用 `parse_police_intake`" in agent_text
    assert "等待 `draft_confirmation`" in agent_text
    assert "不要在同一轮调用 `run_police_intake`" in agent_text
    assert "human_context.inputs.police_intake_draft" in agent_text
    assert "`fields` 参数" in agent_text
