# Hook System

Hook System lets users inject shell or Python commands at well-defined points in the harness lifecycle. Hooks observe, augment, block, or redirect execution without modifying agent or skill files.

Hooks are how a deployment customizes a generic harness for a specific environment, policy, or workflow.

## Events

V0.1 supports these event types:

- `session_start`: before the first model step of a run.
- `user_prompt_submit`: after Harness API receives user input, before context build.
- `pre_tool_use`: after Policy Gate decides allow, before tool execution.
- `post_tool_use`: after tool execution, before result enters state.
- `pre_model_call`: after context build, before model call.
- `post_model_call`: after model returns, before routing.
- `on_approval_request`: when Policy Gate raises require_approval.
- `on_denial`: when Policy Gate denies an action.
- `on_output_validate`: after Output Controller decides status.
- `on_run_end`: after run reaches a terminal status.
- `on_error`: when any module raises a recoverable runtime error.

Each event carries a typed payload defined alongside the event.

## Hook Spec

```python
class HookSpec(TypedDict):
    event: str
    matcher: dict | None       # filter by tool name, agent, skill, risk_level, etc.
    command: str               # shell command, or "python:module.fn"
    timeout_seconds: int
    blocking: bool
    pass_payload: Literal["env", "stdin", "argv"]
    capture: Literal["stdout", "stderr", "none"]
    on_failure: Literal["block", "warn", "ignore"]
```

## Hook Result

```python
class HookResult(TypedDict):
    event: str
    hook_id: str
    decision: Literal["proceed", "block", "redirect"]
    feedback: str | None
    redirect: dict | None
    exit_code: int
    duration_ms: int
    stdout_ref: str | None
    stderr_ref: str | None
```

## Feedback Semantics

Hook feedback is treated as **user feedback**. It can:

- `proceed`: do nothing.
- `block`: stop the current step; surface `feedback` to the model on the next step, just like a denied tool call.
- `redirect`: replace the current step's input or output with `redirect` payload. Only `user_prompt_submit`, `pre_model_call`, and `post_model_call` accept redirect.

A blocking hook on `pre_tool_use` is equivalent to a user-initiated deny: the tool does not execute and the same call is not retried unchanged.

## Configuration

Hooks live in settings, not in agent files:

```text
<project_root>/.modi/settings.json
<user_root>/.modi/settings.json
```

Resolution merges user → project, with project overriding by `event` + `matcher`.

User instructions in CLAUDE.md / memory describing automated behavior should be translated into hooks; agent instructions cannot enforce automation by themselves.

## Rules

- Hooks run inside Runtime Adapter, around the dispatch boundary for each event.
- Hooks never run inside Policy Gate's decision; they react to the decision.
- A blocking failure with `on_failure: block` stops the step and is recorded as a hook denial.
- Hooks cannot grant permission; they can only restrict, block, redirect, or annotate.
- Hook execution is sandboxed by timeout. Misbehaving hooks are killed.
- Hook output is untrusted by default; redirect payloads must come from a trusted hook source (user-owned settings).
- All hook executions are traced with payload references, not payload inlines.

## Boundaries

- Hook System owns event dispatch and result interpretation.
- Policy Gate remains the authority on allow/deny/approval for tool execution.
- Hooks cannot replace agent instructions, skill instructions, or memory writes.
- Hooks cannot bypass Tool Gateway or Output Controller.
