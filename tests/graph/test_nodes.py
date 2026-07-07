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
from modi_harness.brain import MISSING_INPUT_RULE_ID, SlowModelBrain
from modi_harness.context import ContextManager
from modi_harness.graph import GraphDeps, build_main_graph
from modi_harness.hooks import HookDispatcher, HookRegistry
from modi_harness.loop import slow_model_step_decision
from modi_harness.loop.types import StepContext, StepDecision, StepValidationError
from modi_harness.memory import MemoryPaths, MemoryStore, RunRecallCache
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
            workspace=memory_root / "workspace",
            thread=memory_root / "thread",
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


def _tool_message_ids(update: dict[str, Any]) -> list[str]:
    return [
        m["tool_call_id"]
        for m in update.get("messages", [])
        if m.get("role") == "tool"
    ]


class _RecordingBrain:
    def __init__(self) -> None:
        self.contexts: list[StepContext] = []

    def plan_step(self, context: StepContext) -> StepDecision:
        self.contexts.append(context)
        return slow_model_step_decision(step_id=context["step_id"])


class _InvalidBrain:
    def plan_step(self, context: StepContext) -> StepDecision:
        decision = slow_model_step_decision(step_id=context["step_id"])
        decision["operation"] = {
            "kind": "tool",
            "summary": "call something",
            "target": "lookup",
            "arguments": {},
            "expected_outcome": None,
        }
        decision["ask"] = {
            "prompt": "Need both paths",
            "reason": "invalid contract",
            "allowed_kinds": ["clarify"],
        }
        return decision


def test_compiled_graph_runs_to_completion(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo")
    deps = _deps(tmp_path, _ScriptModel(script=[AIMessage(content="hello back")]))
    deps.brain = SlowModelBrain()
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
    assert final["loop_state"]["step_index"] >= 1
    assert final["step_records"]
    assert final["step_records"][0]["decision"]["reasoning_mode"] == "slow"


def test_default_brain_fast_rule_interrupts_for_missing_input(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "demo.md").write_text(
        """---
name: demo
description: demo
brain:
  fast_rules:
    required_inputs:
      - deadline
---
Reply directly.
"""
    )
    model = _ScriptModel(script=[])
    deps = _deps(tmp_path, model)
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

    record = final["step_records"][0]
    assert final["status"] == "interrupted"
    assert final["pending_interaction"]["kind"] == "user_input"
    assert final["pending_interaction"]["payload"]["step_id"] == record["step_id"]
    assert record["step_kind"] == "clarify"
    assert record["decision"]["reasoning_mode"] == "fast"
    assert record["decision"]["rule_ref"] == MISSING_INPUT_RULE_ID
    assert final["loop_state"]["continuation"] == "wait_for_user"
    assert model.cursor["i"] == 0


def test_compiled_graph_exposes_node_set(tmp_path: Path) -> None:
    _write_agent(tmp_path / "agents", "demo")
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    graph = build_main_graph(deps, checkpointer=MemorySaver())
    nodes = set(graph.get_graph().nodes.keys())
    assert {"setup", "model_turn", "execute_tool", "validate_output"}.issubset(nodes)


def test_setup_initializes_loop_state(tmp_path: Path) -> None:
    from langchain_core.runnables import RunnableConfig

    from modi_harness.graph.nodes import setup_node

    _write_agent(tmp_path / "agents", "demo")
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    state = _seed_state()
    config: RunnableConfig = {"configurable": {"modi_deps": deps}}

    update = setup_node(state, config)

    loop = update["loop_state"]
    assert loop["run_id"] == state["run_id"]
    assert loop["agent_name"] == "demo"
    assert loop["status"] == "active"
    assert loop["intent_version"] == update["intent_version"]
    assert loop["stage_id"] == update["stage_id"]
    assert loop["step_index"] == 0
    assert update["current_step"] is None
    events = update["pending_trace_events"]
    assert any(e["event_type"] == "loop_initialized" for e in events)


def test_model_turn_records_slow_brain_step(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import model_turn_node, setup_node

    _write_agent(tmp_path / "agents", "demo")
    deps = _deps(tmp_path, _ScriptModel(script=[AIMessage(content="hello back")]))
    deps.brain = SlowModelBrain()
    state = _seed_state()
    config = {"configurable": {"modi_deps": deps}}
    state.update(setup_node(state, config))

    update = model_turn_node(state, config)

    record = update["step_records"][0]
    assert record["loop_id"] == state["loop_state"]["loop_id"]
    assert record["step_kind"] == "plan"
    assert record["status"] == "completed"
    assert record["decision"]["reasoning_mode"] == "slow"
    assert record["decision"]["continuation_basis"]["source"] == "slow_plan"
    assert update["loop_state"]["step_index"] == 1
    assert update["last_continuation_decision"]["step_id"] == record["step_id"]
    event_types = [e["event_type"] for e in update["pending_trace_events"]]
    assert "step_planned" in event_types
    assert "step_completed" in event_types
    assert "loop_continuation_decision" in event_types


def test_model_turn_calls_brain_with_step_context(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import model_turn_node, setup_node

    _write_agent(tmp_path / "agents", "demo")
    brain = _RecordingBrain()
    deps = _deps(tmp_path, _ScriptModel(script=[AIMessage(content="hello back")]))
    deps.brain = brain
    state = _seed_state()
    config = {"configurable": {"modi_deps": deps}}
    state.update(setup_node(state, config))

    update = model_turn_node(state, config)

    assert len(brain.contexts) == 1
    context = brain.contexts[0]
    assert context["step_id"] == update["step_records"][0]["step_id"]
    assert context["loop"]["loop_id"] == state["loop_state"]["loop_id"]
    assert context["intent"]["version"] == state["human_intent"]["version"]
    assert context["stage"]["id"] == state["stage_id"]
    assert context["intent_clarity"] == state["intent_clarity"]
    assert context["autonomy_scope"] == state["autonomy_scope"]
    assert context["agent_state"]["agent_name"] == "demo"
    assert context["event"]["kind"] == "model_turn"
    assert "tools" in context["available_capabilities"]


def test_model_turn_rejects_invalid_brain_decision_before_model_call(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import model_turn_node, setup_node

    _write_agent(tmp_path / "agents", "demo")
    model = _ScriptModel(script=[])
    deps = _deps(tmp_path, model)
    deps.brain = _InvalidBrain()
    state = _seed_state()
    config = {"configurable": {"modi_deps": deps}}
    state.update(setup_node(state, config))

    with pytest.raises(StepValidationError):
        model_turn_node(state, config)

    assert model.cursor["i"] == 0


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
    deps.brain = SlowModelBrain()
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


def _write_webagent_contract_agent(root: Path) -> None:
    p = root / "webagent.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("""---
name: webagent
description: web agent
skills:
  - zhizheng
tools:
  - browser_observe
output_contract:
  required_fields:
    - task
    - status
    - url
    - evidence_dir
    - summary
    - failures
  schema:
    type: object
    properties:
      task:
        type: string
      status:
        type: string
      url:
        type: string
      evidence_dir:
        type: string
      summary:
        type: string
      failures:
        type: array
        items:
          type: string
    required: [task, status, url, evidence_dir, summary, failures]
    additionalProperties: false
---
Use tools for browser work.
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
    # knows what to fix. Don't pin the specific code — the validator may
    # report `schema.unparseable_json` (string-not-JSON), `schema.type_mismatch`
    # (dict shape wrong), or `schema.missing_field`. Any of these is fine
    # as long as something machine-readable is there.
    assert "schema." in body or "missing" in body
    # And tell it to retry with a valid response.
    assert "json" in body.lower() or "object" in body.lower()


def test_webagent_zhizheng_plain_text_draft_returns_to_tool_loop(tmp_path: Path) -> None:
    from langchain_core.runnables import RunnableConfig

    from modi_harness.graph import nodes

    _write_webagent_contract_agent(tmp_path / "agents")
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    state = _seed_state(agent="webagent")
    state["pending_draft"] = (
        "《医院检查通知单》已成功生成。请问现在您希望:"
        "1. 结束采集 2. 等待/手动介入 3. 继续观察"
    )
    config: RunnableConfig = {"configurable": {"modi_deps": deps}}

    update = nodes.validate_output_node(state, config)

    assert update["pending_draft"] is None
    assert "status" not in update
    message = (update.get("messages") or [])[0]
    assert message["role"] == "user"
    assert "[webagent_tool_loop_required]" in message["content"]
    assert "browser_request_manual_intervention(resume_expected=true)" in message["content"]
    assert "submit_output" in message["content"]
    events = update.get("pending_trace_events") or []
    assert events[0]["event_type"] == "tool_loop_required"


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


# ---------------------------------------------------------------------------
# submit_output protocol interception
# ---------------------------------------------------------------------------


def test_split_submit_output_extracts_args_as_draft() -> None:
    """A submit_output call → draft is its dict args; tool list cleared."""
    from modi_harness.graph.nodes import _split_submit_output

    calls = [{"tool_name": "submit_output", "arguments": {"answer": 42}}]
    remaining, draft = _split_submit_output(calls, "ignored content")
    assert remaining == []
    assert draft == {"answer": 42}


def test_split_submit_output_drops_sibling_tool_calls() -> None:
    """submit_output is contractually the model's last action — sibling
    tool calls in the same turn are discarded so the draft is not lost
    on the next round-trip.
    """
    from modi_harness.graph.nodes import _split_submit_output

    calls = [
        {"tool_name": "search", "arguments": {"q": "x"}},
        {"tool_name": "submit_output", "arguments": {"answer": "ok"}},
    ]
    remaining, draft = _split_submit_output(calls, "ignored")
    assert remaining == []
    assert draft == {"answer": "ok"}


def test_split_submit_output_no_submit_uses_message_content() -> None:
    """No submit_output and no tool calls → draft falls back to message text."""
    from modi_harness.graph.nodes import _split_submit_output

    remaining, draft = _split_submit_output([], "the model said this")
    assert remaining == []
    assert draft == "the model said this"


def test_split_submit_output_other_tools_no_draft() -> None:
    """When the model is mid-tool-loop, draft must be None to defer validation."""
    from modi_harness.graph.nodes import _split_submit_output

    calls = [{"tool_name": "search", "arguments": {"q": "x"}}]
    remaining, draft = _split_submit_output(calls, "")
    assert len(remaining) == 1
    assert draft is None


def test_split_submit_output_empty_args_yields_empty_dict() -> None:
    """Defensive: missing args key still produces a dict (will be rejected
    by validator's required_fields check rather than crashing here)."""
    from modi_harness.graph.nodes import _split_submit_output

    calls = [{"tool_name": "submit_output"}]  # no arguments
    remaining, draft = _split_submit_output(calls, "")
    assert remaining == []
    assert draft == {}


# ---------------------------------------------------------------------------
# default Markdown rendering of submit_output payload
# ---------------------------------------------------------------------------


def test_payload_to_markdown_renders_briefing_shape() -> None:
    """Generic renderer handles the research_assistant briefing shape:
    top-level dict with str / list-of-str / list-of-dict / scalar values.
    """
    from modi_harness.graph.nodes import _payload_to_markdown

    payload = {
        "question": "What's up?",
        "key_findings": [
            {"finding": "First", "citation_key": "src1"},
            {"finding": "Second", "citation_key": "src2"},
        ],
        "open_questions": ["Why?", "How?"],
        "confidence": "low",
    }
    md = _payload_to_markdown(payload)

    # Top-level keys → ## sections.
    assert "## question" in md
    assert "## key_findings" in md
    assert "## open_questions" in md
    assert "## confidence" in md
    # String value is rendered verbatim.
    assert "What's up?" in md
    # List of dicts rendered as bullets with **k**: v · pairs.
    assert "**finding**: First" in md
    assert "**citation_key**: src1" in md
    # List of primitives rendered as plain bullets.
    assert "- Why?" in md
    # Scalar value rendered as-is.
    assert "low" in md


def test_payload_to_markdown_handles_empty_collections() -> None:
    from modi_harness.graph.nodes import _payload_to_markdown

    md = _payload_to_markdown({"items": [], "name": ""})
    assert "_(empty)_" in md  # both empty list and empty string land here


def test_builtin_tools_offered_to_model_when_agent_declares_none(tmp_path: Path) -> None:
    """Regression: model_turn_node must offer builtin tools (save_artifact,
    save_draft, ...) to the model even when agent.md lists no tools.

    The catalog model_turn_node builds was previously sourced only from
    profile["default_tools"], so builtins never reached the model's tool list
    and an agent could not honor a "save your results" instruction. The
    execution layer already treats builtins as callable by any agent
    (tools/gateway.py), so the visibility layer must match.
    """
    from modi_harness.graph.nodes import model_turn_node
    from modi_harness.tools.builtin import get_builtin_specs

    # Capture the tool schemas bound to the model.
    bound_tool_names: list[str] = []

    class _SpyModel(BaseChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

        def bind_tools(self, tools, **kwargs):  # type: ignore[override]
            for t in tools:
                fn = t.get("function", t) if isinstance(t, dict) else {}
                if fn.get("name"):
                    bound_tool_names.append(fn["name"])
            return self

        @property
        def _llm_type(self) -> str:
            return "spy"

    deps = _deps(tmp_path, _SpyModel())
    # Register builtins into the gateway's registry, as ModiHarness does.
    for spec, handler in get_builtin_specs():
        deps.tools._registry.register_tool(spec, handler)

    # Agent declares NO tools.
    _write_agent(tmp_path / "agents", "demo", tools=[])
    state = _seed_state("demo")
    deps.workspace.create_run(state["run_id"])

    model_turn_node(state, {"configurable": {"modi_deps": deps}})

    assert "save_artifact" in bound_tool_names, bound_tool_names
    assert "save_draft" in bound_tool_names, bound_tool_names


def test_builtin_tools_respect_agent_deny_list(tmp_path: Path) -> None:
    """A builtin named in the agent's permission_profile.deny must NOT be
    offered to the model, even though builtins are otherwise auto-visible.
    """
    from modi_harness.graph.nodes import model_turn_node
    from modi_harness.tools.builtin import get_builtin_specs

    bound_tool_names: list[str] = []

    class _SpyModel(BaseChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

        def bind_tools(self, tools, **kwargs):  # type: ignore[override]
            for t in tools:
                fn = t.get("function", t) if isinstance(t, dict) else {}
                if fn.get("name"):
                    bound_tool_names.append(fn["name"])
            return self

        @property
        def _llm_type(self) -> str:
            return "spy"

    deps = _deps(tmp_path, _SpyModel())
    for spec, handler in get_builtin_specs():
        deps.tools._registry.register_tool(spec, handler)

    # Agent denies save_memory specifically.
    agent_md = tmp_path / "agents" / "demo.md"
    agent_md.parent.mkdir(parents=True, exist_ok=True)
    agent_md.write_text(
        "---\n"
        "name: demo\n"
        "description: demo\n"
        "permission_profile:\n"
        "  mode: auto\n"
        "  deny:\n"
        "    - save_memory\n"
        "---\n"
        "Reply directly.\n"
    )
    state = _seed_state("demo")
    deps.workspace.create_run(state["run_id"])

    model_turn_node(state, {"configurable": {"modi_deps": deps}})

    assert "save_memory" not in bound_tool_names, bound_tool_names
    assert "save_draft" in bound_tool_names  # other builtins still offered


def test_execute_tool_node_executes_all_pending_calls_in_one_visit(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["lookup"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    seen: list[str] = []
    deps.tools._registry.register_tool(
        {
            "name": "lookup",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
                "additionalProperties": False,
            },
            "risk_level": "L0",
            "side_effect": False,
        },
        lambda **kw: seen.append(kw["q"]) or {"q": kw["q"]},
    )
    state = _seed_state("demo")
    state["pending_tool_calls"] = [
        {"tool_call_id": "tc1", "tool_name": "lookup", "arguments": {"q": "a"}, "malformed": False, "parse_error": None},
        {"tool_call_id": "tc2", "tool_name": "lookup", "arguments": {"q": "b"}, "malformed": False, "parse_error": None},
        {"tool_call_id": "tc3", "tool_name": "lookup", "arguments": {"q": "c"}, "malformed": False, "parse_error": None},
    ]

    update = execute_tool_node(state, {"configurable": {"modi_deps": deps}})

    assert seen == ["a", "b", "c"]
    assert [r["tool_call_id"] for r in update["tool_calls"]] == ["tc1", "tc2", "tc3"]
    assert _tool_message_ids(update) == ["tc1", "tc2", "tc3"]
    assert update["pending_tool_calls"] == []
    assert all("deferred:" not in m["content"] for m in update["messages"])


def test_execute_tool_node_traces_idempotency_cache_hits(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["snapshot"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    counter = {"n": 0}
    deps.tools._registry.register_tool(
        {
            "name": "snapshot",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
                "additionalProperties": False,
            },
            "risk_level": "L0",
            "side_effect": False,
            "idempotent": True,
        },
        lambda **kw: counter.update(n=counter["n"] + 1) or {"page": counter["n"]},
    )
    state = _seed_state("demo")
    state["pending_tool_calls"] = [
        {
            "tool_call_id": "tc1",
            "tool_name": "snapshot",
            "arguments": {"session_id": "s1"},
            "malformed": False,
            "parse_error": None,
        },
        {
            "tool_call_id": "tc2",
            "tool_name": "snapshot",
            "arguments": {"session_id": "s1"},
            "malformed": False,
            "parse_error": None,
        },
    ]

    update = execute_tool_node(state, {"configurable": {"modi_deps": deps}})

    assert counter["n"] == 1
    assert update["tool_calls"][0]["result"] == {"page": 1}
    assert update["tool_calls"][1]["result"] == {"page": 1}
    tool_results = [
        event["payload"]
        for event in update["pending_trace_events"]
        if event["event_type"] == "tool_result"
    ]
    assert [payload["tool_call_id"] for payload in tool_results] == ["tc1", "tc2"]
    assert [payload["step_id"] for payload in tool_results] == [
        "tool-0000-tc1",
        "tool-0000-tc2",
    ]
    assert [payload["step_type"] for payload in tool_results] == ["tool", "tool"]
    assert [payload["parent_step_id"] for payload in tool_results] == [
        "model-0000",
        "model-0000",
    ]
    assert [payload["attempt"] for payload in tool_results] == [1, 1]
    assert all(payload["elapsed_ms"] is not None for payload in tool_results)
    assert [payload["timeout"] for payload in tool_results] == [False, False]
    assert [payload["error_code"] for payload in tool_results] == [None, None]
    assert [payload["idempotency_cache_hit"] for payload in tool_results] == [False, True]
    assert tool_results[0]["result_fingerprint"] == tool_results[1]["result_fingerprint"]
    assert tool_results[0]["result_keys"] == ["page"]


def test_execute_tool_node_traces_retry_attempts(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["flaky"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    counter = {"n": 0}

    def handler(**kw):
        counter["n"] += 1
        if counter["n"] == 1:
            raise TimeoutError("slow")
        return {"ok": kw["q"], "attempt": counter["n"]}

    deps.tools._registry.register_tool(
        {
            "name": "flaky",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
                "additionalProperties": False,
            },
            "risk_level": "L0",
            "side_effect": False,
            "retry": {
                "max_attempts": 2,
                "backoff_seconds": 0,
                "retry_on": ["timeout"],
            },
        },
        handler,
    )
    state = _seed_state("demo")
    state["pending_tool_calls"] = [
        {
            "tool_call_id": "tc-retry",
            "tool_name": "flaky",
            "arguments": {"q": "x"},
            "malformed": False,
            "parse_error": None,
        }
    ]

    update = execute_tool_node(state, {"configurable": {"modi_deps": deps}})

    assert counter["n"] == 2
    tool_result = next(
        event["payload"]
        for event in update["pending_trace_events"]
        if event["event_type"] == "tool_result"
    )
    assert tool_result["attempt"] == 2
    assert [a["outcome"] for a in tool_result["attempts"]] == ["error", "success"]
    assert tool_result["attempts"][0]["error_code"] == "timeout"
    assert tool_result["error_code"] is None


def test_execute_tool_node_traces_timeout(tmp_path: Path) -> None:
    import time

    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["slow"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    deps.tools._registry.register_tool(
        {
            "name": "slow",
            "description": "",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "risk_level": "L0",
            "side_effect": False,
            "timeout_seconds": 0.05,
        },
        lambda **kw: time.sleep(0.2) or {"ok": True},
    )
    state = _seed_state("demo")
    state["pending_tool_calls"] = [
        {
            "tool_call_id": "tc-timeout",
            "tool_name": "slow",
            "arguments": {},
            "malformed": False,
            "parse_error": None,
        }
    ]

    update = execute_tool_node(state, {"configurable": {"modi_deps": deps}})

    tool_result = next(
        event["payload"]
        for event in update["pending_trace_events"]
        if event["event_type"] == "tool_result"
    )
    assert tool_result["outcome"] == "error"
    assert tool_result["timeout"] is True
    assert tool_result["error_code"] == "timeout"
    assert tool_result["attempts"][0]["timeout"] is True


def test_tool_result_can_request_user_confirmation(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["parse"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    deps.tools._registry.register_tool(
        {
            "name": "parse",
            "description": "",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "risk_level": "L0",
            "side_effect": False,
        },
        lambda: {
            "ok": True,
            "fields": {"name": "李江"},
            "_modi_pending_interaction": {
                "prompt": "确认提交",
                "input_type": "confirm",
                "field": "draft_confirmation",
                "default": "go",
            },
        },
    )
    state = _seed_state("demo")
    state["pending_tool_calls"] = [
        {
            "tool_call_id": "parse-1",
            "tool_name": "parse",
            "arguments": {},
            "malformed": False,
            "parse_error": None,
        }
    ]

    update = execute_tool_node(state, {"configurable": {"modi_deps": deps}})

    interaction = update["pending_interaction"]
    assert interaction["kind"] == "user_input"
    assert interaction["prompt"] == "确认提交"
    assert interaction["payload"]["field"] == "draft_confirmation"
    assert interaction["payload"]["default"] == "go"
    assert interaction["tool_call_id"] is None
    assert "_modi_pending_interaction" not in update["tool_calls"][0]["result"]
    assert "_modi_pending_interaction" not in update["messages"][0]["content"]
    assert any(
        event["event_type"] == "interaction_requested"
        for event in update["pending_trace_events"]
    )


def test_execute_tool_node_isolates_per_call_schema_errors(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["lookup"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    seen: list[str] = []
    deps.tools._registry.register_tool(
        {
            "name": "lookup",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
                "additionalProperties": False,
            },
            "risk_level": "L0",
            "side_effect": False,
        },
        lambda **kw: seen.append(kw["q"]) or {"q": kw["q"]},
    )
    state = _seed_state("demo")
    state["pending_tool_calls"] = [
        {"tool_call_id": "tc1", "tool_name": "lookup", "arguments": {"q": "a"}, "malformed": False, "parse_error": None},
        {"tool_call_id": "tc2", "tool_name": "lookup", "arguments": {}, "malformed": False, "parse_error": None},
        {"tool_call_id": "tc3", "tool_name": "lookup", "arguments": {"q": "c"}, "malformed": False, "parse_error": None},
    ]

    update = execute_tool_node(state, {"configurable": {"modi_deps": deps}})

    assert seen == ["a", "c"]
    assert [r["tool_call_id"] for r in update["tool_calls"]] == ["tc1", "tc2", "tc3"]
    assert update["tool_calls"][1]["error"] is not None
    tool_results = [
        event["payload"]
        for event in update["pending_trace_events"]
        if event["event_type"] == "tool_result"
    ]
    assert tool_results[1]["elapsed_ms"] is not None
    assert tool_results[1]["attempt"] == 1
    assert tool_results[1]["timeout"] is False
    assert tool_results[1]["error_code"] == "schema_validation_failed"
    assert _tool_message_ids(update) == ["tc1", "tc2", "tc3"]
    assert update["pending_tool_calls"] == []


def test_model_turn_uses_recall_cache_within_run(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import model_turn_node

    _write_agent(tmp_path / "agents", "demo")
    deps = _deps(
        tmp_path,
        _ScriptModel(script=[AIMessage(content="first"), AIMessage(content="second")]),
    )
    deps.recall_cache = RunRecallCache()
    deps.memory.write_record({
        "id": "m1",
        "scope": "user",
        "type": "user",
        "name": "pref",
        "description": "pref",
        "body": "be concise",
        "tags": [],
        "source_run_id": None,
        "expires_at": None,
        "metadata": {},
    })
    counts = {"recall": 0, "select": 0}
    original_recall = deps.memory.recall_candidates_for_context
    original_select = deps.memory.select_for_context

    def counting_recall(*args, **kwargs):
        counts["recall"] += 1
        return original_recall(*args, **kwargs)

    def counting_select(*args, **kwargs):
        counts["select"] += 1
        return original_select(*args, **kwargs)

    deps.memory.recall_candidates_for_context = counting_recall  # type: ignore[method-assign]
    deps.memory.select_for_context = counting_select  # type: ignore[method-assign]
    state = _seed_state("demo")
    deps.workspace.create_run(state["run_id"])

    model_turn_node(state, {"configurable": {"modi_deps": deps}})
    model_turn_node(state, {"configurable": {"modi_deps": deps}})

    assert counts == {"recall": 1, "select": 0}


def test_execute_tool_node_invalidates_recall_cache_on_committed_memory_write(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["save_memory"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    deps.recall_cache = RunRecallCache()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return ([], [])

    state = _seed_state("demo")
    assert deps.recall_cache.get_or_compute(state["run_id"], compute) == ([], [])
    deps.tools._registry.register_tool(
        {
            "name": "save_memory",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            "risk_level": "L0",
            "side_effect": True,
        },
        lambda **kw: {"id": kw["id"], "scope": "thread", "type": "user"},
    )
    state["pending_tool_calls"] = [
        {"tool_call_id": "tc1", "tool_name": "save_memory", "arguments": {"id": "m2"}, "malformed": False, "parse_error": None}
    ]

    execute_tool_node(state, {"configurable": {"modi_deps": deps}})
    deps.recall_cache.get_or_compute(state["run_id"], compute)

    assert calls["count"] == 2


def test_execute_tool_node_invalidates_recall_cache_on_committed_memory_proposal(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["propose_memory"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    deps.recall_cache = RunRecallCache()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return ([], [])

    state = _seed_state("demo")
    deps.recall_cache.get_or_compute(state["run_id"], compute)
    deps.tools._registry.register_tool(
        {
            "name": "propose_memory",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            "risk_level": "L0",
            "side_effect": True,
        },
        lambda **kw: {"id": kw["id"], "status": "committed"},
    )
    state["pending_tool_calls"] = [
        {"tool_call_id": "tc1", "tool_name": "propose_memory", "arguments": {"id": "m2"}, "malformed": False, "parse_error": None}
    ]

    execute_tool_node(state, {"configurable": {"modi_deps": deps}})
    deps.recall_cache.get_or_compute(state["run_id"], compute)

    assert calls["count"] == 2


def test_execute_tool_node_keeps_recall_cache_for_uncommitted_memory_proposal(tmp_path: Path) -> None:
    from modi_harness.graph.nodes import execute_tool_node

    _write_agent(tmp_path / "agents", "demo", tools=["propose_memory"])
    deps = _deps(tmp_path, _ScriptModel(script=[]))
    deps.recall_cache = RunRecallCache()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return ([], [])

    state = _seed_state("demo")
    deps.recall_cache.get_or_compute(state["run_id"], compute)
    deps.tools._registry.register_tool(
        {
            "name": "propose_memory",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            "risk_level": "L0",
            "side_effect": True,
        },
        lambda **kw: {"id": kw["id"], "status": "approval_required"},
    )
    state["pending_tool_calls"] = [
        {"tool_call_id": "tc1", "tool_name": "propose_memory", "arguments": {"id": "m2"}, "malformed": False, "parse_error": None}
    ]

    execute_tool_node(state, {"configurable": {"modi_deps": deps}})
    deps.recall_cache.get_or_compute(state["run_id"], compute)

    assert calls["count"] == 1
