"""Offline tests for the research_assistant Memory demo."""

from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

_RUN_PATH = Path(__file__).resolve().parents[2] / "examples" / "research_assistant" / "run.py"


def _load_run_module():
    spec = importlib.util.spec_from_file_location("research_assistant_run", _RUN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ScriptModel(BaseChatModel):
    script: list[Any] = Field(default_factory=list)
    cursor: dict[str, int] = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self) -> str:
        return "script"


def _native_task_script(*middle: AIMessage, final: AIMessage) -> list[AIMessage]:
    tasks = [
        {"id": "scope", "title": "确定可用研究资料与回答边界"},
        {"id": "compare", "title": "形成证据支持的比较结论"},
        {"id": "answer", "title": "给出问题答案与来源限制"},
    ]
    return [
        AIMessage(content="", tool_calls=[{
            "name": "create_task_plan", "args": {"tasks": tasks}, "id": "plan",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "start_task",
            "args": {"task_id": "scope", "current_action": "核对资料覆盖范围"},
            "id": "start",
        }]),
        *middle,
        AIMessage(content="", tool_calls=[{
            "name": "complete_task",
            "args": {
                "task_id": "scope", "summary": "产出：确定比较范围；证据：demo；限制：仅覆盖给定来源",
                "next_task_id": "compare", "current_action": "依据范围比较证据",
            },
            "id": "done-collect",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "complete_task",
            "args": {
                "task_id": "compare", "summary": "产出：形成比较结论；证据：demo；限制：缺少外部基准",
                "next_task_id": "answer", "current_action": "形成有边界的最终答案",
            },
            "id": "done-analyse",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "complete_task",
            "args": {"task_id": "answer", "summary": "产出：回答研究问题；证据：demo；限制：结论限于给定来源"},
            "id": "done-write",
        }]),
        final,
    ]


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = "text/html; charset=utf-8",
        url: str = "https://example.com/final",
    ) -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._body
        return self._body[:size]

    def geturl(self) -> str:
        return self._url


class _AsyncQuestionModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    async def ainvoke(self, messages):
        self.prompts.append(messages[0]["content"])
        return SimpleNamespace(content=self.responses[len(self.prompts) - 1])


def test_research_assistant_memory_demo_batches_recall_and_write(tmp_path: Path) -> None:
    run = _load_run_module()
    work = AIMessage(content="", tool_calls=[
        {
            "name": "recall_memory",
            "args": {
                "query": "研究",
                "scopes": ["user", "workspace", "thread", "agent"],
                "limit": 5,
            },
            "id": "tc-recall",
        },
        {
            "name": "propose_memory",
            "args": {
                "id": "ra_learned_transformer_comparison",
                "scope": "thread",
                "type": "feedback",
                "name": "transformer-comparison-pattern",
                "description": "Reusable comparison pattern from demo run.",
                "body": "Transformer/RNN 对比要覆盖并行性、长程依赖、数据规模和延迟。",
                "tags": ["research", "model-comparison"],
                "source_kind": "model",
            },
            "id": "tc-propose",
        },
    ])
    final = AIMessage(
            content="",
            tool_calls=[{
                "name": "submit_output",
                "args": {
                    "research_question": "比较 Transformer 和 RNN",
                    "executive_summary": "Transformer 更适合并行训练和长程依赖建模。",
                    "task_results": [
                        {"task": "确定可用研究资料与回答边界", "result": "已确定比较范围。", "evidence": ["demo memory"], "limitations": ["仅覆盖给定来源"]},
                        {"task": "形成证据支持的比较结论", "result": "Transformer 的并行训练能力更强。", "evidence": ["demo memory"], "limitations": ["缺少外部基准"]},
                        {"task": "给出问题答案与来源限制", "result": "已形成有边界的比较答案。", "evidence": ["demo memory"], "limitations": ["结论限于给定来源"]},
                    ],
                    "recommendations": [],
                    "source_limitations": ["仅使用示例资料"],
                },
                "id": "tc-submit",
            }],
    )
    script = _ScriptModel(script=_native_task_script(work, final=final))
    session = run.build_session(
        chat_model=script,
        memory_root=tmp_path / "mem",
        workspace_root=tmp_path / ".modi" / "workspace" / "research_assistant",
    )
    seeded = run.seed_example_memory(session)
    assert "ra_feedback_citations" in seeded
    assert "ra_project_compare_models" in seeded

    response = session.run_task(
        agent="research-assistant",
        input={
            "goal": "Produce a cited briefing.",
            "messages": [{"role": "user", "content": "比较 Transformer 和 RNN"}],
            "tags": ["research", "model-comparison"],
            "reference_keys": ["memory-benchmark-note"],
        },
        thread_id="research-memory-test",
    )
    assert response["status"] == "completed"
    # V0.6.e + structured submit: recall_memory and propose_memory are batched
    # in one turn; final delivery is a submit_output tool call, not raw JSON text.
    assert script.cursor["i"] == 7
    assert response["output"]["research_question"] == "比较 Transformer 和 RNN"
    workspace_records = session.list_memory(scopes=["workspace"], tags=["model-comparison"])
    assert any(r["id"] == "ra_project_compare_models" for r in workspace_records)
    assert (
        tmp_path / "mem" / "workspace" / "research_assistant"
        / "ra_project_compare_models.md"
    ).exists()

    learned_path = (
        tmp_path / "mem" / "thread" / "research-memory-test"
        / "ra_learned_transformer_comparison.md"
    )
    assert learned_path.exists()
    assert not (tmp_path / "mem" / "project").exists()
    assert not (tmp_path / "mem" / "conversation").exists()

    event_types = [event["event_type"] for event in session.get_trace("research-memory-test")]
    assert "memory_recall_candidates" in event_types
    assert "memory_admission" in event_types
    assert "memory_selection" in event_types
    assert "memory_write_proposed" in event_types
    assert "memory_write" in event_types
    recall_sources = [
        event["payload"].get("source")
        for event in session.get_trace("research-memory-test")
        if event["event_type"] == "memory_recall_candidates"
    ]
    assert "harness_memory" in recall_sources
    assert "agent_recall_memory" in recall_sources


def test_research_assistant_contract_synthesizes_submit_output_schema() -> None:
    run = _load_run_module()
    agent = run.build_research_agent()

    assert agent.output_contract is not None
    assert agent.output_contract["schema"]["required"] == [
        "research_question",
        "executive_summary",
        "task_results",
        "recommendations",
        "source_limitations",
    ]
    assert agent.output_contract["required_fields"] == [
        "research_question",
        "executive_summary",
        "task_results",
        "recommendations",
        "source_limitations",
    ]
    assert set(agent.output_contract["schema"]["properties"]) == {
        "research_question",
        "executive_summary",
        "task_results",
        "recommendations",
        "source_limitations",
    }
    assert agent.output_contract["schema"]["additionalProperties"] is False
    assert "submit_output" in agent.metadata["_frontmatter_tools"]
    assert {binding.spec["name"] for binding in agent.tools} == {
        "fetch_url",
        "source_extract",
    }
    assert agent.task_protocol.mode == "required"
    assert agent.task_protocol.review == "never"
    assert agent.interaction_protocol.startup == "agent"


def test_research_assistant_submits_task_results_artifact(tmp_path: Path) -> None:
    run = _load_run_module()
    final = AIMessage(
        content="",
        tool_calls=[{
            "name": "submit_output",
            "args": {
                "research_question": "DeepSeek API 怎么收费？",
                "executive_summary": "缓存命中时输入价格更低。",
                "task_results": [
                    {"task": "确定可用研究资料与回答边界", "result": "定价页可支持价格比较。", "evidence": ["DeepSeek 官方定价页"], "limitations": []},
                    {"task": "形成证据支持的比较结论", "result": "缓存命中价低于未命中价。", "evidence": ["DeepSeek 官方定价页"], "limitations": []},
                    {"task": "给出问题答案与来源限制", "result": "已回答缓存与常规定价差异。", "evidence": ["DeepSeek 官方定价页"], "limitations": ["未覆盖竞品"]},
                ],
                "recommendations": ["重复前缀较多时优先利用缓存。"],
                "source_limitations": ["仅覆盖 DeepSeek 官方定价页。"],
            },
            "id": "submit-minimal",
        }],
    )
    model = _ScriptModel(script=_native_task_script(final=final))
    session = run.build_session(
        chat_model=model,
        memory_root=tmp_path / "mem",
        workspace_root=tmp_path / "workspace",
    )

    response = session.run_task(
        agent="research-assistant",
        input={"goal": "submit minimal briefing"},
        thread_id="minimal-output-fields",
    )
    assert response["status"] == "completed"
    assert set(response["output"]) == {
        "research_question",
        "executive_summary",
        "task_results",
        "recommendations",
        "source_limitations",
    }
    assert len(response["output"]["task_results"]) == 3


def test_source_extract_returns_compact_evidence_card() -> None:
    run = _load_run_module()
    content = (
        "Transformers use self-attention to model dependencies between sequence tokens. "
        "This architecture supports parallel training better than recurrent sequence models. "
        "Recurrent neural networks process tokens sequentially and can be useful for streaming."
    )
    out = run.source_extract(
        url="https://example.com/research/topic",
        content=content,
        content_type="text/html",
    )
    card = out["evidence_card"]

    assert card["citation_key"] == "example-com-research-topic"
    assert card["source_url"] == "https://example.com/research/topic"
    assert card["content_type"] == "text/html"
    assert card["facts"]
    assert "Transformers use self-attention" in card["facts"][0]
    assert card["source_tokens_estimate"] > 0
    assert card["card_tokens_estimate"] > 0


def test_fetch_url_returns_clean_content_and_title(monkeypatch) -> None:
    run = _load_run_module()
    html = b"""
    <html>
      <head><title> Research Title \n Example </title></head>
      <body>
        <header>site chrome</header>
        <nav>navigation links</nav>
        <main>
          <h1>Finding</h1>
          <p>Transformers use self-attention to compare tokens in a sequence.</p>
          <aside>related links</aside>
          <p>Recurrent networks process tokens sequentially.</p>
        </main>
        <footer>copyright footer</footer>
      </body>
    </html>
    """

    def fake_urlopen(req, timeout):
        return _FakeResponse(html)

    monkeypatch.setattr(run.urllib.request, "urlopen", fake_urlopen)
    out = run.fetch_url("https://example.com/source")

    assert out["url"] == "https://example.com/final"
    assert out["content_type"] == "text/html; charset=utf-8"
    assert out["title"] == "Research Title Example"
    assert "Transformers use self-attention" in out["content"]
    assert "Recurrent networks process tokens sequentially" in out["content"]
    assert "navigation links" not in out["content"]
    assert "site chrome" not in out["content"]
    assert "related links" not in out["content"]
    assert "copyright footer" not in out["content"]
    assert "evidence_card" not in out
    assert "facts" not in out
    assert "content_preview" not in out
    assert out["truncated"] is False
    assert out["source_tokens_estimate"] > 0


def test_fetch_url_truncates_clean_content_at_model_context_budget(monkeypatch) -> None:
    run = _load_run_module()
    html = f"<html><head><title>Huge</title></head><body><main>{'x' * 33000}</main></body></html>"

    def fake_urlopen(req, timeout):
        return _FakeResponse(html.encode("utf-8"))

    monkeypatch.setattr(run.urllib.request, "urlopen", fake_urlopen)
    out = run.fetch_url("https://example.com/huge")

    assert out["title"] == "Huge"
    assert out["truncated"] is True
    assert len(out["content"]) == run._MAX_BODY_CHARS
    assert "evidence_card" not in out


def test_memory_trace_summary_counts_events() -> None:
    run = _load_run_module()
    counts = run.memory_trace_summary([
        {"event_type": "memory_selection"},
        {"event_type": "memory_selection"},
        {"event_type": "memory_write"},
        {"event_type": "model_call"},
    ])
    assert counts["memory_selection"] == 2
    assert counts["memory_write"] == 1


async def test_question_dialog_accepts_suggestion_with_enter(monkeypatch) -> None:
    run = _load_run_module()
    model = _AsyncQuestionModel(["DeepSeek API 怎么收费，缓存会带来什么影响？"])
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    question = await run._generate_and_confirm_question(
        run.Console(file=None), model, ["https://example.com/pricing"]
    )

    assert question == "DeepSeek API 怎么收费，缓存会带来什么影响？"
    assert len(model.prompts) == 1


async def test_question_dialog_supports_repeated_refinement(monkeypatch) -> None:
    run = _load_run_module()
    model = _AsyncQuestionModel([
        "DeepSeek API 怎么收费？",
        "DeepSeek API 的缓存定价如何影响成本？",
        "DeepSeek API 不同模型的缓存定价如何影响成本？",
    ])
    answers = iter(["重点讲缓存", "也比较不同模型", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    question = await run._generate_and_confirm_question(
        run.Console(file=None), model, ["https://example.com/pricing"]
    )

    assert question == "DeepSeek API 不同模型的缓存定价如何影响成本？"
    assert len(model.prompts) == 3
    assert "重点讲缓存" in model.prompts[1]
    assert "也比较不同模型" in model.prompts[2]


async def test_question_dialog_can_be_cancelled(monkeypatch) -> None:
    run = _load_run_module()
    model = _AsyncQuestionModel(["DeepSeek API 怎么收费？"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "/cancel")

    question = await run._generate_and_confirm_question(
        run.Console(file=None), model, ["https://example.com/pricing"]
    )

    assert question is None
    assert len(model.prompts) == 1


def test_research_plan_prompt_accepts_feedback(monkeypatch) -> None:
    run = _load_run_module()
    output = StringIO()
    prompt = run.ResearchPlanPrompt(run.Console(file=output, force_terminal=False))
    monkeypatch.setattr("builtins.input", lambda _prompt: "增加成本测算")

    decision, reason = prompt.ask({"summary": "propose_research_plan({'tasks': []})"})

    assert decision == "revise"
    assert reason == "增加成本测算"
    assert "确认并开始研究" in output.getvalue()
