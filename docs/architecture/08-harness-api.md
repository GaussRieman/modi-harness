# Harness API (V0.5 — three-object model)

> **V0.5 status:** The public API is **three** top-level objects, not one.
> `ModiHarness` is now a slim, immutable *capability suite*; `ModiAgent` is a
> first-class *agent declaration*; `ModiSession` is the *binding object* that
> combines a harness, a set of agents, and infra into something runnable and is
> the **sole execution entry point**.
>
> This file is a summary reference. For the complete contract see the design
> spec: [`docs/superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md`](../superpowers/specs/2026-06-03-v0.5-three-object-architecture-design.md).

See [`types-reference.md`](../types-reference.md) for `RunTaskResponse`,
`AgentState`, `ThreadInfo`, `StreamEvent`, `HookSpec`, `HookResult`,
`TraceEvent`, `DeniedAction`, `WorkspaceRef`, `MemoryRecord`, and the V0.5
supporting types `ToolBinding`, `Skill`, `ModelSpec`, `PermissionsConfig`,
`PluginInfo`.

## Position

modi-harness is a **harness layer** between LangGraph runtime and the
application framework. The three objects map onto three lifecycles:

| Object | Role | Lifecycle |
|---|---|---|
| `ModiHarness` | capability suite — *what governs the model* (policy, hooks, output, context, model adapter, kernel builtins) | built once at startup; immutable; shareable across sessions |
| `ModiAgent` | declaration of one governable agent (profile, scoped tools, skills, subagents, overrides) | constructed from markdown or code; immutable; self-contained |
| `ModiSession` | `harness × agents × infra` binder; owns the compiled graph and is the only executor | built per `(harness, agents, checkpointer, roots)`; rebuilt when agents/infra change |

All three are imported from `modi_harness`:

```python
from modi_harness import ModiHarness, ModiAgent, ModiSession, ToolBinding
```

The internal graph adapter is `HarnessGraphAdapter`
(`src/modi_harness/graph/harness_adapter.py`, renamed from the old
`RuntimeAdapter`; the `runtime/` directory was removed). It is internal — not
exported, and owned by `ModiSession`.

## `ModiHarness` — capability suite

```python
ModiHarness(
    chat_model,                  # BaseChatModel — required, injected
    *,
    rule_packs=None,             # list[str]
    permissions=None,            # PermissionsConfig
    hook_specs=None,             # list[HookSpec] — declarations only, no execution
    builtin_tools=None,          # None=all builtins, []=none, [names]=whitelist
    kernel_tools=None,           # list[ToolBinding] — extra kernel-scoped tools
)
```

Holds (immutable after construction): `.chat_model`, `.policy` (`PolicyGate`),
`.permissions`, `.hook_registry` (declarations, no dispatcher), `.context`,
`.output`, `.model`, `.model_cache`, `.builtin_tools_registry`,
`.builtin_tool_names`. It does **not** hold any agent, skill, infra
(checkpointer/workspace/memory), dispatcher, or compiled graph.

`builtin_tools` is a *whitelist filter*; `kernel_tools` *adds* new kernel-scoped
tools. Different roles, deliberately different names.

## `ModiAgent` — agent declaration

```python
ModiAgent(
    name, description, instruction,
    *,
    tools=(),                # tuple[ToolBinding, ...] — agent-scoped
    skills=(),               # tuple[Skill, ...]
    subagents=(),            # tuple[ModiAgent, ...] — recursive
    output_contract=None,
    permission_profile=None,
    safety_constraints=(),
    model_override=None,     # ModelSpec
    metadata={},             # read-only mapping
)

ModiAgent.from_markdown(path, *, tools=None, skills=None, subagents=None) -> ModiAgent
ModiAgent.load_dir(directory) -> list[ModiAgent]
```

Immutable (`@dataclass(frozen=True)`; lists stored as tuples, `metadata` as
`MappingProxyType`), value-equal, not subclassable, and has **no `run`
method** — execution lives only on `ModiSession`. Tools attached here are
visible only to this agent (and to its declared subagents). Constructors accept
the legacy `(spec, handler)` tuple form and normalize via
`ToolBinding.from_tuple`.

## `ModiSession` — binder & executor

```python
ModiSession(
    harness,                 # ModiHarness — held by reference
    *,
    agents,                  # list[ModiAgent] — top-level (runnable) agents
    checkpointer,            # BaseCheckpointSaver — injected
    workspace_root,          # Path | str
    memory_root,             # Path | str
    project_root=None,       # for hook dispatcher
    hook_pass_env=None,
    max_steps=20,
    repair_budget=3,
)

ModiSession.from_discovery(
    harness, *,
    checkpointer, workspace_root, memory_root,
    plugins=None,            # list[PluginInfo]; None → discover_plugins()
    agents_dir=None,         # convenience: ModiAgent.load_dir(agents_dir)
    extra_agents=None,
    project_root=None, hook_pass_env=None, max_steps=20, repair_budget=3,
) -> ModiSession
```

Session owns the `WorkspaceManager`, `MemoryStore`, `HookDispatcher`,
`TraceMiddleware`, merged `ToolGateway` (harness builtins + each agent's scoped
tools), the `HarnessGraphAdapter`, and the compiled LangGraph graph (built once,
immutable). It walks `subagents` recursively and registers them; nested
subagents get a generated `delegate_to_<name>` tool, top-level agents do not.
Non-equal name collisions raise `AgentNameConflict`; equal agents dedupe.

### Execution (all keyword-only)

> **Deviation from spec §3.3:** the implemented execution methods are
> fully keyword-only — `agent=` and `input=` are not positional. The
> permission-mode argument is `mode=`, not `permission_mode=`.

```text
run_task(*, agent, input, options=None, mode=None, thread_id=None) -> RunTaskResponse
resume_task(*, thread_id, payload=None) -> RunTaskResponse
approve_action(*, thread_id, approval_id, decision="approved") -> RunTaskResponse
reject_action(*, thread_id, approval_id, reason) -> RunTaskResponse
stream(*, agent, input, options=None, mode=None, thread_id=None)  -> Iterable[StreamEvent]
astream(*, agent, input, options=None, mode=None, thread_id=None) -> AsyncIterator[StreamEvent]
```

`run_task` is a thin wrapper over the stream; it returns the terminal
`RunTaskResponse`. Stream event types: `model_delta`, `tool_call_proposal`,
`tool_call_result`, `approval_request`, `terminal`.

### Introspection (keyed by `thread_id`)

```text
get_state(thread_id)     -> AgentState | None
get_artifacts(thread_id) -> list[WorkspaceRef]
get_trace(thread_id)     -> Iterable[TraceEvent]
get_denials(thread_id)   -> list[DeniedAction]
```

### Memory / hooks / threads / agents / cleanup

```text
add_memory(record) / list_memory(...) / forget_memory(record_id)
list_hooks(thread_id=None) / get_hook_results(thread_id, event_id)
list_threads() -> list[ThreadInfo] / end_thread(thread_id)
get_agent(name) -> ModiAgent
list_agents()     -> list[str]   # top-level (runnable) only
list_all_agents() -> list[str]   # includes nested subagents
close()                           # release dispatcher subprocesses, trace handles
```

`run_task(agent=...)` only accepts a top-level name; subagent-only names are
delegation targets and raise `AgentNotRegistered` when used as an entry point.

## Embedded usage

```python
from modi_harness import ModiHarness, ModiAgent, ModiSession, ToolBinding
from langgraph.checkpoint.memory import MemorySaver

# 1) Capability suite — knows nothing about specific agents.
harness = ModiHarness(chat_model=my_chat_model, rule_packs=["default"])

# 2) Agent declarations — markdown- or code-constructed, equivalent.
research = ModiAgent.from_markdown(
    "./agents/research-assistant.md",
    tools=[ToolBinding(spec=FETCH_URL_SPEC, handler=fetch_url)],
)

# 3) Session — binds harness, agents, and infra into something runnable.
session = ModiSession(
    harness=harness,
    agents=[research],
    checkpointer=MemorySaver(),
    workspace_root=".modi/workspace",
    memory_root="~/.modi/memory",
)

# 4) Execute — the sole entry point.
response = session.run_task(
    agent="research-assistant",
    input={"goal": "...", "messages": [...]},
)
```

## Errors

| Exception | Defined in | Raised when |
|---|---|---|
| `AgentFrontmatterError` / `AgentDuplicateError` / `AgentNotFoundError` | `agents/errors.py` | `ModiAgent.from_markdown` / `load_dir` |
| `AgentNameConflict` | `api/errors.py` | two non-equal agents share a `name` after merge |
| `AgentNotRegistered` | `api/errors.py` | `run_task(agent=...)` for unknown / subagent-only name |
| `ModiSessionConfigError` | `api/errors.py` | infra construction failure (no agents, bad roots, …) |

## Boundaries

- Capabilities (policy, hooks-declaration, output, context, model): `ModiHarness`.
- Agent declarations: `ModiAgent`.
- Execution, infra binding, graph, workspace, memory, trace, hook dispatch:
  `ModiSession` (delegating to `HarnessGraphAdapter` and the governance modules).
- `thread_id` is the primary persistence/introspection key; `run_id` is internal.
