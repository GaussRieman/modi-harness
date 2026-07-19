"""Research Assistant migration onto the generic Task Graph runtime."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from modi_harness import ModiHarness, ModiSession
from modi_harness.checkpoint import InMemoryRootCheckpointStore
from modi_harness.cli.renderer import _format_terminal_output
from modi_harness.long_task import InMemoryChildCheckpointStore

from .test_research_assistant import _SOURCE_URL, _agent_with_fake_research


def _comparison_intent() -> dict[str, Any]:
    dimensions = [
        {
            "id": "dimensions",
            "title": "车身尺寸与空间",
            "criterion_id": "criterion-dimensions",
            "question": "Tesla Model Y 与小米 YU7 的车身尺寸和空间有何差异?",
            "entities": [
                {
                    "name": "Tesla Model Y",
                    "aliases": ["Model Y", "Tesla ModelY", "特斯拉 Model Y"],
                },
                {
                    "name": "小米 YU7",
                    "aliases": ["小米YU7", "Xiaomi YU7", "小米YU"],
                },
            ],
            "dimension": "车身尺寸与空间",
            "verification_method": "single_source_sufficient",
            "authority_bindings": [],
            "depends_on": [],
        },
        {
            "id": "pricing",
            "title": "价格与配置",
            "criterion_id": "criterion-pricing",
            "question": "Tesla Model Y 与小米 YU7 的价格和配置有何差异?",
            "entities": [
                {
                    "name": "Tesla Model Y",
                    "aliases": ["Model Y", "Tesla ModelY", "特斯拉 Model Y"],
                },
                {
                    "name": "小米 YU7",
                    "aliases": ["小米YU7", "Xiaomi YU7", "小米YU"],
                },
            ],
            "dimension": "价格与配置",
            "verification_method": "single_source_sufficient",
            "authority_bindings": [],
            "depends_on": [],
        },
    ]
    return {
        "intent_id": "tesla-model-y-vs-xiaomi-yu7",
        "version": 1,
        "status": "draft",
        "goal": "对比 Tesla Model Y 与小米 YU7",
        "desired_outcome": "形成有公开来源、明确局限的购车对比",
        "success_criteria": [
            {
                "id": item["criterion_id"],
                "description": item["question"],
                "required": True,
                "verification_mode": "evidence",
                "validator_id": "research-criterion-verifier",
            }
            for item in dimensions
        ],
        "constraints": ["使用当前公开来源", "保留完整车型名称和中文别名"],
        "non_goals": ["不推断未公开配置"],
        "assumptions": [],
        "planning_context": {
            "subject": "Tesla Model Y vs 小米 YU7",
            "research_question": "两款车各自适合什么用户?",
            "candidate_dimensions": dimensions,
        },
    }


class _ParallelComparisonModel(BaseChatModel):
    """Drive root scope/synthesis and isolated children without serial assumptions."""

    def __init__(self, *, blocked_task: str | None = None) -> None:
        super().__init__()
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_root_phase", 0)
        object.__setattr__(self, "_task_phases", {"dimensions": 0, "pricing": 0})
        object.__setattr__(self, "_blocked_task", blocked_task)
        object.__setattr__(self, "_call_count", 0)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        del stop, run_manager, kwargs
        text = "\n".join(str(message) for message in messages)
        with self._lock:
            self._call_count += 1
            call_id = f"parallel-{self._call_count}"
            if self._root_phase == 0:
                self._root_phase = 1
                return self._result(
                    "route__deep_research",
                    {
                        "request": "对比Teslamodely和小米YU",
                        "subject": "Tesla Model Y vs 小米 YU7",
                        "question": "两款车有什么差异?",
                    },
                    call_id,
                )
            if self._root_phase == 1:
                self._root_phase = 2
                return self._result("complete_node", _comparison_intent(), call_id)
            task_id = next(
                (
                    item
                    for item, phase in self._task_phases.items()
                    if phase < 4 and item in text
                ),
                None,
            )
            if task_id is None:
                if not all(phase == 4 for phase in self._task_phases.values()):
                    raise AssertionError(f"child Task identity missing from model context: {text}")
                return self._result(
                    "complete_node",
                    {
                        "direct_answer": "Model Y 与 YU7 的空间、价格取向不同, 应结合证据和预算选择。",
                        "limitations": ["小米 YU7 的部分公开价格信息仍有限。"],
                    },
                    call_id,
                )
            phase = self._task_phases[task_id]
            self._task_phases[task_id] = phase + 1
            blocked = task_id == self._blocked_task
            if phase == 0:
                return self._result("get_current_time", {}, call_id)
            if phase == 1:
                tokens = re.findall(r"time-\d+", text)
                if not tokens:
                    raise AssertionError("fresh time token missing from child context")
                dimension = "车身尺寸与空间" if task_id == "dimensions" else "价格与配置"
                return self._result(
                    "public_web_search",
                    {
                        "task_id": task_id,
                        "time_token": tokens[-1],
                        "searches": [
                            {
                                "query": f'"Tesla Model Y" 2026 {dimension}',
                                "entity": "Tesla Model Y",
                                "aliases": ["Model Y", "Tesla ModelY", "特斯拉 Model Y"],
                                "dimension": dimension,
                            },
                            {
                                "query": f'"小米 YU7" 2026 {dimension}',
                                "entity": "小米 YU7",
                                "aliases": ["小米YU7", "Xiaomi YU7", "小米YU"],
                                "dimension": dimension,
                            },
                        ],
                    },
                    call_id,
                )
            conclusion = (
                "当前公开来源不足以可靠比较价格。"
                if blocked
                else "两款车型的尺寸定位存在可核验差异。"
            )
            if phase == 2:
                return self._result(
                    "verify_claim_evidence",
                    {
                        "task_id": task_id,
                        "claim": conclusion,
                        "search_ids": [f"search-{task_id}-1"],
                        "items": (
                            []
                            if blocked
                            else [
                                {
                                    "source_url": _SOURCE_URL,
                                    "source_type": "official",
                                    "stance": "supporting",
                                    "independent": True,
                                    "directness": "direct",
                                    "as_of": "2026-07-18",
                                }
                            ]
                        ),
                    },
                    call_id,
                )
            return self._result(
                "complete_node",
                {
                    "finding": {
                        "conclusion": conclusion,
                        "implications": "该维度会直接影响购车选择。",
                        "verification_id": f"verification-{task_id}-1",
                        "status": "blocked" if blocked else "sourced",
                        "limitations": ["公开价格来源不足。"] if blocked else [],
                    }
                },
                call_id,
            )

    @staticmethod
    def _result(name: str, arguments: dict[str, Any], call_id: str) -> ChatResult:
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[{"name": name, "args": arguments, "id": call_id}],
                    )
                )
            ]
        )

    @property
    def _llm_type(self) -> str:
        return "parallel-research-task-graph-test"


def test_model_y_yu7_dimensions_run_in_parallel_children_and_keep_limitations(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str]] = []
    agent = _agent_with_fake_research(
        calls,
        search_resolution={"dimensions": "sourced", "pricing": "no_evidence"},
    )
    root_store = InMemoryRootCheckpointStore()
    child_store = InMemoryChildCheckpointStore()
    model = _ParallelComparisonModel(blocked_task="pricing")
    session = ModiSession(
        ModiHarness(model),
        agents=[agent],
        checkpointer=MemorySaver(),
        workspace_root=tmp_path / "workspace",
        memory_root=tmp_path / "memory",
        root_checkpoint_store=root_store,
        child_checkpoint_store=child_store,
        max_steps=120,
    )

    review = session.run_task(
        agent=agent.name,
        input={"prompt": "对比Teslamodely和小米YU"},
        thread_id="model-y-yu7-task-graph",
    )

    assert review["status"] == "interrupted", review
    interaction = review["pending_interaction"]
    assert interaction is not None and interaction["kind"] == "node_review"
    assert session.get_child_runs("model-y-yu7-task-graph") == []

    completed = session.respond_to_interaction(
        thread_id="model-y-yu7-task-graph",
        interaction_id=interaction["interaction_id"],
        decision="approved",
    )
    assert completed["status"] == "completed", {
        "response": completed,
        "task_phases": model._task_phases,
        "calls": calls,
        "children": session.get_child_runs("model-y-yu7-task-graph"),
        "root_state": session.get_state("model-y-yu7-task-graph"),
        "child_states": [
            item.workflow_state for item in child_store.list_by_root(completed["run_id"])
        ],
    }
    output = completed["output"]
    assert output is not None
    assert [item["task_id"] for item in output["key_findings"]] == [
        "dimensions",
        "pricing",
    ]
    assert [item["status"] for item in output["key_findings"]] == [
        "sourced",
        "limited",
    ]
    assert list(output["citations"]) == [_SOURCE_URL]
    assert "公开价格来源不足" in " ".join(output["limitations"])
    assert output["direct_answer"] == (
        "Tesla Model Y 与小米 YU7 的车身尺寸和空间有何差异?: "
        "两款车型的尺寸定位存在可核验差异。\n\n"
        "Tesla Model Y 与小米 YU7 的价格和配置有何差异?: "
        "未达到验证要求，详见限制"  # noqa: RUF001
    )
    assert "空间、价格取向不同" not in output["direct_answer"]
    assert all("implications" not in item for item in output["key_findings"])
    assert all("provenance" in item for item in output["key_findings"])
    assert all(
        item["provenance"]["searches"][0]["current_time"]["current_date"]
        for item in output["key_findings"]
    )
    rendered = _format_terminal_output(output)
    assert "[未核实] 当前公开来源不足以可靠比较价格。" in rendered
    assert "公开价格来源不足" in rendered
    assert "该维度会直接影响购车选择" not in rendered

    child_runs = session.get_child_runs("model-y-yu7-task-graph")
    assert len(child_runs) == 2
    assert {item["status"] for item in child_runs} == {"completed"}
    history = session.get_task_history("model-y-yu7-task-graph")
    assert [item["task_id"] for item in history] == ["dimensions", "pricing"]
    assert all(item["depends_on"] == [] for item in history)
    assert all(item["status"] == "completed" for item in history)

    search_calls = [item for item in calls if item[0] == "public_web_search"]
    assert {item[2] for item in search_calls} == {"dimensions", "pricing"}
    assert all("Tesla Model Y" in item[1] for item in search_calls)
    assert all("小米 YU7" in item[1] for item in search_calls)
    event_types = [
        event["event_type"] for event in session.get_trace("model-y-yu7-task-graph")
    ]
    child_started = [index for index, item in enumerate(event_types) if item == "child_started"]
    task_completed = [index for index, item in enumerate(event_types) if item == "task_completed"]
    assert len(child_started) == 2 and len(task_completed) >= 2
    assert max(child_started) < min(task_completed)
