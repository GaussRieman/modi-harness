"""Builtin save_artifact in a subagent must land in the child's workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from modi_harness import ModiAgent
from modi_harness._test_fixtures import make_session


class _Script(BaseChatModel):
    by_agent: dict[str, list[Any]] = Field(default_factory=dict)
    cursor: dict[str, dict[str, int]] = Field(default_factory=dict)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        agent_name = self._sniff(messages)
        cur = self.cursor.setdefault(agent_name, {"i": 0})
        i = cur["i"]
        cur["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.by_agent[agent_name][i])])

    def _sniff(self, messages) -> str:
        for m in messages:
            content = getattr(m, "content", "") or ""
            if isinstance(content, str) and "AGENT_NAME=" in content:
                return content.split("AGENT_NAME=", 1)[1].split("\n", 1)[0].strip()
        return "unknown"

    @property
    def _llm_type(self) -> str:
        return "builtin_sub_script"


def _agent_md(
    root: Path,
    name: str,
    *,
    allowed_subagents: list[str] | None = None,
    tools: list[str] | None = None,
) -> Path:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tool_block = "\n".join(f"  - {t}" for t in (tools or []))
    pp = "  mode: auto"
    if allowed_subagents is not None:
        pp += f"\n  allowed_subagents: {allowed_subagents!r}"
    p.write_text(
        f"""---
name: {name}
description: e2e
tools:
{tool_block if tool_block else '  []'}
permission_profile:
{pp}
---
AGENT_NAME={name}
"""
    )
    return p


def test_child_save_artifact_lands_in_child_workspace(tmp_path: Path) -> None:
    src = tmp_path / "src"
    writer_md = _agent_md(src, "writer")
    lead_md = _agent_md(
        src, "lead", tools=["delegate_to_writer"], allowed_subagents=["writer"]
    )
    writer = ModiAgent.from_markdown(writer_md)
    lead = ModiAgent.from_markdown(lead_md, subagents=[writer])

    session = make_session(
        tmp_path,
        chat_model=_Script(by_agent={
            "lead": [
                AIMessage(content="", tool_calls=[{
                    "name": "delegate_to_writer",
                    "args": {"task": {"goal": "write report"}, "rationale": "specialty"},
                    "id": "tc1",
                }]),
                AIMessage(content="lead done"),
            ],
            "writer": [
                AIMessage(content="", tool_calls=[{
                    "name": "save_artifact",
                    "args": {"name": "report.md", "content": "# report"},
                    "id": "tc2",
                }]),
                AIMessage(content="writer done"),
            ],
        }),
        agents=[lead],
    )

    response = session.run_task(
        agent="lead",
        input={"goal": "delegate", "messages": [{"role": "user", "content": "go"}]},
        thread_id="t-sub-builtin",
    )
    assert response["status"] == "completed"

    # Find both run dirs.
    parent_run = response["run_id"]
    runs = [p for p in (tmp_path / "ws").iterdir() if p.is_dir()]
    assert len(runs) >= 2
    child_run = next(p for p in runs if p.name != parent_run)

    # Child has the artifact, parent does NOT.
    assert (child_run / "artifacts" / "report.md").exists(), \
        "child workspace should contain report.md"
    assert not (tmp_path / "ws" / parent_run / "artifacts" / "report.md").exists(), \
        "parent workspace must not contain the child's artifact"
