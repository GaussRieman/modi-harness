from __future__ import annotations

# ruff: noqa: RUF001
import importlib.util
import inspect
import json
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
        "prepare_zhizheng_capture",
        "browser_begin_flow_capture",
        "browser_record_flow_step",
        "browser_finish_flow_capture",
        "browser_observe",
        "browser_recover_zhizheng_context",
        "browser_recommend_material",
        "browser_click_candidate",
        "browser_click_candidate_and_upload",
        "browser_upload",
        "browser_assert_zhizheng_detail",
        "browser_analyze_zhizheng_state",
    }
    assert "transition_stage" in descriptor.agent.permission_profile["deny"]
    assert "list_workspace_dir" in descriptor.agent.permission_profile["deny"]
    assert [skill.name for skill in descriptor.agent.skills] == ["police-intake", "zhizheng"]
    assert {tool.spec["name"] for tool in descriptor.agent.tools} == {
        "parse_police_intake",
        "run_police_intake",
        "prepare_zhizheng_capture",
        "browser_start",
        "browser_begin_flow_capture",
        "browser_record_flow_step",
        "browser_finish_flow_capture",
        "browser_observe",
        "browser_recover_zhizheng_context",
        "browser_recommend_material",
        "browser_click_candidate",
        "browser_click_candidate_and_upload",
        "browser_click",
        "browser_type",
        "browser_upload",
        "browser_wait_for_text",
        "browser_assert_zhizheng_detail",
        "browser_screenshot",
        "browser_detect_error",
        "browser_analyze_zhizheng_state",
        "browser_close",
    }
    run_tool = next(tool for tool in descriptor.agent.tools if tool.spec["name"] == "run_police_intake")
    assert run_tool.spec["risk_level"] == "L1"
    assert run_tool.spec["side_effect"] is False
    observe_tool = next(tool for tool in descriptor.agent.tools if tool.spec["name"] == "browser_observe")
    assert observe_tool.spec["risk_level"] == "L0"
    assert observe_tool.spec["side_effect"] is False
    start_tool = next(tool for tool in descriptor.agent.tools if tool.spec["name"] == "browser_start")
    assert start_tool.spec["risk_level"] == "L0"
    assert start_tool.spec["side_effect"] is False


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


def test_prepare_zhizheng_capture_reads_oudataren_materials() -> None:
    runtime = _load_runtime()

    result = runtime.prepare_zhizheng_capture()

    assert result["ok"] is True
    assert result["caseType"] == "殴打他人"
    assert result["url"] == "http://192.168.7.171:5173/home"
    assert result["dataDir"].endswith("agents/modi-webagent/data/oudataren/files")
    assert "/runs/zhizheng-" in result["evidence_dir"].replace("\\", "/")
    assert result["instructions"] == ["操作区域一定是在页面的靠下方，进度只是显示，不操作。"]
    material_names = {material["name"] for material in result["materials"]}
    assert {"现场.jpg", "作案工具烟灰缸.jpg", "证人证言1.mp4"} <= material_names
    assert all(Path(material["path"]).is_absolute() for material in result["materials"])
    assert all(Path(material["path"]).is_file() for material in result["materials"])


def test_zhizheng_flow_capture_writes_flow_json(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

    session_id = "capture-test"
    materials = tmp_path / "files"
    materials.mkdir()
    (materials / "现场.jpg").write_text("fake", encoding="utf-8")
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"started_at_ms": 123},
    }

    try:
        begun = runtime.browser_begin_flow_capture(
            session_id=session_id,
            caseType="殴打他人",
            task="完成殴打他人案取证",
            dataDir=str(materials),
        )
        runtime._SESSIONS[session_id]["flow"]["pendingActions"] = [
            {
                "action": "click",
                "selector": {"text": "J202606250016"},
                "result": {"ok": True},
            }
        ]
        recorded = runtime.browser_record_flow_step(
            session_id=session_id,
            goal="进入案件",
            before_state="首页显示待办案件",
            proposal={"action": "click", "selector": {"text": "J202606250016"}},
            confirmation="go",
            action={"tool": "browser_click", "args": {"text": "J202606250016"}},
            after_state="进入案件详情页",
        )
        finished = runtime.browser_finish_flow_capture(
            session_id=session_id,
            final_state="流程已完成",
            finalText="流程已完结",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    flow_path = Path(finished["flow_path"])
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    assert begun["ok"] is True
    assert recorded["step"]["beforeState"] == "首页显示待办案件"
    assert flow["caseType"] == "殴打他人"
    assert flow["finalText"] == "流程已完结"
    assert flow["finalState"] == "流程已完成"
    assert flow["materials"][0]["name"] == "现场.jpg"
    assert flow["steps"][0]["confirmation"] == "go"


def test_finish_flow_rejects_incomplete_zhizheng_progress(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/chat?jqbh=J202606250016"

        def evaluate(self, script: object) -> object:
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": self.url,
                    "path": "/chat?jqbh=J202606250016",
                    "visible_text": "当前进度 4/13 伤情表阶段 交给受害人签字",
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

    session_id = "finish-incomplete-progress-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "flow": {"steps": [], "status": "capturing"},
    }

    try:
        result = runtime.browser_finish_flow_capture(
            session_id=session_id,
            final_state="当前进度 4/13（伤情表阶段）",
            finalText="签字后剩余步骤需现场操作",
            status="success",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is False
    assert result["progress"] == {"current": 4, "total": 13}
    assert "cannot finish Zhizheng flow at progress 4/13" in result["error"]
    assert Path(result["flow_path"]).is_file()


def test_finish_flow_rejects_arbitrary_incomplete_progress_total(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/chat?jqbh=J202606250016"

        def evaluate(self, script: object) -> object:
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": self.url,
                    "path": "/chat?jqbh=J202606250016",
                    "visible_text": "当前进度 2/7 自定义页面任务",
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

    session_id = "finish-arbitrary-progress-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "flow": {"steps": [], "status": "capturing"},
    }

    try:
        result = runtime.browser_finish_flow_capture(
            session_id=session_id,
            final_state="当前进度 2/7",
            status="success",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is False
    assert result["progress"] == {"current": 2, "total": 7}


def test_completed_zhizheng_page_model_treats_full_flow_as_layout_and_final_task() -> None:
    runtime = _load_runtime()
    stage_elements = [
        {"index": index, "scope": "page", "tag": "div", "role": "", "text": text}
        for index, text in enumerate(
            [
                "总览",
                "现场",
                "报案人",
                "受害人",
                "伤势",
                "伤情表",
                "工具",
                "案由",
                "嫌疑人",
                "证人",
                "调解",
                "医检单",
                "回执",
                "入区",
            ]
        )
    ]
    elements = [
        *stage_elements,
        {
            "index": 100,
            "candidate_id": "obs-1-100",
            "observe_id": 1,
            "scope": "latest_assistant_message",
            "tag": "button",
            "role": "button",
            "text": "确认返回警情列表",
        },
    ]
    page_state = {
        "url": "http://example.test/chat?jqbh=J202606250016",
        "path": "/chat?jqbh=J202606250016",
        "visible_text": (
            "智证Agent 进度（13/13） 总览 现场 报案人 受害人 伤势 伤情表 "
            "工具 案由 嫌疑人 证人 调解 医检单 回执 入区 "
            "已根据您提供的信息生成入区预约，请及时前往。"
            "本次处警固证流程已完成。确认返回警情列表"
        ),
        "visible_lines": [
            "智证Agent",
            "进度（13/13）",
            "总览",
            "现场",
            "报案人",
            "确认返回警情列表",
        ],
    }

    model = runtime.zhizheng_model.build_page_model(page_state, elements, "J202606250016")

    assert model["progress"] == {"current": 13, "total": 13}
    assert model["is_finish_allowed"] is True
    assert "finish_flow" not in model["forbidden_actions"]
    assert "现场" in model["layout"]["stage_navigation"]
    assert "入区" in model["layout"]["stage_navigation"]
    assert [action["text"] for action in model["business_actions"]] == ["确认返回警情列表"]
    assert model["tasks"]["available_action_labels"] == ["确认返回警情列表"]


def test_zhizheng_homepage_requires_exact_home_route() -> None:
    runtime = _load_runtime()
    elements = [
        {
            "index": 2,
            "candidate_id": "obs-1-2",
            "observe_id": 1,
            "scope": "page",
            "tag": "div",
            "role": "",
            "text": "J202606250015 待出警 诚高大厦6楼 我被我的同事周枫打了",
        }
    ]
    exact_home = {
        "url": "http://192.168.7.171:5173/home",
        "path": "/home",
        "visible_text": "待办警情列表 J202606250015 待出警 诚高大厦6楼",
    }
    home_with_query = {
        "url": "http://192.168.7.171:5173/home?jqbh=J202606250015",
        "path": "/home?jqbh=J202606250015",
        "visible_text": "待办警情列表 J202606250015 待出警 诚高大厦6楼",
    }

    exact_model = runtime.zhizheng_model.build_page_model(exact_home, elements, "J202606250015")
    query_model = runtime.zhizheng_model.build_page_model(home_with_query, elements, "J202606250015")

    assert exact_model["route_state"] == "homepage_or_case_list"
    assert query_model["route_state"] == "unknown"
    assert query_model["business_actions"] == []


def test_browser_click_record_id_targets_record_card(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        def __init__(self) -> None:
            self.scripts: list[object] = []

        def evaluate(self, script: object, arg: object | None = None) -> object:
            self.scripts.append(script)
            if script == runtime.CLICK_RECORD_CARD_BY_TEXT_JS:
                assert arg == "J202606250016"
                return {"ok": True, "text": "报案人 李江 J202606250016", "role": ""}
            if script == "() => window.location.href":
                return "http://example.test/chat?jqbh=J202606250016"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    session_id = "record-card-click-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
    }

    try:
        result = runtime.browser_click(
            session_id=session_id,
            text="J202606250016",
            node="点击警情 J202606250016 卡片进入详情",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["selector"]["record_card_text"] == "J202606250016"
    assert session is not None
    assert session["actions"][0]["selector"]["record_card_text"] == "J202606250016"
    assert session["actions"][0]["result"]["url"].endswith("/chat?jqbh=J202606250016")


def test_browser_observe_assigns_candidate_ids(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

        def evaluate(self, script: object) -> object:
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [
                    {"index": 0, "tag": "div", "role": "", "text": "进度 (0/13)"},
                    {"index": 1, "tag": "button", "role": "button", "text": "确认出警"},
                ]
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": "http://example.test/home",
                    "path": "/home",
                    "visible_text": "进度 (0/13) 确认出警",
                    "visible_lines": ["进度 (0/13)", "确认出警"],
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def title(self) -> str:
            return "智证Agent"

    session_id = "observe-candidate-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
    }

    try:
        result = runtime.browser_observe(session_id)
        session = runtime._SESSIONS[session_id]
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["observe_id"] == 1
    assert result["elements"][0]["candidate_id"] == "obs-1-0"
    assert result["elements"][1]["candidate_id"] == "obs-1-1"
    assert result["zhizheng_route_state"] == "material_or_capture"
    assert result["zhizheng_page_model"]["schema"] == "zhizheng_page_model.v1"
    assert result["zhizheng_page_model"]["route_state"] == "material_or_capture"
    assert result["zhizheng_page_model"]["layout"]["candidate_counts"]["tag"]["button"] == 1
    assert result["zhizheng_page_model"]["tasks"]["available_action_labels"] == ["确认出警"]
    assert result["zhizheng_page_model"]["business_actions"][0]["text"] == "确认出警"
    assert session["last_candidates"]["obs-1-1"]["text"] == "确认出警"


def test_browser_click_candidate_uses_confirmed_observe_candidate(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_CANDIDATE_JS:
                assert arg == 1
                return {"ok": True, "tag": "button", "role": "button", "text": "确认出警"}
            if script == "() => window.location.href":
                return "http://example.test/home"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    session_id = "click-candidate-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 1,
        "last_candidates": {
            "obs-1-1": {
                "candidate_id": "obs-1-1",
                "observe_id": 1,
                "index": 1,
                "tag": "button",
                "role": "button",
                "text": "确认出警",
            }
        },
    }

    try:
        result = runtime.browser_click_candidate(
            session_id=session_id,
            candidate_id="obs-1-1",
            node="点击确认出警进入取证流程",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["selector"]["candidate_id"] == "obs-1-1"
    assert result["selector"]["index"] == 1
    assert session is not None
    assert session["actions"][0]["action"] == "click_candidate"
    assert session["actions"][0]["selector"]["candidate_id"] == "obs-1-1"


def test_successful_action_is_durable_in_flow_pending_actions(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_CANDIDATE_JS:
                return {"ok": True, "tag": "button", "role": "button", "text": "完成拍摄"}
            if script == "() => window.location.href":
                return "http://example.test/chat?jqbh=J202606250016"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    session_id = "flow-pending-action-test"
    evidence_dir = tmp_path / "run"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(evidence_dir),
        "trace": {"steps": []},
        "actions": [],
        "flow": {"steps": [], "status": "capturing"},
        "last_observe_id": 3,
        "last_candidates": {
            "obs-3-108": {
                "candidate_id": "obs-3-108",
                "observe_id": 3,
                "index": 108,
                "tag": "button",
                "role": "button",
                "text": "完成拍摄",
            }
        },
    }

    try:
        clicked = runtime.browser_click_candidate(
            session_id=session_id,
            candidate_id="obs-3-108",
            node="完成现场拍摄",
        )
        recorded = runtime.browser_record_flow_step(
            session_id=session_id,
            goal="完成现场拍摄",
            before_state="已保存现场环境照片 1 张",
            proposal={"candidate_id": "obs-3-108"},
            confirmation="完成拍摄",
            action={"tool": "browser_click_candidate", "candidate_id": "obs-3-108"},
            after_state="进入下一阶段",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    flow = json.loads((evidence_dir / "flow.json").read_text(encoding="utf-8"))
    assert clicked["ok"] is True
    assert recorded["ok"] is True
    assert flow["pendingActions"][0]["action"] == "click_candidate"
    assert flow["pendingActions"][0]["selector"]["text"] == "完成拍摄"
    assert flow["pendingActions"][0]["flow_step"] == 1
    assert flow["steps"][0]["goal"] == "完成现场拍摄"


def test_record_flow_step_rejects_success_record_after_failed_upload_candidate(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/chat?jqbh=J202606250015"

    session_id = "reject-failed-upload-record-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": [], "record_id": "J202606250015"},
        "actions": [],
        "flow": {
            "steps": [],
            "pendingActions": [
                {
                    "action": "click_candidate",
                    "selector": {"candidate_id": "obs-1-0", "requested_candidate_id": "obs-1-0"},
                    "result": {"ok": True},
                    "flow_step": 1,
                }
            ],
            "strictActionRecording": True,
            "record_id": "J202606250015",
        },
        "last_observe_id": 5,
        "last_candidates": {
            "obs-5-23": {
                "candidate_id": "obs-5-23",
                "observe_id": 5,
                "index": 23,
                "scope": "latest_assistant_message",
                "tag": "button",
                "role": "button",
                "text": "完成拍摄",
            }
        },
        "candidate_history": {
            "obs-2-0": {
                "candidate_id": "obs-2-0",
                "observe_id": 2,
                "index": 0,
                "scope": "latest_assistant_message",
                "tag": "button",
                "role": "button",
                "text": "拍摄现场",
            }
        },
    }

    try:
        upload = runtime.browser_click_candidate_and_upload(
            session_id=session_id,
            candidate_id="obs-2-0",
            file_paths=[str(tmp_path / "现场.jpg")],
        )
        recorded = runtime.browser_record_flow_step(
            session_id=session_id,
            goal="上传现场照片并完成拍摄",
            before_state="页面显示拍摄现场按钮",
            proposal={"candidate_id": "obs-2-0"},
            confirmation="go",
            action={"tool": "browser_click_candidate_and_upload", "candidate_id": "obs-2-0"},
            after_state="现场照片已上传成功",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert upload["ok"] is False
    assert upload["must_not_record_flow_step"] is True
    assert "do not report upload success" in upload["next_step"]
    assert recorded["ok"] is False
    assert "no successful unrecorded browser action matches" in recorded["error"]


def test_browser_click_candidate_remaps_stale_candidate_to_latest_equivalent(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_CANDIDATE_JS:
                assert arg == 20
                return {"ok": True, "tag": "div", "role": "", "text": "J202606250016 报案人 张三"}
            if script == "() => window.location.href":
                return "http://example.test/home"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    session_id = "click-stale-candidate-test"
    stale = {
        "candidate_id": "obs-1-20",
        "observe_id": 1,
        "index": 20,
        "scope": "page",
        "tag": "div",
        "role": "",
        "text": "J202606250016 报案人 张三",
        "aria_label": "",
    }
    latest = dict(stale, candidate_id="obs-3-20", observe_id=3)
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 3,
        "last_candidates": {"obs-3-20": latest},
        "candidate_history": {"obs-1-20": stale, "obs-3-20": latest},
    }

    try:
        result = runtime.browser_click_candidate(
            session_id=session_id,
            candidate_id="obs-1-20",
            node="重新进入警情详情",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["selector"]["candidate_id"] == "obs-3-20"
    assert result["selector"]["requested_candidate_id"] == "obs-1-20"
    assert "remapped" in result["selector"]["resolution"]
    assert session is not None
    assert session["actions"][0]["selector"]["candidate_id"] == "obs-3-20"


def test_browser_click_candidate_rejects_ambiguous_stale_candidate(tmp_path: Path) -> None:
    runtime = _load_runtime()

    session_id = "click-ambiguous-stale-candidate-test"
    stale = {
        "candidate_id": "obs-1-20",
        "observe_id": 1,
        "index": 20,
        "scope": "page",
        "tag": "div",
        "role": "",
        "text": "确认出警",
        "aria_label": "",
    }
    runtime._SESSIONS[session_id] = {
        "page": object(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 3,
        "last_candidates": {
            "obs-3-4": dict(stale, candidate_id="obs-3-4", observe_id=3, index=4),
            "obs-3-9": dict(stale, candidate_id="obs-3-9", observe_id=3, index=9),
        },
        "candidate_history": {"obs-1-20": stale},
    }

    try:
        result = runtime.browser_click_candidate(
            session_id=session_id,
            candidate_id="obs-1-20",
            node="点击确认动作",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is False
    assert "stale" in result["error"]
    assert "matches 2 current candidates" in result["error"]
    assert "ask for confirmation" in result["error"]
    assert result["observe_id"] == 3
    assert [element["candidate_id"] for element in result["elements"]] == ["obs-3-4", "obs-3-9"]
    assert "present these latest elements" in result["next_step"]


def test_browser_click_candidate_returns_latest_candidates_when_stale_missing(tmp_path: Path) -> None:
    runtime = _load_runtime()

    session_id = "click-missing-stale-candidate-test"
    stale = {
        "candidate_id": "obs-1-20",
        "observe_id": 1,
        "index": 20,
        "scope": "page",
        "tag": "div",
        "role": "",
        "text": "J202606250016 报案人 张三",
        "aria_label": "",
    }
    runtime._SESSIONS[session_id] = {
        "page": object(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 3,
        "last_candidates": {
            "obs-3-94": {
                "candidate_id": "obs-3-94",
                "observe_id": 3,
                "index": 94,
                "scope": "latest_assistant_message",
                "tag": "button",
                "role": "button",
                "text": "重新进入",
            }
        },
        "candidate_history": {"obs-1-20": stale},
    }

    try:
        result = runtime.browser_click_candidate(
            session_id=session_id,
            candidate_id="obs-1-20",
            node="重新进入警情详情",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is False
    assert "no equivalent candidate exists" in result["error"]
    assert result["elements"][0]["candidate_id"] == "obs-3-94"
    assert result["elements"][0]["scope"] == "latest_assistant_message"
    assert "ask the human to confirm" in result["next_step"]


def test_browser_click_candidate_and_upload_handles_file_chooser(tmp_path: Path) -> None:
    runtime = _load_runtime()
    upload_file = tmp_path / "现场.jpg"
    upload_file.write_text("fake image", encoding="utf-8")

    class FakeFileChooser:
        def __init__(self) -> None:
            self.files: list[str] = []

        def set_files(self, files: list[str]) -> None:
            self.files = files

    class FakeFileChooserContext:
        def __init__(self, chooser: FakeFileChooser) -> None:
            self.value = chooser

        def __enter__(self) -> FakeFileChooserContext:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

    class FakePage:
        def __init__(self) -> None:
            self.chooser = FakeFileChooser()
            self.expect_file_chooser_called = False

        def expect_file_chooser(self, timeout: int) -> FakeFileChooserContext:
            assert timeout == 5000
            self.expect_file_chooser_called = True
            return FakeFileChooserContext(self.chooser)

        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_CANDIDATE_JS:
                assert self.expect_file_chooser_called is True
                assert arg == 94
                return {"ok": True, "tag": "button", "role": "button", "text": "拍摄现场"}
            if script == "() => window.location.href":
                return "http://example.test/chat?jqbh=J202606250016"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    page = FakePage()
    session_id = "click-candidate-upload-test"
    runtime._SESSIONS[session_id] = {
        "page": page,
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 2,
        "last_candidates": {
            "obs-2-94": {
                "candidate_id": "obs-2-94",
                "observe_id": 2,
                "index": 94,
                "scope": "latest_assistant_message",
                "tag": "button",
                "role": "button",
                "text": "拍摄现场",
            }
        },
    }

    try:
        result = runtime.browser_click_candidate_and_upload(
            session_id=session_id,
            candidate_id="obs-2-94",
            file_paths=[str(upload_file)],
            node="上传现场照片",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert page.chooser.files == [str(upload_file)]
    assert result["selector"]["candidate_id"] == "obs-2-94"
    assert result["selector"]["upload_trigger"] == "filechooser"
    assert session is not None
    assert session["actions"][0]["action"] == "click_candidate_and_upload"
    assert session["actions"][0]["files"] == [str(upload_file)]


def test_browser_click_candidate_and_upload_returns_followup_candidates_when_no_filechooser(
    tmp_path: Path,
) -> None:
    runtime = _load_runtime()
    upload_file = tmp_path / "受害人李江伤情.jpg"
    upload_file.write_text("fake image", encoding="utf-8")

    class FakeFileChooserContext:
        def __enter__(self) -> FakeFileChooserContext:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            raise TimeoutError('Timeout 5000ms exceeded while waiting for event "filechooser"')

    class FakePage:
        url = "http://example.test/chat?jqbh=J202606250016"

        def expect_file_chooser(self, timeout: int) -> FakeFileChooserContext:
            assert timeout == 5000
            return FakeFileChooserContext()

        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_CANDIDATE_JS:
                assert arg == 178
                return {"ok": True, "tag": "button", "role": "button", "text": "拍摄伤情"}
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [
                    {
                        "index": 190,
                        "scope": "latest_assistant_message",
                        "tag": "button",
                        "role": "button",
                        "text": "民警上传伤情照片 1 张",
                    }
                ]
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": "http://example.test/chat?jqbh=J202606250016",
                    "path": "/chat?jqbh=J202606250016",
                    "visible_text": "民警上传伤情照片 1 张",
                    "visible_lines": ["民警上传伤情照片 1 张"],
                }
            if script == "() => window.location.href":
                return "http://example.test/chat?jqbh=J202606250016"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

    session_id = "click-candidate-upload-followup-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 5,
        "last_candidates": {
            "obs-5-178": {
                "candidate_id": "obs-5-178",
                "observe_id": 5,
                "index": 178,
                "scope": "latest_assistant_message",
                "tag": "button",
                "role": "button",
                "text": "拍摄伤情",
            }
        },
    }

    try:
        result = runtime.browser_click_candidate_and_upload(
            session_id=session_id,
            candidate_id="obs-5-178",
            file_paths=[str(upload_file)],
            node="上传伤情照片",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["uploaded"] is False
    assert result["needs_followup_upload"] is True
    assert result["elements"][0]["candidate_id"] == "obs-1-190"
    assert result["elements"][0]["text"] == "民警上传伤情照片 1 张"
    assert "do not retry the stale candidate" in result["next_step"]
    assert session is not None
    assert session["actions"][0]["action"] == "click_candidate_upload_trigger"


def test_browser_click_candidate_and_upload_uses_single_file_input_after_click(
    tmp_path: Path,
) -> None:
    runtime = _load_runtime()
    upload_file = tmp_path / "受害人李江伤情.jpg"
    upload_file.write_text("fake image", encoding="utf-8")

    class FakeFileChooserContext:
        def __enter__(self) -> FakeFileChooserContext:
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            raise TimeoutError('Timeout 5000ms exceeded while waiting for event "filechooser"')

    class FakeFileInput:
        def __init__(self) -> None:
            self.files: list[str] = []

        def set_input_files(self, files: list[str]) -> None:
            self.files = files

    class FakeLocator:
        def __init__(self, file_input: FakeFileInput) -> None:
            self.file_input = file_input

        def count(self) -> int:
            return 1

        def nth(self, index: int) -> FakeFileInput:
            assert index == 0
            return self.file_input

    class FakePage:
        url = "http://example.test/chat?jqbh=J202606250016"

        def __init__(self) -> None:
            self.file_input = FakeFileInput()

        def expect_file_chooser(self, timeout: int) -> FakeFileChooserContext:
            assert timeout == 5000
            return FakeFileChooserContext()

        def locator(self, selector: str) -> FakeLocator:
            assert selector == "input[type=file]"
            return FakeLocator(self.file_input)

        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_CANDIDATE_JS:
                assert arg == 178
                return {"ok": True, "tag": "button", "role": "button", "text": "拍摄伤情"}
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [
                    {
                        "index": 190,
                        "scope": "latest_assistant_message",
                        "tag": "input",
                        "type": "file",
                        "role": "",
                        "text": "",
                    }
                ]
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": "http://example.test/chat?jqbh=J202606250016",
                    "path": "/chat?jqbh=J202606250016",
                    "visible_text": "民警上传伤情照片 1 张",
                    "visible_lines": ["民警上传伤情照片 1 张"],
                }
            if script == "() => window.location.href":
                return "http://example.test/chat?jqbh=J202606250016"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    page = FakePage()
    session_id = "click-candidate-upload-single-input-test"
    runtime._SESSIONS[session_id] = {
        "page": page,
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 5,
        "last_candidates": {
            "obs-5-178": {
                "candidate_id": "obs-5-178",
                "observe_id": 5,
                "index": 178,
                "scope": "latest_assistant_message",
                "tag": "button",
                "role": "button",
                "text": "拍摄伤情",
            }
        },
    }

    try:
        result = runtime.browser_click_candidate_and_upload(
            session_id=session_id,
            candidate_id="obs-5-178",
            file_paths=[str(upload_file)],
            node="上传伤情照片",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["uploaded"] is True
    assert result["upload_trigger"] == "single_file_input_after_click"
    assert page.file_input.files == [str(upload_file)]
    assert session is not None
    assert session["actions"][0]["action"] == "click_candidate_and_upload"
    assert session["actions"][0]["selector"]["upload_trigger"] == "single_file_input_after_click"


def test_browser_recommend_material_ranks_files_from_requirement(tmp_path: Path) -> None:
    runtime = _load_runtime()
    data_dir = tmp_path / "files"
    data_dir.mkdir()
    for name in ["现场.jpg", "作案工具烟灰缸.jpg", "证人证言1.mp4", "当事人录音.mp3"]:
        (data_dir / name).write_text("fake", encoding="utf-8")

    class FakePage:
        url = "http://example.test/home"

    session_id = "recommend-material-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "flow": {
            "dataDir": str(data_dir),
            "materials": [
                {"name": path.name, "path": str(path)}
                for path in sorted(data_dir.iterdir())
            ],
            "steps": [],
        },
    }

    try:
        result = runtime.browser_recommend_material(
            session_id=session_id,
            requirement="请先拍摄现场环境照片",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["recommended"]["name"] == "现场.jpg"
    assert "现场" in result["recommended"]["matched_keywords"]
    assert result["recommended"]["path"].endswith("现场.jpg")
    assert len(result["candidates"]) >= 3
    assert "ask the human" in result["next_step"]


def test_browser_click_text_resolves_duplicate_button_text(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_BY_TEXT_JS:
                assert arg == "确认出警"
                return {
                    "ok": True,
                    "matched_count": 2,
                    "text": "确认出警",
                    "role": "button",
                }
            if script == "() => window.location.href":
                return "http://example.test/home"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    session_id = "duplicate-text-click-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
    }

    try:
        result = runtime.browser_click(
            session_id=session_id,
            text="确认出警",
            node="点击确认出警进入取证流程",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["selector"]["text"] == "确认出警"
    assert result["selector"]["matched_count"] == 2
    assert result["selector"]["role"] == "button"
    assert session is not None
    assert session["actions"][0]["selector"]["matched_count"] == 2


def test_browser_click_is_disabled_during_zhizheng_flow_capture(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

    session_id = "zhizheng-click-disabled-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "flow": {"steps": []},
    }

    try:
        result = runtime.browser_click(
            session_id=session_id,
            text="确认出警",
            node="点击确认出警进入取证流程",
        )
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is False
    assert "browser_click is disabled during Zhizheng flow capture" in result["error"]
    assert "browser_click_candidate" in result["error"]


def test_assert_zhizheng_detail_accepts_home_path_with_detail_markers(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

        def evaluate(self, script: object) -> object:
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": "http://example.test/home",
                    "path": "/home",
                    "visible_text": (
                        "进度 (0/13) 现场 报案人 受害人 "
                        "警情详情 警情编号 J202606250016 报警时间 2026.6.25 "
                        "警情摘要 我被我的同事周枫打了 确认出警"
                    ),
                }
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [{"index": 0, "tag": "button", "role": "button", "text": "确认出警"}]
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    session_id = "detail-assert-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
    }

    try:
        result = runtime.browser_assert_zhizheng_detail(
            session_id=session_id,
            record_id="J202606250016",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["path"] == "/home"
    assert "警情详情" in result["matched_markers"]
    assert "确认出警" in result["matched_markers"]
    assert result["elements"][0]["candidate_id"] == "obs-1-0"
    assert result["elements"][0]["text"] == "确认出警"
    assert session is not None
    assert session["last_candidates"]["obs-1-0"]["text"] == "确认出警"
    assert session["trace"]["steps"][0]["action"] == "assert_zhizheng_detail"


def test_analyze_zhizheng_state_detects_identity_confirmation(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/chat?jqbh=J202606250016"

        def evaluate(self, script: object) -> object:
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [
                    {"index": 107, "tag": "button", "role": "button", "text": "信息准确"},
                    {"index": 108, "tag": "button", "role": "button", "text": "修改"},
                ]
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": "http://example.test/chat?jqbh=J202606250016",
                    "path": "/chat?jqbh=J202606250016",
                    "visible_text": (
                        "报案人人像照片 1张 Skill 1 · 人像识别结果 "
                        "姓名 李江 性别 男 年龄 28 身份证 349810199801057629 "
                        "地址 杭州市西湖区萍水街丰潭路口 前科 无 "
                        "请确认报案人信息是否准确，如有误可手动修改。 信息准确 修改"
                    ),
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def title(self) -> str:
            return "智证Agent"

    session_id = "identity-confirmation-state-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
    }

    try:
        observed = runtime.browser_observe(session_id)
        analyzed = runtime.browser_analyze_zhizheng_state(session_id)
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert observed["ok"] is True
    assert observed["zhizheng_state"]["state"] == "identity_recognition_confirm"
    assert observed["zhizheng_state"]["is_error"] is False
    assert observed["zhizheng_state"]["recognized_fields"]["姓名"] == "李江"
    assert analyzed["state"]["state"] == "identity_recognition_confirm"
    assert [action["label"] for action in analyzed["state"]["recommended_actions"]] == ["信息准确", "修改"]


def test_recover_zhizheng_context_proposes_record_card_without_clicking(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"
        clicked = False

        def evaluate(self, script: object) -> object:
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [
                    {
                        "index": 23,
                        "scope": "page",
                        "tag": "div",
                        "role": "",
                        "text": "J202606250016 报案人 李江 案由 殴打他人",
                    },
                    {
                        "index": 25,
                        "scope": "page",
                        "tag": "div",
                        "role": "",
                        "text": "警情编号 J202606250016",
                    },
                ]
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": "http://example.test/home",
                    "path": "/home",
                    "visible_text": "警情列表 J202606250016 报案人 李江 案由 殴打他人",
                    "visible_lines": ["警情列表", "J202606250016 报案人 李江 案由 殴打他人"],
                }
            if script == runtime.CLICK_CANDIDATE_JS:
                self.clicked = True
                return {"ok": True}
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def title(self) -> str:
            return "智证Agent"

    page = FakePage()
    session_id = "recover-context-test"
    runtime._SESSIONS[session_id] = {
        "page": page,
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": [], "record_id": "J202606250016"},
        "actions": [],
        "flow": {"steps": [], "record_id": "J202606250016"},
    }

    try:
        result = runtime.browser_recover_zhizheng_context(session_id=session_id)
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["record_id"] == "J202606250016"
    assert result["state"] == "homepage_or_case_list"
    assert result["action_cards"][0]["candidate_id"] == "obs-1-23"
    assert result["action_cards"][0]["goal"] == "恢复进入目标警情 J202606250016"
    assert result["action_cards"][1]["candidate_id"] == "obs-1-25"
    assert "只像警情编号文本" in result["action_cards"][1]["evidence"]
    assert "ask for confirmation" in result["next_step"]
    assert page.clicked is False


def test_recover_zhizheng_context_prefers_current_assistant_action_over_reentry(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

        def evaluate(self, script: object) -> object:
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [
                    {
                        "index": 10,
                        "scope": "page",
                        "tag": "div",
                        "role": "",
                        "text": "J202606250016 报案人 李江 案由 殴打他人",
                    },
                    {
                        "index": 108,
                        "scope": "latest_assistant_message",
                        "tag": "button",
                        "role": "button",
                        "text": "确认信息",
                    },
                ]
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": "http://example.test/home",
                    "path": "/home",
                    "visible_text": (
                        "警情列表 J202606250016 报案人 李江 案由 殴打他人 "
                        "体表原始伤情记录表 详情页确认成功 确认信息"
                    ),
                    "visible_lines": [
                        "警情列表",
                        "J202606250016 报案人 李江 案由 殴打他人",
                        "体表原始伤情记录表",
                        "确认信息",
                    ],
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def title(self) -> str:
            return "智证Agent"

    session_id = "recover-prefers-current-action-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": [], "record_id": "J202606250016"},
        "actions": [],
        "flow": {"steps": [], "record_id": "J202606250016"},
    }

    try:
        result = runtime.browser_recover_zhizheng_context(session_id=session_id)
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["state"] == "detail_or_workflow"
    assert result["zhizheng_page_model"]["route_state"] == "detail_or_workflow"
    assert result["action_cards"][0]["candidate_id"] == "obs-1-108"
    assert result["action_cards"][0]["goal"] == "继续当前最新助手卡片的业务操作"
    assert len(result["action_cards"]) == 1


def test_recover_zhizheng_context_filters_stage_navigation_candidates(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/chat?jqbh=J202606250016"

        def evaluate(self, script: object) -> object:
            if script == runtime.OBSERVE_CANDIDATE_JS:
                return [
                    {
                        "index": 133,
                        "scope": "page",
                        "tag": "textarea",
                        "role": "",
                        "text": "",
                        "placeholder": "向智证Agent提问...",
                    },
                    {
                        "index": 142,
                        "scope": "page",
                        "tag": "div",
                        "role": "",
                        "text": "工具",
                    },
                    {
                        "index": 143,
                        "scope": "page",
                        "tag": "div",
                        "role": "",
                        "text": "案由",
                    },
                    {
                        "index": 150,
                        "scope": "latest_assistant_message",
                        "tag": "button",
                        "role": "button",
                        "text": "继续上传",
                    },
                ]
            if script == runtime.PAGE_STATE_JS:
                return {
                    "url": self.url,
                    "path": "/chat?jqbh=J202606250016",
                    "visible_text": "当前进度 4/13 伤情表 工具 案由 继续上传",
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def title(self) -> str:
            return "智证Agent"

    session_id = "filter-stage-nav-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": [], "record_id": "J202606250016"},
        "actions": [],
        "flow": {"steps": [], "record_id": "J202606250016"},
    }

    try:
        result = runtime.browser_recover_zhizheng_context(session_id=session_id)
    finally:
        runtime._SESSIONS.pop(session_id, None)

    element_texts = [element["text"] for element in result["elements"]]
    card_texts = [card["candidate"]["text"] for card in result["action_cards"]]
    assert "工具" not in element_texts
    assert "案由" not in element_texts
    assert card_texts[0] == "继续上传"
    assert "工具" not in card_texts
    assert "案由" not in card_texts


def test_observe_preserves_recent_detail_recovery_when_transient_home_appears(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

        def __init__(self) -> None:
            self.observe_count = 0

        def evaluate(self, script: object) -> object:
            if script == runtime.OBSERVE_CANDIDATE_JS:
                self.observe_count += 1
                if self.observe_count == 1:
                    return [
                        {
                            "index": 108,
                            "scope": "latest_assistant_message",
                            "tag": "button",
                            "role": "button",
                            "text": "确认信息",
                        }
                    ]
                return [
                    {
                        "index": 20,
                        "scope": "page",
                        "tag": "div",
                        "role": "",
                        "text": "J202606250016 报案人 李江 案由 殴打他人",
                    }
                ]
            if script == runtime.PAGE_STATE_JS:
                if self.observe_count == 1:
                    return {
                        "url": "http://example.test/chat?jqbh=J202606250016",
                        "path": "/chat?jqbh=J202606250016",
                        "visible_text": "体表原始伤情记录表 确认信息",
                    }
                return {
                    "url": "http://example.test/home",
                    "path": "/home",
                    "visible_text": "警情列表 J202606250016 报案人 李江 案由 殴打他人",
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def title(self) -> str:
            return "智证Agent"

    session_id = "preserve-recovery-observe-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": [], "record_id": "J202606250016"},
        "actions": [],
        "flow": {"steps": [], "record_id": "J202606250016"},
    }

    try:
        recovered = runtime.browser_recover_zhizheng_context(session_id=session_id)
        observed = runtime.browser_observe(session_id)
        session = runtime._SESSIONS[session_id]
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert recovered["state"] == "detail_or_workflow"
    assert observed["preserved_from_recovery"] is True
    assert observed["url"].endswith("/chat?jqbh=J202606250016")
    assert observed["elements"][0]["candidate_id"] == "obs-1-108"
    assert session["last_candidates"]["obs-1-108"]["text"] == "确认信息"


def test_recover_preserves_material_context_without_canonical_workflow_button(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

        def __init__(self) -> None:
            self.observe_count = 0

        def evaluate(self, script: object) -> object:
            if script == runtime.OBSERVE_CANDIDATE_JS:
                self.observe_count += 1
                if self.observe_count == 1:
                    return [
                        {
                            "index": 9,
                            "scope": "latest_assistant_message",
                            "tag": "div",
                            "role": "",
                            "text": "民警上传现场照片 1 张",
                        }
                    ]
                return [
                    {
                        "index": 2,
                        "scope": "page",
                        "tag": "div",
                        "role": "",
                        "text": "J202606250015 待出警 诚高大厦6楼 我被我的同事周枫打了",
                    }
                ]
            if script == runtime.PAGE_STATE_JS:
                if self.observe_count == 1:
                    return {
                        "url": "http://example.test/chat?jqbh=J202606250015",
                        "path": "/chat?jqbh=J202606250015",
                        "visible_text": "已到达现场 请先拍摄现场环境照片 民警上传现场照片 1 张",
                    }
                return {
                    "url": "http://example.test/home",
                    "path": "/home",
                    "visible_text": "待办警情列表 J202606250015 待出警 诚高大厦6楼",
                }
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def title(self) -> str:
            return "智证Agent"

    session_id = "preserve-material-context-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": [], "record_id": "J202606250015"},
        "actions": [],
        "flow": {"steps": [], "record_id": "J202606250015"},
    }

    try:
        recovered = runtime.browser_recover_zhizheng_context(session_id=session_id)
        observed = runtime.browser_observe(session_id)
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert recovered["state"] == "detail_or_workflow"
    assert observed["preserved_from_recovery"] is True
    assert observed["url"].endswith("/chat?jqbh=J202606250015")
    assert observed["elements"][0]["candidate_id"] == "obs-1-9"
    assert "民警上传现场照片" in observed["elements"][0]["text"]


def test_click_candidate_rejects_homepage_card_when_detail_guard_exists(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

    session_id = "reject-home-card-with-detail-guard-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": [], "record_id": "J202606250015"},
        "actions": [],
        "flow": {"steps": [], "record_id": "J202606250015"},
        "last_observe_id": 2,
        "last_candidates": {
            "obs-2-2": {
                "candidate_id": "obs-2-2",
                "observe_id": 2,
                "index": 2,
                "scope": "page",
                "tag": "div",
                "role": "",
                "text": "J202606250015 待出警 诚高大厦6楼 我被我的同事周枫打了",
            }
        },
        "candidate_history": {},
        "last_zhizheng_recovery_observe": {
            "saved_at_ms": runtime._now_ms(),
            "record_id": "J202606250015",
            "state": "detail_or_workflow",
            "url": "http://example.test/chat?jqbh=J202606250015",
            "page_state": {
                "url": "http://example.test/chat?jqbh=J202606250015",
                "path": "/chat?jqbh=J202606250015",
                "visible_text": "已到达现场 请先拍摄现场环境照片",
            },
            "zhizheng_state": {},
            "zhizheng_route_state": "detail_or_workflow",
            "zhizheng_page_model": {
                "schema": "zhizheng_page_model.v1",
                "record_id": "J202606250015",
                "route_state": "detail_or_workflow",
                "business_actions": [
                    {
                        "candidate_id": "obs-1-9",
                        "observe_id": 1,
                        "index": 9,
                        "scope": "latest_assistant_message",
                        "tag": "button",
                        "role": "button",
                        "text": "完成拍摄",
                    }
                ],
                "latest_assistant_actions": [],
            },
            "elements": [
                {
                    "candidate_id": "obs-1-9",
                    "observe_id": 1,
                    "index": 9,
                    "scope": "latest_assistant_message",
                    "tag": "button",
                    "role": "button",
                    "text": "完成拍摄",
                }
            ],
            "observe_id": 1,
        },
    }

    try:
        result = runtime.browser_click_candidate(session_id=session_id, candidate_id="obs-2-2")
        session = runtime._SESSIONS[session_id]
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is False
    assert "homepage record card" in result["error"]
    assert result["url"].endswith("/chat?jqbh=J202606250015")
    assert result["elements"][0]["candidate_id"] == "obs-1-9"
    assert session["last_candidates"]["obs-1-9"]["text"] == "完成拍摄"


def test_click_candidate_relocates_stale_workflow_action_by_text(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        def evaluate(self, script: object, arg: object | None = None) -> object:
            if script == runtime.CLICK_CANDIDATE_BY_TEXT_JS:
                assert arg == {"text": "确认信息", "preferLatestAssistant": True}
                return {
                    "ok": True,
                    "matched_count": 1,
                    "scope": "latest_assistant_message",
                    "tag": "button",
                    "role": "button",
                    "text": "确认信息",
                }
            if script == "() => window.location.href":
                return "http://example.test/chat?jqbh=J202606250016"
            raise AssertionError(f"unexpected evaluate script: {script!r}")

        def wait_for_timeout(self, _timeout: int) -> None:
            return None

    stale = {
        "candidate_id": "obs-1-108",
        "observe_id": 1,
        "index": 108,
        "scope": "latest_assistant_message",
        "tag": "button",
        "role": "button",
        "text": "确认信息",
        "aria_label": "",
    }
    session_id = "stale-workflow-action-text-click-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "last_observe_id": 2,
        "last_candidates": {
            "obs-2-20": {
                "candidate_id": "obs-2-20",
                "observe_id": 2,
                "index": 20,
                "scope": "page",
                "tag": "div",
                "role": "",
                "text": "J202606250016 报案人 李江 案由 殴打他人",
                "aria_label": "",
            }
        },
        "candidate_history": {"obs-1-108": stale},
    }

    try:
        result = runtime.browser_click_candidate(
            session_id=session_id,
            candidate_id="obs-1-108",
            node="确认体表原始伤情记录表",
        )
    finally:
        session = runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["selector"]["candidate_id"] == "obs-1-108"
    assert result["selector"]["stale_text_fallback"] is True
    assert result["selector"]["clicked_text"] == "确认信息"
    assert result["selector"]["clicked_scope"] == "latest_assistant_message"
    assert session is not None
    assert session["actions"][0]["selector"]["clicked_text"] == "确认信息"


def test_record_flow_step_remembers_record_id_for_recovery(tmp_path: Path) -> None:
    runtime = _load_runtime()

    class FakePage:
        url = "http://example.test/home"

    session_id = "record-id-memory-test"
    runtime._SESSIONS[session_id] = {
        "page": FakePage(),
        "evidence_dir": str(tmp_path / "run"),
        "trace": {"steps": []},
        "actions": [],
        "flow": {"steps": []},
    }

    try:
        result = runtime.browser_record_flow_step(
            session_id=session_id,
            goal="进入 J202606250016 警情",
            before_state="首页",
            proposal={"candidate_id": "obs-1-20"},
            confirmation="go",
            action={"tool": "browser_click_candidate", "candidate_id": "obs-1-20"},
            after_state="进入 J202606250016 详情",
        )
        session = runtime._SESSIONS[session_id]
    finally:
        runtime._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert session["zhizheng_record_id"] == "J202606250016"
    assert session["flow"]["record_id"] == "J202606250016"


def test_script_entrypoint_exists() -> None:
    assert (AGENT_DIR / "scripts" / "submit_police_intake.py").is_file()


def test_playwright_import_falls_back_to_repo_venv_site_packages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _load_runtime()
    package_dir = tmp_path / "agents" / "modi-webagent"
    site_packages = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    package_dir.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    original_import_module = runtime.importlib.import_module

    def fake_import_module(name: str):
        if name != "playwright.sync_api":
            return original_import_module(name)
        if str(site_packages) not in runtime.sys.path:
            raise ImportError("no playwright in current interpreter")
        return type(
            "FakeSyncApi",
            (),
            {"sync_playwright": staticmethod(lambda: "fallback-playwright")},
        )

    monkeypatch.setattr(runtime, "PACKAGE_DIR", package_dir)
    monkeypatch.setattr(
        runtime.sys,
        "path",
        [path for path in runtime.sys.path if path != str(site_packages)],
    )
    monkeypatch.setattr(runtime.importlib, "import_module", fake_import_module)

    sync_playwright = runtime._import_sync_playwright()

    assert sync_playwright() == "fallback-playwright"
    assert str(site_packages) in runtime.sys.path


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


def test_zhizheng_observe_includes_non_semantic_divs() -> None:
    runtime = _load_runtime()

    source = runtime.OBSERVE_CANDIDATE_JS

    assert '"div"' in source
    assert 'style.cursor === "pointer"' in source
    assert "text.length <= 300" in source
    assert "element.children.length <= 8" in source
    assert "window.location.href" in runtime.PAGE_STATE_JS
    assert "visible_text" in runtime.PAGE_STATE_JS


def test_zhizheng_record_card_click_rules_are_hardened() -> None:
    runtime = _load_runtime()
    agent_text = (AGENT_DIR / "agent.md").read_text(encoding="utf-8")
    skill_text = (AGENT_DIR / "skills" / "zhizheng" / "SKILL.md").read_text(encoding="utf-8")

    assert "CLICK_BY_TEXT_JS" in inspect.getsource(runtime.browser_click)
    assert "CLICK_RECORD_CARD_BY_TEXT_JS" in inspect.getsource(runtime.browser_click)
    assert "record_card_text" in inspect.getsource(runtime.browser_click)
    assert "智证页面解析、动作卡片、上传、恢复、路由、结束规则都以该 skill 为准" in agent_text
    assert "每一步都按 skill 的模型驱动循环推进" in agent_text
    assert "browser_click_candidate(candidate_id=...)" in skill_text
    assert "页面语义模型" in skill_text
    assert "人工确认动作卡片后，只执行绑定的 `candidate_id`" in skill_text
    assert "ZHIZHENG_DETAIL_MARKERS" in inspect.getsource(runtime.browser_assert_zhizheng_detail)
    assert "禁止把警情编号文字本身作为点击目标" in skill_text
    assert "禁止仅因出现“报案人”“受害人”“嫌疑人”等首页卡片或进度条也包含的文字就判断跳转成功" in skill_text
    assert "禁止硬编码某个固定按钮序列" in skill_text
    assert "不要只凭 URL、path、可见文本片段或旧候选判断当前状态" in skill_text
    assert "禁止根据历史卡片、旧截图、旧 URL、旧候选继续执行" in skill_text
    assert "首页路由必须精确等于" in skill_text
    assert "`/home?...`、`/home#...`、`/home/...` 都不是首页路由" in skill_text
    assert "只有精确 `/home` 才能作为首页路由信号" in skill_text
    assert "调用 `browser_recover_zhizheng_context(record_id=...)`" in skill_text
    assert "不要先 `browser_click_candidate` 再 `browser_upload`" in skill_text
    assert "`browser_recommend_material(requirement=...)`" in skill_text
    assert "`browser_click_candidate_and_upload`" in skill_text
    assert "禁止编造页面没有出现的动作" in skill_text
    assert "手动录入" in skill_text
    assert "重新上传" in skill_text
    assert "`needs_followup_upload=True`" in skill_text
    assert "两段式上传入口" in skill_text
    assert "不要重试旧 `candidate_id`" in skill_text


def test_zhizheng_capture_reads_and_enforces_instruction_doc() -> None:
    runtime = _load_runtime()
    agent_text = (AGENT_DIR / "agent.md").read_text(encoding="utf-8")
    skill_text = (AGENT_DIR / "skills" / "zhizheng" / "SKILL.md").read_text(encoding="utf-8")

    assert "说明.md" in inspect.getsource(runtime._zhizheng_instructions)
    assert "智证APP的一些操作说明" in inspect.getsource(runtime._zhizheng_instructions)
    assert "prepare_zhizheng_capture.instructions" in agent_text
    assert "prepare_zhizheng_capture.instructions" in skill_text
    assert "布局和交互启发" in skill_text
    assert "不是固定分支脚本" in skill_text
    assert "智证的操作区域一定在页面靠下方，进度只是显示，不操作" in skill_text
    assert "位于页面靠下方的操作候选" in skill_text
    assert "进度标签" in skill_text


def test_zhizheng_observe_prefers_latest_assistant_message_scope() -> None:
    runtime = _load_runtime()
    agent_text = (AGENT_DIR / "agent.md").read_text(encoding="utf-8")
    skill_text = (AGENT_DIR / "skills" / "zhizheng" / "SKILL.md").read_text(encoding="utf-8")

    assert ".chat-body .message-wrap.assistant" in runtime.OBSERVE_CANDIDATE_JS
    assert ".chat-body .message-wrap.assistant" in runtime.CLICK_CANDIDATE_JS
    assert "latest_assistant_message" in runtime.OBSERVE_CANDIDATE_JS
    assert "latest_assistant_message" in runtime.CLICK_CANDIDATE_JS
    assert "`zhizheng` skill" in agent_text
    assert "最新助手卡片通常是当前业务操作区域" in skill_text
    assert "`scope=latest_assistant_message`" in skill_text


def test_browser_observe_returns_page_state_source() -> None:
    runtime = _load_runtime()

    source = inspect.getsource(runtime.browser_observe)

    assert "page.evaluate(PAGE_STATE_JS)" in source
    assert '"page_state": page_state' in source
    assert '"url": page_state.get("url") or page.url' in source


def test_browser_close_writes_actions_json_source() -> None:
    runtime = _load_runtime()

    source = inspect.getsource(runtime.browser_close)

    assert "_write_actions(session)" in source
    assert "_write_flow(session)" in source
    assert '"actions_path": str(actions_path)' in source
    assert '"flow_path": str(flow_path)' in source


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
    assert "自己维护更新后的完整草稿" in agent_text
    assert "`fields` 参数" in agent_text
    assert "应用说明必须写在 `request_user_input.prompt` 里" in agent_text


def test_agent_prompt_describes_zhizheng_model_driven_loop() -> None:
    agent_text = (AGENT_DIR / "agent.md").read_text(encoding="utf-8")
    skill_text = (AGENT_DIR / "skills" / "zhizheng" / "SKILL.md").read_text(encoding="utf-8")

    assert "`selected_app: zhizheng`" in agent_text
    assert "默认材料目录：agents/modi-webagent/data/oudataren/files" in agent_text
    assert "先调用 `prepare_zhizheng_capture`" in agent_text
    assert "prepare_zhizheng_capture.instructions" in agent_text
    assert "`evidence_dir` 必须使用 `prepare_zhizheng_capture` 返回值" in agent_text
    assert "请求人工确认" in agent_text
    assert "`browser_record_flow_step`" in agent_text
    assert "`browser_finish_flow_capture`" in agent_text
    assert "非语义 `div`" in skill_text
    assert "禁止把警情编号文字本身作为点击目标" in skill_text
    assert "禁止仅因出现“报案人”“受害人”“嫌疑人”等首页卡片或进度条也包含的文字就判断跳转成功" in skill_text
    assert "智证探索没有“跑完整流程”的大工具" in agent_text
    assert "`flow.json`" in agent_text
    assert "`actions.json`" in agent_text
