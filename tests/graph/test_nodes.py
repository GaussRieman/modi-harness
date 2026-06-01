"""End-to-end smoke for the compiled main graph with MemorySaver."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from modi_harness._utils import new_ulid
from modi_harness.agents import AgentLoader
from modi_harness.context import ContextManager
from modi_harness.graph import GraphDeps, build_main_graph
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.memory import MemoryPaths, MemoryStore
from modi_harness.models import ModelAdapter
from modi_harness.output import OutputController
from modi_harness.policy import PolicyGate
from modi_harness.tools import ToolGateway, ToolRegistry
from modi_harness.workspace import WorkspaceManager


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


def _write_agent(root: Path, name: str, tools: list[str] | None = None) -> None:
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tool_block = "\n".join(f"  - {t}" for t in (tools or []))
    body = f"""---
name: {name}
description: demo
tools:
{tool_block}
---
Reply directly.
"""
    p.write_text(body)


def _deps(tmp_path: Path, chat_model: BaseChatModel) -> GraphDeps:
    agents_dir = tmp_path / "agents"
    workspace = WorkspaceManager(workspace_root=tmp_path / "ws")
    memory_root = tmp_path / "mem"
    memory = MemoryStore(
        MemoryPaths(
            user=memory_root / "user",
            agent=memory_root / "agent",
            project=memory_root / "project",
            conversation=memory_root / "conversation",
        )
    )
    policy = PolicyGate()
    registry = ToolRegistry()
    hook_registry = HookRegistry.from_files(user_settings=None, project_settings=None)
    hooks = HookDispatcher(
        registry=hook_registry,
        project_root=tmp_path,
        pass_env=["PATH"],
    )
    gateway = ToolGateway(
        registry=registry,
        policy=policy,
        hooks=hooks,
        result_inline_limit_bytes=8192,
    )
    context = ContextManager(policy=policy)
    model = ModelAdapter(chat_model=chat_model)
    output = OutputController()
    return GraphDeps(
        agents=AgentLoader(project_dir=agents_dir),
        skills=None,
        memory=memory,
        workspace=workspace,
        context=context,
        model=model,
        tools=gateway,
        policy=policy,
        output=output,
        hooks=hooks,
    )


def _seed_state(agent: str = "demo") -> dict[str, Any]:
    run_id = new_ulid()
    return {
        "run_id": run_id,
        "root_run_id": run_id,
        "parent_run_id": None,
        "parent_thread_id": None,
        "thread_id": f"run_{run_id}",
        "agent_name": agent,
        "permission_mode": "auto",
        "task": {"goal": "say hi"},
        "messages": [
            {"role": "user", "content": "hi", "tool_call_id": None, "metadata": {}}
        ],
        "loaded_skills": [],
        "tool_calls": [],
        "denied_actions": [],
        "workspace_refs": [],
        "pending_approval": None,
        "draft_output": None,
        "final_output": None,
        "step_count": 0,
        "status": "running",
        "pending_trace_events": [],
        "repair_used": 0,
        "max_steps": 20,
    }


def test_compiled_graph_runs_to_completion(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo")
    deps = _deps(tmp_path, _ScriptModel(script=[AIMessage(content="hello back")]))
    graph = build_main_graph(deps, checkpointer=MemorySaver())
    state = _seed_state()
    final = graph.invoke(
        state,
        config={
            "configurable": {
                "thread_id": state["thread_id"],
                "modi_deps": deps,
            }
        },
    )
    assert final["status"] == "completed"
    assert final["final_output"]["value"] == "hello back"


def test_compiled_graph_exposes_node_set(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo")
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    graph = build_main_graph(deps, checkpointer=MemorySaver())
    nodes = set(graph.get_graph().nodes.keys())
    assert {"setup", "model_turn", "execute_tool", "validate_output"}.issubset(nodes)


def test_memory_level_flows_through_model_turn(tmp_path: Path) -> None:
    """Agent with memory_level=minimal only gets feedback records in context."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    # Write agent with memory_level: minimal
    (agents_dir / "strict.md").write_text(
        "---\nname: strict\ndescription: strict agent\nmemory_level: minimal\n---\nBe strict.\n"
    )

    deps = _deps(tmp_path, _ScriptModel(script=[AIMessage(content="done")]))

    # Seed memory with feedback + user records
    deps.memory.write_record({
        "id": "fb1",
        "scope": "user",
        "type": "feedback",
        "name": "fb",
        "description": "feedback",
        "body": "be terse",
        "tags": [],
        "source_run_id": None,
        "expires_at": None,
        "metadata": {},
    })
    deps.memory.write_record({
        "id": "u1",
        "scope": "user",
        "type": "user",
        "name": "pref",
        "description": "user pref",
        "body": "likes verbose",
        "tags": [],
        "source_run_id": None,
        "expires_at": None,
        "metadata": {},
    })

    graph = build_main_graph(deps, checkpointer=MemorySaver())
    state = _seed_state(agent="strict")
    final = graph.invoke(
        state,
        config={
            "configurable": {
                "thread_id": state["thread_id"],
                "modi_deps": deps,
            }
        },
    )
    assert final["status"] == "completed"
    # The test verifies the graph completes successfully with memory_level=minimal.
    # The actual filtering is tested in test_levels.py; here we confirm integration.


# ---------------------------------------------------------------------------
# Repair-feedback tests: when output_validation rejects, the harness must
# inject the issues back into the conversation so the model can repair on
# the next turn instead of retrying blind. (Bug observed in the
# research-assistant example: 4 rejections in a row with identical output.)
# ---------------------------------------------------------------------------


def _write_strict_contract_agent(root: Path, name: str = "strict") -> None:
    """Agent with a structured output_contract that requires JSON."""
    p = root / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"""---
name: {name}
description: strict json agent
output_contract:
  free_form: false
  citation_required: true
  risk_label_required: true
  required_fields:
    - question
    - evidence
    - risk_label
---
Reply with JSON only.
""")


def test_validate_rejection_appends_repair_message(tmp_path: Path) -> None:
    """A rejected validation must surface its issues into state['messages'].

    The next ``model_turn`` reads ``state['messages']`` via the context manager,
    so this is how feedback reaches the model. Without it, the model retries
    blind and exhausts the repair budget producing the same bad output.
    """
    from langchain_core.runnables import RunnableConfig

    from modi_harness.graph import nodes

    _write_strict_contract_agent(tmp_path / "agents")
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    state = _seed_state(agent="strict")
    # Simulate model_turn having stored a string draft (not JSON).
    state["pending_draft"] = "Here is some markdown, not JSON."
    config: RunnableConfig = {"configurable": {"modi_deps": deps}}

    update = nodes.validate_output_node(state, config)

    new_msgs = update.get("messages") or []
    assert len(new_msgs) == 1, f"expected 1 repair message, got {new_msgs!r}"
    repair = new_msgs[0]
    assert repair["role"] == "user"
    body = repair["content"]
    assert "[validation_failed]" in body
    # Repair message must list at least one validator issue code so the model
    # knows what to fix.
    assert "schema.type_mismatch" in body or "missing" in body
    # And tell it to retry with a valid response.
    assert "json" in body.lower() or "object" in body.lower()


def test_validate_pass_does_not_append_repair_message(tmp_path: Path) -> None:
    """When validation passes, no repair message is added."""
    from langchain_core.runnables import RunnableConfig

    from modi_harness.graph import nodes

    _write_strict_contract_agent(tmp_path / "agents")
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    state = _seed_state(agent="strict")
    state["pending_draft"] = {
        "question": "q",
        "evidence": [{"citation_key": "k", "source": "s"}],
        "risk_label": "low",
    }
    config: RunnableConfig = {"configurable": {"modi_deps": deps}}

    update = nodes.validate_output_node(state, config)

    new_msgs = update.get("messages") or []
    assert new_msgs == []
    assert update["status"] == "completed"


def test_repair_message_uses_user_role_not_system(tmp_path: Path) -> None:
    """Repair feedback must use role='user' (not 'system').

    Multiple non-consecutive system messages break Anthropic-compatible
    proxies (GLM gateways). The repair message arrives mid-conversation,
    so it must be a user-role message.
    """
    from langchain_core.runnables import RunnableConfig

    from modi_harness.graph import nodes

    _write_strict_contract_agent(tmp_path / "agents")
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    state = _seed_state(agent="strict")
    state["pending_draft"] = "not json"
    config: RunnableConfig = {"configurable": {"modi_deps": deps}}

    update = nodes.validate_output_node(state, config)
    repair = (update.get("messages") or [])[0]
    assert repair["role"] == "user"
