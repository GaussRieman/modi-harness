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


def test_research_assistant_memory_demo_batches_recall_and_write(tmp_path: Path) -> None:
    run = _load_run_module()
    script = _ScriptModel(script=[
        AIMessage(
            content="",
            tool_calls=[
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
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "submit_output",
                "args": {
                    "question": "比较 Transformer 和 RNN",
                    "key_findings": ["Transformer 更适合并行训练和长程依赖建模。"],
                    "evidence": [{"citation_key": "demo", "source": "demo memory"}],
                    "open_questions": [],
                    "confidence": "medium",
                    "risk_label": "low",
                },
                "id": "tc-submit",
            }],
        ),
    ])
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
    assert script.cursor["i"] == 2
    assert response["output"]["question"] == "比较 Transformer 和 RNN"
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
        "question",
        "key_findings",
        "evidence",
        "open_questions",
        "confidence",
        "risk_label",
    ]
    assert "submit_output" in agent.metadata["_frontmatter_tools"]
    assert {binding.spec["name"] for binding in agent.tools} >= {"fetch_url", "source_extract"}


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
