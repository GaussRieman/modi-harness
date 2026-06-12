"""Offline tests for the research_assistant Memory demo."""

from __future__ import annotations

import importlib.util
from pathlib import Path
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


def test_research_assistant_memory_demo_recall_and_write(tmp_path: Path) -> None:
    run = _load_run_module()
    script = _ScriptModel(script=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "recall_memory",
                "args": {
                    "query": "研究",
                    "scopes": ["user", "workspace", "thread", "agent"],
                    "limit": 5,
                },
                "id": "tc-recall",
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
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
            }],
        ),
        AIMessage(content='{"question":"比较 Transformer 和 RNN","key_findings":["Transformer 更适合并行训练和长程依赖建模。"],"evidence":[{"citation_key":"demo","source":"demo memory"}],"open_questions":[],"confidence":"medium","risk_label":"low"}'),
    ])
    session = run.build_session(
        chat_model=script,
        memory_root=tmp_path / "mem",
        workspace_root=tmp_path / "ws",
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
    workspace_records = session.list_memory(scopes=["workspace"], tags=["model-comparison"])
    assert any(r["id"] == "ra_project_compare_models" for r in workspace_records)

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
